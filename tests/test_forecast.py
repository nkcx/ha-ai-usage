"""Tests for usage projections, with synthetic usage patterns."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ai_usage.core.forecast import (
    HOURS_PER_WEEK,
    RETENTION_HOURS,
    Confidence,
    ForecastSettings,
    History,
    project,
    replay,
    typical_profile,
)

# A Monday, 00:00 UTC.
MONDAY = datetime(2026, 9, 7, tzinfo=UTC)
STEP = timedelta(minutes=5)


def workday_rate(moment: datetime) -> float:
    """% of the weekly limit per hour: 0.5 on weekdays 9:00-17:00 UTC, else 0."""
    return 0.5 if moment.weekday() < 5 and 9 <= moment.hour < 17 else 0.0


def weekly_samples(
    start: datetime, end: datetime, rate: object = workday_rate
) -> Iterator[tuple[datetime, float, datetime]]:
    """Yield samples every 5 minutes of a weekly window resetting Mondays 00:00."""
    value = 0.0
    moment = start
    while moment < end:
        week_start = moment - timedelta(
            days=moment.weekday(), hours=moment.hour, minutes=moment.minute
        )
        reset = week_start + timedelta(weeks=1)
        if moment == week_start:
            value = 0.0
        yield moment, round(value, 3), reset
        value += rate(moment) * STEP.total_seconds() / 3600  # type: ignore[operator]
        moment += STEP


def test_increments_and_resets() -> None:
    history = History()
    reset = MONDAY + timedelta(hours=5)
    history.add_sample(MONDAY, 10, reset)
    history.add_sample(MONDAY + timedelta(minutes=30), 13, reset)
    hour = int(MONDAY.timestamp() // 3600)
    assert history.increments == {hour: pytest.approx(3)}

    # Spread over two hours.
    history.add_sample(MONDAY + timedelta(minutes=90), 15, reset)
    assert history.increments[hour] == pytest.approx(4)
    assert history.increments[hour + 1] == pytest.approx(1)

    # The window reset: consumption since the reset is the new value.
    later_reset = reset + timedelta(hours=5)
    history.add_sample(MONDAY + timedelta(minutes=120), 2, later_reset)
    assert history.increments[hour + 1] == pytest.approx(3)

    # A drop without a moved reset time is also a reset.
    history.add_sample(MONDAY + timedelta(minutes=150), 1, later_reset)
    assert history.increments[hour + 2] == pytest.approx(1)


def test_gaps_and_order() -> None:
    history = History()
    history.add_sample(MONDAY, 10, None)
    # A gap of more than 2 hours isn't attributed to any hour...
    history.add_sample(MONDAY + timedelta(hours=3), 20, None)
    assert history.increments == {}
    assert history.observed == {}
    # ...but becomes the new reference point.
    history.add_sample(MONDAY + timedelta(hours=3, minutes=10), 21, None)
    assert sum(history.increments.values()) == pytest.approx(1)
    # Out-of-order samples change nothing.
    history.add_sample(MONDAY + timedelta(hours=1), 50, None)
    assert history.last_value == 21
    # Never negative.
    history.add_sample(MONDAY + timedelta(hours=3, minutes=20), 20.8, None)
    assert min(history.increments.values()) >= 0


def test_round_trip_and_retention() -> None:
    history = replay(weekly_samples(MONDAY, MONDAY + timedelta(weeks=10)))
    hours = sorted(history.observed)
    assert hours[-1] - hours[0] <= RETENTION_HOURS
    restored = History.from_dict(history.to_dict())
    assert restored.observed.keys() == history.observed.keys()
    assert restored.last_value == history.last_value
    for hour, amount in history.increments.items():
        assert restored.increments[hour] == pytest.approx(amount, abs=1e-4)
    assert History.from_dict({"increments": {}, "observed": {}}).last_time is None


def test_profile_learns_the_week() -> None:
    history = replay(weekly_samples(MONDAY, MONDAY + timedelta(weeks=3)))
    now = MONDAY + timedelta(weeks=3)
    profile = typical_profile(history, now)
    assert len(profile) == HOURS_PER_WEEK
    tuesday_10 = 24 + 10
    saturday_10 = 5 * 24 + 10
    tuesday_03 = 24 + 3
    assert profile[tuesday_10] == pytest.approx(0.5, abs=0.15)
    assert profile[tuesday_03] == pytest.approx(0, abs=0.01)
    # Weekends are quiet, but the pooled prior pulls them up a little.
    assert profile[saturday_10] < profile[tuesday_10] / 2


def test_profile_unobserved_hours_are_unknown_not_zero() -> None:
    """Hours the app wasn't running don't teach the profile "no usage"."""
    samples = [
        sample
        for sample in weekly_samples(MONDAY, MONDAY + timedelta(weeks=2))
        # The app was down on Wednesday the first week.
        if not (sample[0].weekday() == 2 and sample[0] < MONDAY + timedelta(weeks=1))
    ]
    history = replay(samples)
    profile = typical_profile(history, MONDAY + timedelta(weeks=2))
    wednesday_10 = 2 * 24 + 10
    assert profile[wednesday_10] == pytest.approx(0.5, abs=0.15)


def test_projection_low_confidence_uses_current_rate() -> None:
    """With little history: the current burst, fading out."""
    start = MONDAY + timedelta(hours=9)
    history = replay(
        weekly_samples(start, start + timedelta(hours=2), rate=lambda _: 10.0)
    )
    now = start + timedelta(hours=2)
    projection = project(history, 20, MONDAY + timedelta(weeks=1), now)
    assert projection.confidence is Confidence.LOW
    assert projection.current_rate == pytest.approx(10, abs=0.5)
    # 20 % now, plus a decaying 10 %/h (half-life 1.5 h, ~21.6 % more).
    assert projection.at_reset == pytest.approx(41.6, abs=2)
    assert projection.runs_out is None


def test_projection_runs_out() -> None:
    start = MONDAY + timedelta(hours=9)
    history = replay(
        weekly_samples(start, start + timedelta(hours=2), rate=lambda _: 40.0)
    )
    now = start + timedelta(hours=2)
    projection = project(history, 80, MONDAY + timedelta(weeks=1), now)
    assert projection.runs_out is not None
    assert now < projection.runs_out < now + timedelta(hours=1)
    assert projection.at_reset > 100


def test_projection_already_out() -> None:
    projection = project(History(), 100, MONDAY + timedelta(days=1), MONDAY)
    assert projection.runs_out == MONDAY
    assert projection.at_reset == 100


def test_projection_backtest_typical_week() -> None:
    """After three typical weeks, Monday morning's projection is about right."""
    history = replay(weekly_samples(MONDAY, MONDAY + timedelta(weeks=3, hours=10)))
    now = MONDAY + timedelta(weeks=3, hours=10)
    value = history.last_value
    assert value is not None
    projection = project(history, value, MONDAY + timedelta(weeks=4), now)
    assert projection.confidence is Confidence.HIGH
    # The week's actual total is 5 days x 8 h x 0.5 % = 20 %.
    assert projection.at_reset == pytest.approx(20, abs=2)
    assert projection.runs_out is None
    assert projection.typical_rate == pytest.approx(0.5, abs=0.15)


def test_projection_heavy_day_above_typical() -> None:
    """A burst well above the usual rate raises the projection, temporarily."""
    heavy = MONDAY + timedelta(weeks=2, hours=10)

    def rate(moment: datetime) -> float:
        return (
            5.0
            if heavy <= moment < heavy + timedelta(hours=1)
            else workday_rate(moment)
        )

    history = replay(weekly_samples(MONDAY, heavy + timedelta(hours=1), rate=rate))
    now = heavy + timedelta(hours=1)
    value = history.last_value
    assert value is not None
    projection = project(history, value, MONDAY + timedelta(weeks=3), now)
    assert projection.confidence is Confidence.MEDIUM
    assert projection.current_rate > projection.typical_rate * 5
    usual = project(
        history,
        value,
        MONDAY + timedelta(weeks=3),
        now,
        ForecastSettings(burst_half_life_hours=1e-6),
    )
    assert projection.at_reset > usual.at_reset


def test_unchanged_value_is_known_idle() -> None:
    """The recorder only stores changes: an unchanged value means no use."""
    history = History()
    history.add_sample(MONDAY, 40, None)
    history.add_sample(MONDAY + timedelta(hours=8), 40, None)
    assert sum(history.observed.values()) == pytest.approx(8 * 3600)
    assert sum(history.increments.values()) == 0
    # Beyond a day it's no longer trusted.
    history.add_sample(MONDAY + timedelta(hours=40), 40, None)
    assert sum(history.observed.values()) == pytest.approx(8 * 3600)


def test_gap_marker_breaks_the_chain() -> None:
    """An unknown stretch (sensor unavailable) isn't counted as idle or used."""
    history = History()
    history.add_sample(MONDAY, 10, None)
    history.add_sample(MONDAY + timedelta(minutes=30), None, None)
    history.add_sample(MONDAY + timedelta(minutes=90), 30, None)
    assert history.increments == {}
    assert history.observed == {}
    history.add_sample(MONDAY + timedelta(minutes=100), 31, None)
    assert sum(history.increments.values()) == pytest.approx(1)
