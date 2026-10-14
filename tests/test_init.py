"""Tests for setup, polling, repairs and device handling."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.ai_usage import async_remove_config_entry_device
from custom_components.ai_usage.const import DOMAIN, SCAN_INTERVAL

from .fake_app import INSTANCE_ID, FakeApp


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Poll the app once, as the coordinator does every SCAN_INTERVAL."""
    freezer.tick(SCAN_INTERVAL)
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_setup_without_entries(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})


async def test_setup_and_unload(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED
    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, INSTANCE_ID), setup_entry.entry_id
    )
    assert hub is not None
    assert hub.sw_version == "0.1.0"
    assert (
        hub.configuration_url == "homeassistant://hassio/addon/425b2f89_ai_usage/info"
    )
    claude = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{INSTANCE_ID}_claude"), setup_entry.entry_id
    )
    assert claude is not None
    assert claude.via_device_id == hub.id
    assert claude.manufacturer == "Anthropic"

    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_manual_entry_without_slug(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    data = dict(config_entry.data)
    del data["app_slug"]
    entry = MockConfigEntry(domain=DOMAIN, unique_id=INSTANCE_ID, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, INSTANCE_ID), entry.entry_id
    )
    assert hub is not None
    assert hub.configuration_url is None


@pytest.mark.parametrize(
    ("status", "state"),
    [(500, ConfigEntryState.SETUP_RETRY), (401, ConfigEntryState.SETUP_ERROR)],
)
async def test_setup_failures(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_app: FakeApp,
    status: int,
    state: ConfigEntryState,
) -> None:
    fake_app.status = status
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is state


async def test_app_unreachable_then_back(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    freezer: FrozenDateTimeFactory,
) -> None:
    entity_id = "sensor.claude_session"
    assert hass.states.get(entity_id).state == "27.0"
    fake_app.status = 502
    await poll(hass, freezer)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    fake_app.status = None
    await poll(hass, freezer)
    assert hass.states.get(entity_id).state == "27.0"


async def test_token_rejected_starts_reauth(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    freezer: FrozenDateTimeFactory,
) -> None:
    fake_app.token = "rotated"
    await poll(hass, freezer)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_repairs(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    signed_out = issue_registry.async_get_issue(
        DOMAIN, f"signed_out_{INSTANCE_ID}_codex"
    )
    assert signed_out is not None
    assert signed_out.translation_placeholders == {
        "provider": "Codex",
        "cli": "Codex CLI",
        "error": "",
    }
    assert signed_out.learn_more_url == "/hassio/ingress/425b2f89_ai_usage"

    fake_app.provider("gemini")["install_error"] = "HTTP 404"
    fake_app.provider("codex")["enabled"] = False
    await poll(hass, freezer)
    assert (
        issue_registry.async_get_issue(DOMAIN, f"signed_out_{INSTANCE_ID}_codex")
        is None
    )
    install = issue_registry.async_get_issue(
        DOMAIN, f"install_failed_{INSTANCE_ID}_gemini"
    )
    assert install is not None
    assert install.translation_placeholders["error"] == "HTTP 404"


async def test_provider_turned_off_and_on(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    identifier = (DOMAIN, f"{INSTANCE_ID}_gemini")
    assert (
        device_registry.async_get_device_by_identifier(identifier, setup_entry.entry_id)
        is not None
    )

    fake_app.provider("gemini")["enabled"] = False
    fake_app.provider("gemini")["report"] = None
    await poll(hass, freezer)
    assert (
        device_registry.async_get_device_by_identifier(identifier, setup_entry.entry_id)
        is None
    )
    assert entity_registry.async_get("sensor.gemini_gemini_models_weekly") is None

    fake_app.snapshot["providers"]["gemini"] = FakeApp().provider("gemini")
    await poll(hass, freezer)
    assert (
        device_registry.async_get_device_by_identifier(identifier, setup_entry.entry_id)
        is not None
    )
    assert hass.states.get("sensor.gemini_gemini_models_weekly").state == "2.1"


async def test_remove_device(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    fake_app: FakeApp,
    device_registry: dr.DeviceRegistry,
    hass_ws_client: WebSocketGenerator,
    freezer: FrozenDateTimeFactory,
) -> None:
    assert await async_setup_component(hass, "config", {})
    client = await hass_ws_client(hass)

    async def remove(identifier: str) -> bool:
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, identifier), setup_entry.entry_id
        )
        assert device is not None
        await client.send_json_auto_id(
            {
                "type": "config/device_registry/remove_config_entry",
                "config_entry_id": setup_entry.entry_id,
                "device_id": device.id,
            }
        )
        response = await client.receive_json()
        return bool(response["success"])

    # Devices of enabled providers and the app itself stay.
    assert not await remove(f"{INSTANCE_ID}_claude")
    assert not await remove(INSTANCE_ID)

    # Once the app stops reporting a provider, its device can go.
    del fake_app.snapshot["providers"]["claude"]
    await poll(hass, freezer)
    device_registry.async_get_or_create(
        config_entry_id=setup_entry.entry_id,
        identifiers={(DOMAIN, f"{INSTANCE_ID}_claude")},
    )
    assert await remove(f"{INSTANCE_ID}_claude")


async def test_remove_foreign_device(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    device = device_registry.async_get_or_create(
        config_entry_id=setup_entry.entry_id, identifiers={("other", "x")}
    )
    assert not await async_remove_config_entry_device(hass, setup_entry, device)
