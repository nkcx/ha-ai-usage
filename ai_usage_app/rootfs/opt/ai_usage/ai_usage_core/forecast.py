"""Projecting when a usage window runs out.

Usage isn't linear: it comes in bursts (work sessions), with quiet nights and
weekends. Dividing "% used" by "time elapsed" says little about the rest of a
window. Instead:

1. **Increments, not levels.** Each pair of samples of a window meter gives the
   % consumed between them. A reset (the window's reset time moves forward, or
   the value drops) restarts from the new value.
2. **Typical profile.** Consumption per **hour of the week** (168 buckets),
   averaged over past weeks with exponentially decaying weights so recent
   habits count most. Thin buckets are shrunk toward the same hour of the day
   across all days. Hours with no samples (app down) are unknown, not zero.
3. **Current intensity.** The recent rate (last hour) minus what the profile
   expected for that hour: "heavier than usual right now".
4. **Projection.** Expected rate for each future hour = profile + the current
   deviation, decaying back to zero (bursts end). Integrated up to the reset
   time, this gives the projected % at reset and when it would hit 100 %.

Pure Python: the integration feeds it samples and stores its ``History``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import math
from typing import Any

HOUR = 3600
HOURS_PER_WEEK = 168

#: History kept, in hours.
RETENTION_HOURS = 8 * HOURS_PER_WEEK
#: A gap longer than this between samples isn't attributed to any hour.
MAX_GAP = 2 * HOUR
#: An unchanged value over a longer stretch still means nothing was consumed.
MAX_IDLE = 24 * HOUR
#: A reset time moving forward by more than this means the window reset.
RESET_TOLERANCE = 300


@dataclass(frozen=True, kw_only=True)
class ForecastSettings:
    """Tunables, with defaults chosen before any real data; to be backtested."""

    profile_half_life_weeks: float = 2.0
    """Older weeks count less: weight halves every this many weeks."""

    shrink_weeks: float = 2.0
    """Weeks of evidence an hour-of-week bucket needs to outweigh its pooled
    hour-of-day average."""

    burst_half_life_hours: float = 1.5
    """The current deviation from the profile halves every this many hours."""

    recent_hours: float = 1.0
    """Window for the current rate."""

    min_history_hours: float = 7 * 24
    """Below this much observed history, confidence is low."""


class Confidence(StrEnum):
    """How much the projection can be trusted."""

    LOW = "low"
    """Less than a week of history: mostly the current rate."""

    MEDIUM = "medium"
    """At least a week."""

    HIGH = "high"
    """At least three weeks."""


@dataclass
class History:
    """Hourly consumption of one window meter, plus the last sample."""

    increments: dict[int, float] = field(default_factory=dict)
    """Hour (Unix time // 3600) → % consumed in that hour."""

    observed: dict[int, float] = field(default_factory=dict)
    """Hour → seconds of it covered by samples (consumption known there)."""

    last_time: float | None = None
    last_value: float | None = None
    last_reset: float | None = None

    def add_sample(
        self, when: datetime, value: float | None, resets_at: datetime | None
    ) -> None:
        """Record a sample of the meter; ``None`` marks a gap (value unknown).

        Samples are reported values at the times they were reported. A value
        that stayed the same is known to have consumed nothing, even over a
        long stretch (the recorder only stores changes); a gap breaks the
        chain, so the time around it stays unknown.
        """
        now = when.timestamp()
        if value is None:
            self.last_time = self.last_value = self.last_reset = None
            return
        reset = None if resets_at is None else resets_at.timestamp()
        if self.last_time is not None and self.last_value is not None:
            gap = now - self.last_time
            unchanged = value == self.last_value and gap <= MAX_IDLE
            if 0 < gap <= MAX_GAP or (gap > 0 and unchanged):
                reset_happened = (
                    reset is not None
                    and self.last_reset is not None
                    and reset - self.last_reset > RESET_TOLERANCE
                ) or value < self.last_value - 0.5
                consumed = (
                    value if reset_happened else max(0.0, value - self.last_value)
                )
                self._spread(self.last_time, now, consumed)
        if self.last_time is None or now > self.last_time:
            self.last_time, self.last_value, self.last_reset = now, value, reset
        self._prune(now)

    def _spread(self, start: float, end: float, consumed: float) -> None:
        """Attribute consumption evenly over the hours between two samples."""
        span = end - start
        hour = int(start // HOUR)
        while hour * HOUR < end:
            overlap = min(end, (hour + 1) * HOUR) - max(start, hour * HOUR)
            if overlap > 0:
                self.observed[hour] = min(HOUR, self.observed.get(hour, 0.0) + overlap)
                self.increments[hour] = (
                    self.increments.get(hour, 0.0) + consumed * overlap / span
                )
            hour += 1

    def _prune(self, now: float) -> None:
        oldest = int(now // HOUR) - RETENTION_HOURS
        self.observed = {
            hour: seconds for hour, seconds in self.observed.items() if hour >= oldest
        }
        self.increments = {
            hour: amount for hour, amount in self.increments.items() if hour >= oldest
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form (for storage)."""
        return {
            "increments": {
                str(hour): round(amount, 4) for hour, amount in self.increments.items()
            },
            "observed": {str(hour): round(sec) for hour, sec in self.observed.items()},
            "last": [self.last_time, self.last_value, self.last_reset],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> History:
        """Restore from ``to_dict`` output."""
        last_time, last_value, last_reset = data.get("last") or (None, None, None)
        return cls(
            increments={
                int(hour): float(amount) for hour, amount in data["increments"].items()
            },
            observed={int(hour): float(sec) for hour, sec in data["observed"].items()},
            last_time=last_time,
            last_value=last_value,
            last_reset=last_reset,
        )


@dataclass(frozen=True, kw_only=True)
class Projection:
    """Where a window is heading."""

    at_reset: float
    """Projected % used when the window resets (may exceed 100)."""

    runs_out: datetime | None
    """When it's projected to reach 100 %, if before the reset."""

    confidence: Confidence
    current_rate: float
    """% per hour over the recent window."""

    typical_rate: float
    """% per hour the profile expected for the current hour."""


def _hourly_rate(history: History, hour: int) -> float:
    """% per hour during the observed part of ``hour``."""
    covered = history.observed.get(hour, 0.0)
    return history.increments.get(hour, 0.0) * HOUR / covered if covered else 0.0


def _hour_of_week(hour: int) -> int:
    # Hour 0 (1970-01-01 00:00 UTC) was a Thursday; offset so 0 = Monday 00:00 UTC.
    return (hour + 72) % HOURS_PER_WEEK


def typical_profile(
    history: History, now: datetime, settings: ForecastSettings | None = None
) -> list[float]:
    """Return expected % per hour for each hour of the week (Monday 00:00 UTC first)."""
    settings = settings or ForecastSettings()
    current = int(now.timestamp() // HOUR)
    weighted = [0.0] * HOURS_PER_WEEK
    weights = [0.0] * HOURS_PER_WEEK
    for hour, covered in history.observed.items():
        if hour >= current:
            continue  # the current hour is still incomplete
        age_weeks = (current - hour) / HOURS_PER_WEEK
        # Partly observed hours count for the part that was observed.
        weight = 0.5 ** (age_weeks / settings.profile_half_life_weeks) * covered / HOUR
        slot = _hour_of_week(hour)
        weighted[slot] += weight * _hourly_rate(history, hour)
        weights[slot] += weight

    # Pool each hour of the day across the 7 days, as a prior for thin buckets.
    pooled = []
    for hour_of_day in range(24):
        slots = range(hour_of_day, HOURS_PER_WEEK, 24)
        total_weight = sum(weights[slot] for slot in slots)
        pooled.append(
            sum(weighted[slot] for slot in slots) / total_weight
            if total_weight
            else 0.0
        )

    profile = []
    for slot in range(HOURS_PER_WEEK):
        prior = pooled[slot % 24]
        # Shrinkage: a bucket with little evidence leans on the pooled value.
        evidence, prior_weight = weights[slot], settings.shrink_weeks
        own = weighted[slot] / evidence if evidence else prior
        profile.append(
            (evidence * own + prior_weight * prior) / (evidence + prior_weight)
        )
    return profile


def project(
    history: History,
    value: float,
    resets_at: datetime,
    now: datetime,
    settings: ForecastSettings | None = None,
) -> Projection:
    """Project a window meter from ``value`` now to ``resets_at``."""
    settings = settings or ForecastSettings()
    profile = typical_profile(history, now, settings)
    current_hour = int(now.timestamp() // HOUR)

    # Measure the recent rate up to the last sample: nothing is known after it.
    recent_end = min(now.timestamp(), history.last_time or now.timestamp())
    recent_start = recent_end - settings.recent_hours * HOUR
    # Average over the observed part of the window only: unobserved time is
    # unknown, not idle.
    consumed = observed = 0.0
    for hour, covered in history.observed.items():
        overlap = min(covered / HOUR, _overlap(hour, recent_start, recent_end))
        consumed += _hourly_rate(history, hour) * overlap
        observed += overlap
    current_rate = consumed / observed if observed else 0.0
    typical_rate = profile[_hour_of_week(current_hour)]
    deviation = current_rate - typical_rate

    observed_hours = sum(history.observed.values()) / HOUR
    if observed_hours < settings.min_history_hours:
        confidence = Confidence.LOW
        # Not enough history for a profile: carry the current rate, decaying.
        profile = [0.0] * HOURS_PER_WEEK
        deviation = current_rate
    elif observed_hours < 3 * HOURS_PER_WEEK:
        confidence = Confidence.MEDIUM
    else:
        confidence = Confidence.HIGH

    total = value
    runs_out = None if value < 100 else now
    step_start = now
    while step_start < resets_at:
        step_end = min(resets_at, _next_hour(step_start))
        hours = (step_end - step_start).total_seconds() / HOUR
        elapsed = ((step_start - now).total_seconds() + hours * HOUR / 2) / HOUR
        decay = 0.5 ** (elapsed / settings.burst_half_life_hours)
        hour = int(step_start.timestamp() // HOUR)
        rate = max(0.0, profile[_hour_of_week(hour)] + deviation * decay)
        added = rate * hours
        if runs_out is None and total + added >= 100 and rate > 0:
            runs_out = step_start + timedelta(hours=(100 - total) / rate)
        total += added
        step_start = step_end

    return Projection(
        at_reset=round(total, 1),
        runs_out=runs_out,
        confidence=confidence,
        current_rate=round(current_rate, 3),
        typical_rate=round(typical_rate, 3),
    )


def _overlap(hour: int, start: float, end: float) -> float:
    """Fraction of ``hour`` that lies within [start, end]."""
    lo, hi = max(hour * HOUR, start), min((hour + 1) * HOUR, end)
    return max(0.0, hi - lo) / HOUR


def _next_hour(moment: datetime) -> datetime:
    floor = math.floor(moment.timestamp() / HOUR) * HOUR
    return datetime.fromtimestamp(floor + HOUR, UTC)


def replay(
    samples: Iterable[tuple[datetime, float | None, datetime | None]],
) -> History:
    """Build a history from (time, value, resets_at) samples (for backtests)."""
    history = History()
    for when, value, resets_at in samples:
        history.add_sample(when, value, resets_at)
    return history
