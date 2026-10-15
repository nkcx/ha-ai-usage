"""Tests for projections from the recorder's statistics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ai_usage.const import DOMAIN, SCAN_INTERVAL
from custom_components.ai_usage.projections import fetch_statistics

from .fake_app import COLLECTED, FakeApp

FETCH = "custom_components.ai_usage.projections.fetch_statistics"


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


async def test_projection_from_statistics(
    hass: HomeAssistant,
    frozen: FrozenDateTimeFactory,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
) -> None:
    """A heavy last hour in the 5-minute statistics: projected to run out."""
    calls: list[tuple[str, str]] = []

    async def fake_fetch(
        hass: HomeAssistant, entity_id: str, start: datetime, end: datetime, period: str
    ) -> list[tuple[float, float]]:
        calls.append((entity_id, period))
        if entity_id != "sensor.claude_session":
            return []
        if period == "hour":
            return rows(start, timedelta(hours=1), [0] * 10)
        # 5 % every 5 minutes for the last hour.
        return rows(end - timedelta(hours=1), timedelta(minutes=5), range(5, 61, 5))

    hass.config.components.add("recorder")
    fake_app.set_meter("claude", "session", value=62)
    fake_app.provider("claude")["report"]["collected_at"] = (
        COLLECTED + SCAN_INTERVAL
    ).isoformat()
    frozen.tick(SCAN_INTERVAL)
    with patch(FETCH, side_effect=fake_fetch):
        await poll(hass)

    projected = hass.states.get("sensor.claude_session_projected_at_reset")
    assert float(projected.state) > 100
    assert projected.attributes["current_rate"] == pytest.approx(60, abs=3)
    assert hass.states.get("sensor.claude_session_runs_out").state != STATE_UNKNOWN
    assert (
        hass.states.get("binary_sensor.claude_session_projected_to_run_out").state
        == STATE_ON
    )
    assert ("sensor.claude_session", "hour") in calls
    assert ("sensor.claude_session", "5minute") in calls

    # No new report: projections are kept, statistics aren't read again.
    calls.clear()
    with patch(FETCH, side_effect=fake_fetch):
        await poll(hass)
    assert calls == []
    assert (
        float(hass.states.get("sensor.claude_session_projected_at_reset").state) > 100
    )

    # A new report in the same hour reads only the recent statistics again.
    fake_app.provider("claude")["report"]["collected_at"] = (
        COLLECTED + 2 * SCAN_INTERVAL
    ).isoformat()
    frozen.tick(SCAN_INTERVAL)
    with patch(FETCH, side_effect=fake_fetch):
        await poll(hass)
    assert ("sensor.claude_session", "hour") not in calls
    assert ("sensor.claude_session", "5minute") in calls


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
    """Runs the statistics query function and returns a canned result."""

    def __init__(self, result: dict[str, list[dict[str, Any]]]) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        self.calls.append((func.__name__, *args[1:]))
        return self.result


async def test_fetch_statistics(hass: HomeAssistant) -> None:
    """Query the recorder for maxima; rows without one are skipped."""
    recorder = FakeRecorder(
        {"sensor.x": [{"end": 1.0, "max": 5.0}, {"end": 2.0, "max": None}]}
    )
    start = COLLECTED - timedelta(hours=2)
    with patch("homeassistant.helpers.recorder.get_instance", return_value=recorder):
        result = await fetch_statistics(hass, "sensor.x", start, COLLECTED, "hour")
        assert list(result) == [(1.0, 5.0)]
        empty = await fetch_statistics(hass, "sensor.y", start, COLLECTED, "5minute")
        assert list(empty) == []
    assert recorder.calls[0] == (
        "statistics_during_period",
        start,
        COLLECTED,
        {"sensor.x"},
        "hour",
        None,
        {"max"},
    )
