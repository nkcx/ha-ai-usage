"""The AI Usage integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import AppClient
from .const import CONF_APP_SLUG, DOMAIN
from .coordinator import AiUsageConfigEntry, AiUsageCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the AI Usage integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AiUsageConfigEntry) -> bool:
    """Connect to the AI Usage app."""
    client = AppClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_API_TOKEN],
    )
    coordinator = AiUsageCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    slug = entry.data.get(CONF_APP_SLUG)
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.instance_id)},
        name="AI Usage app",
        manufacturer="AI Usage",
        model="AI Usage app",
        sw_version=coordinator.data.app_version,
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=f"homeassistant://config/app/{slug}/info" if slug else None,
    )
    coordinator.hub_device_id = hub.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AiUsageConfigEntry) -> bool:
    """Disconnect from the app."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AiUsageConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a provider's device once it's turned off in the app."""
    coordinator = entry.runtime_data
    for domain, identifier in device.identifiers:
        if domain != DOMAIN or identifier == coordinator.instance_id:
            continue
        provider = identifier.removeprefix(f"{coordinator.instance_id}_")
        state = coordinator.data.providers.get(provider)
        return state is None or not state.enabled
    return False
