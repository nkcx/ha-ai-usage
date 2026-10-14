"""Tests for the usage history store and projection entities."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ai_usage.const import DOMAIN, SCAN_INTERVAL
from custom_components.ai_usage.history import SAVE_DELAY

from .fake_app import COLLECTED, FakeApp


async def poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, fake_app: FakeApp
) -> None:
    """Advance time, as if the app collected again, and poll it."""
    freezer.tick(SCAN_INTERVAL)
    for state in fake_app.snapshot["providers"].values():
        if state["report"]:
            state["report"]["collected_at"] = _now(freezer)
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def _now(freezer: FrozenDateTimeFactory) -> str:
    return freezer.time_to_freeze.replace(tzinfo=COLLECTED.tzinfo).isoformat()


@pytest.fixture
def frozen(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Start the clock where the fake app's reports were collected."""
    freezer.move_to(COLLECTED)
    return freezer


async def test_projection_entities(
    hass: HomeAssistant, frozen: FrozenDateTimeFactory, setup_entry: MockConfigEntry
) -> None:
    projected = hass.states.get("sensor.claude_session_projected_at_reset")
    # One sample: no history yet, so no growth is projected.
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


async def test_projection_runs_out(
    hass: HomeAssistant,
    frozen: FrozenDateTimeFactory,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
) -> None:
    """A heavy session: 27 % → 87 % in 30 minutes is projected to run out."""
    for step in range(1, 7):
        fake_app.set_meter("claude", "session", value=27 + 10 * step)
        await poll(hass, frozen, fake_app)
    projected = hass.states.get("sensor.claude_session_projected_at_reset")
    assert float(projected.state) > 100
    assert projected.attributes["current_rate"] > 100
    runs_out = hass.states.get("sensor.claude_session_runs_out")
    assert runs_out.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert (
        hass.states.get("binary_sensor.claude_session_projected_to_run_out").state
        == STATE_ON
    )


async def test_history_saved_and_restored(
    hass: HomeAssistant,
    frozen: FrozenDateTimeFactory,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    hass_storage: dict[str, Any],
) -> None:
    for step in range(1, 4):
        fake_app.set_meter("claude", "session", value=27 + step)
        await poll(hass, frozen, fake_app)
    frozen.tick(timedelta(seconds=SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    key = f"{DOMAIN}.{setup_entry.entry_id}.history"
    saved = hass_storage[key]["data"]["meters"]
    assert set(saved) >= {"claude/session", "claude/weekly", "gemini/gemini_models_5h"}
    assert saved["claude/session"]["last"][1] == 30

    # Reloading restores it.
    assert await hass.config_entries.async_reload(setup_entry.entry_id)
    await hass.async_block_till_done()
    history = setup_entry.runtime_data.history.meters[("claude", "session")]
    assert sum(history.increments.values()) == pytest.approx(3)

    # Removing the entry deletes it.
    assert await hass.config_entries.async_remove(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_history_unreadable(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}.history"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}.history",
        "data": {"meters": {"claude/session": {"increments": "nope"}}},
    }
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert "Discarding unreadable usage history for claude/session" in caplog.text
