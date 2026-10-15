"""Tests for projections from the recorder's statistics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ai_usage.const import DOMAIN, SCAN_INTERVAL
from custom_components.ai_usage.projections import fetch_states, fetch_statistics

from .fake_app import COLLECTED, FakeApp

STATES = "custom_components.ai_usage.projections.fetch_states"
STATISTICS = "custom_components.ai_usage.projections.fetch_statistics"


async def poll(hass: HomeAssistant) -> None:
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def rows(
    start: datetime, step: timedelta, values: Iterable[float]
) -> list[tuple[float, float]]:
    """Statistics rows (period end, max) for consecutive periods from ``start``."""
    return [
        ((start + step * (i + 1)).timestamp(), value) for i, value in enumerate(values)
    ]


@pytest.fixture
def frozen(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    freezer.move_to(COLLECTED)
    return freezer


async def test_without_recorder(
    hass: HomeAssistant, frozen: FrozenDateTimeFactory, setup_entry: MockConfigEntry
) -> None:
    """No recorder: only the latest value is known, nothing more is projected."""
    projected = hass.states.get("sensor.claude_session_projected_at_reset")
    assert projected.state == "27.0"
    assert projected.attributes["confidence"] == "low"
    assert projected.attributes["current_rate"] == 0
    assert hass.states.get("sensor.claude_session_runs_out").state == STATE_UNKNOWN
    assert (
        hass.states.get("binary_sensor.claude_session_projected_to_run_out").state
        == STATE_OFF
    )
    # Only windows with a reset time get projections.
    assert hass.states.get("sensor.gemini_ai_credits_projected_at_reset") is None


async def test_projection_from_history(
    hass: HomeAssistant,
    frozen: FrozenDateTimeFactory,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
) -> None:
    """A heavy last hour in the recorded states: projected to run out."""
    calls: list[tuple[str, str]] = []
    now = COLLECTED + SCAN_INTERVAL

    async def fake_states(
        hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, float | None]]:
        calls.append((entity_id, "states"))
        if entity_id != "sensor.claude_session":
            return []
        # Reported every 5 minutes, 5 % more each time, for the last hour.
        return [
            (now - timedelta(minutes=60 - 5 * i), float(5 + 5 * i)) for i in range(12)
        ]

    async def fake_statistics(
        hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
    ) -> list[tuple[float, float]]:
        calls.append((entity_id, "hour"))
        return rows(start, timedelta(hours=1), [0] * 10)

    hass.config.components.add("recorder")
    fake_app.set_meter("claude", "session", value=62)
    fake_app.provider("claude")["report"]["collected_at"] = now.isoformat()
    frozen.tick(SCAN_INTERVAL)
    with (
        patch(STATES, side_effect=fake_states),
        patch(STATISTICS, side_effect=fake_statistics),
    ):
        await poll(hass)

    projected = hass.states.get("sensor.claude_session_projected_at_reset")
    assert float(projected.state) > 100
    assert projected.attributes["current_rate"] == pytest.approx(60, abs=5)
    assert hass.states.get("sensor.claude_session_runs_out").state != STATE_UNKNOWN
    assert (
        hass.states.get("binary_sensor.claude_session_projected_to_run_out").state
        == STATE_ON
    )
    assert ("sensor.claude_session", "hour") in calls
    assert ("sensor.claude_session", "states") in calls

    # No new report: projections are kept, the recorder isn't read again.
    calls.clear()
    with (
        patch(STATES, side_effect=fake_states),
        patch(STATISTICS, side_effect=fake_statistics),
    ):
        await poll(hass)
    assert calls == []

    # A new report in the same hour reads only the recent states again.
    fake_app.provider("claude")["report"]["collected_at"] = (
        now + SCAN_INTERVAL
    ).isoformat()
    frozen.tick(SCAN_INTERVAL)
    with (
        patch(STATES, side_effect=fake_states),
        patch(STATISTICS, side_effect=fake_statistics),
    ):
        await poll(hass)
    assert ("sensor.claude_session", "hour") not in calls
    assert ("sensor.claude_session", "states") in calls


async def test_provider_turned_off(
    hass: HomeAssistant,
    frozen: FrozenDateTimeFactory,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
) -> None:
    fake_app.provider("claude")["enabled"] = False
    await poll(hass)
    assert ("claude", "session") not in setup_entry.runtime_data.projections
    assert ("gemini", "gemini_models_weekly") in setup_entry.runtime_data.projections


class FakeRecorder:
    """Records the recorder query and returns a canned result."""

    def __init__(self, result: dict[str, list[dict[str, Any]]]) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        self.calls.append((func.__name__, *args[1:]))
        return self.result


async def test_fetch_statistics(hass: HomeAssistant) -> None:
    """Query hourly maxima; rows without one are skipped."""
    recorder = FakeRecorder(
        {"sensor.x": [{"end": 1.0, "max": 5.0}, {"end": 2.0, "max": None}]}
    )
    start = COLLECTED - timedelta(hours=2)
    with patch("homeassistant.helpers.recorder.get_instance", return_value=recorder):
        assert list(await fetch_statistics(hass, "sensor.x", start, COLLECTED)) == [
            (1.0, 5.0)
        ]
        assert list(await fetch_statistics(hass, "sensor.y", start, COLLECTED)) == []
    assert recorder.calls[0] == (
        "statistics_during_period",
        start,
        COLLECTED,
        {"sensor.x"},
        "hour",
        None,
        {"max"},
    )


async def test_fetch_states(hass: HomeAssistant) -> None:
    """Query recorded states; unknown and unavailable become gaps."""
    states = [
        State("sensor.x", "12.5", last_changed=COLLECTED),
        State("sensor.x", "unavailable", last_changed=COLLECTED + timedelta(minutes=5)),
        State("sensor.x", "unknown", last_changed=COLLECTED + timedelta(minutes=10)),
        State("sensor.x", "garbage", last_changed=COLLECTED + timedelta(minutes=15)),
    ]
    recorder = FakeRecorder({"sensor.x": states})
    start = COLLECTED - timedelta(hours=1)
    with patch("homeassistant.helpers.recorder.get_instance", return_value=recorder):
        result = list(await fetch_states(hass, "sensor.x", start, COLLECTED))
    assert result == [
        (COLLECTED, 12.5),
        (COLLECTED + timedelta(minutes=5), None),
        (COLLECTED + timedelta(minutes=10), None),
        (COLLECTED + timedelta(minutes=15), None),
    ]
    assert recorder.calls[0] == (
        "state_changes_during_period",
        start,
        COLLECTED,
        "sensor.x",
        True,
    )
