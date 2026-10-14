"""Tests for the sensor and button platforms."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ai_usage.const import DOMAIN, REFRESH_DELAY, SCAN_INTERVAL

from .fake_app import FakeApp


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Poll the app once, as the coordinator does every SCAN_INTERVAL."""
    freezer.tick(SCAN_INTERVAL)
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize("platform", [Platform.SENSOR, Platform.BUTTON])
async def test_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    platform: Platform,
) -> None:
    """Every entity, its registry entry and its state."""
    config_entry.add_to_hass(hass)
    with patch("custom_components.ai_usage.PLATFORMS", [platform]):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


async def test_meter_values(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert hass.states.get("sensor.claude_session").state == "27.0"
    assert hass.states.get("sensor.claude_week").state == "46.0"
    assert hass.states.get("sensor.claude_week_fable").state == "0.0"
    assert (
        hass.states.get("sensor.claude_session_resets").state
        == "2026-10-15T01:30:00+00:00"
    )
    assert hass.states.get("sensor.claude_status").state == "ok"
    assert hass.states.get("sensor.gemini_ai_credits").state == "0.0"
    assert hass.states.get("sensor.codex_status").state == "signed_out"
    # Disabled by default.
    assert hass.states.get("sensor.claude_last_checked") is None


async def test_signed_out_makes_meters_unavailable(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    freezer: FrozenDateTimeFactory,
) -> None:
    fake_app.provider("claude")["report"] = {
        **fake_app.provider("claude")["report"],
        "status": "signed_out",
        "meters": [],
        "account": None,
    }
    await poll(hass, freezer)
    assert hass.states.get("sensor.claude_session").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.claude_session_resets").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.claude_status").state == "signed_out"


async def test_status_while_installing(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    freezer: FrozenDateTimeFactory,
) -> None:
    fake_app.provider("codex")["report"] = None
    await poll(hass, freezer)
    assert hass.states.get("sensor.codex_status").state == "installing"
    fake_app.provider("codex")["install_error"] = "404"
    await poll(hass, freezer)
    assert hass.states.get("sensor.codex_status").state == "error"


async def test_new_meters_appear(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A newly reported meter (e.g. a new model's limit) gets entities."""
    report = fake_app.provider("claude")["report"]
    report["meters"].append(
        {
            **report["meters"][2],
            "key": "weekly_opus",
            "name": "Week (Opus)",
            "model": "Opus",
            "value": 12,
        }
    )
    report["meters"].append(
        {
            "key": "extra_usage",
            "name": "Extra usage",
            "kind": "spend",
            "value": 4.5,
            "unit": "USD",
            "limit": 100,
            "resets_at": None,
            "window_seconds": None,
            "group": None,
            "model": None,
            "active": None,
        }
    )
    await poll(hass, freezer)
    assert hass.states.get("sensor.claude_week_opus").state == "12.0"
    assert hass.states.get("sensor.claude_week_opus_resets") is not None
    extra = hass.states.get("sensor.claude_extra_usage")
    assert extra.state == "4.5"
    assert extra.attributes["unit_of_measurement"] == "USD"
    assert hass.states.get("sensor.claude_extra_usage_resets") is None

    # A meter that disappears goes unavailable.
    report["meters"] = [m for m in report["meters"] if m["key"] != "weekly_opus"]
    await poll(hass, freezer)
    assert hass.states.get("sensor.claude_week_opus").state == STATE_UNAVAILABLE


async def test_refresh_button(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    freezer: FrozenDateTimeFactory,
) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.ai_usage_app_refresh_usage"},
        blocking=True,
    )
    assert fake_app.refreshes == 1

    fake_app.set_meter("claude", "session", value=55)
    freezer.tick(timedelta(seconds=REFRESH_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.claude_session").state == "55.0"


async def test_refresh_button_error(
    hass: HomeAssistant, setup_entry: MockConfigEntry, fake_app: FakeApp
) -> None:
    fake_app.status = 500
    with pytest.raises(HomeAssistantError, match="couldn't start a refresh"):
        await hass.services.async_call(
            "button",
            "press",
            {ATTR_ENTITY_ID: "button.ai_usage_app_refresh_usage"},
            blocking=True,
        )


async def test_refresh_fetch_cancelled_on_unload(
    hass: HomeAssistant, setup_entry: MockConfigEntry, fake_app: FakeApp
) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.ai_usage_app_refresh_usage"},
        blocking=True,
    )
    with patch(
        "custom_components.ai_usage.coordinator.AiUsageCoordinator.async_request_refresh"
    ) as refresh:
        assert await hass.config_entries.async_unload(setup_entry.entry_id)
        await hass.async_block_till_done()
    refresh.assert_not_called()
