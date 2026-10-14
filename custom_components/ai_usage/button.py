"""Button to read usage again now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import AppError
from .const import DOMAIN, REFRESH_DELAY
from .coordinator import AiUsageConfigEntry, AiUsageCoordinator
from .entity import AiUsageEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AiUsageConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the refresh button to the app's device."""
    async_add_entities([RefreshButton(entry.runtime_data)])


class RefreshButton(AiUsageEntity, ButtonEntity):
    """Asks the app to read every provider's usage now."""

    _attr_translation_key = "refresh"

    def __init__(self, coordinator: AiUsageCoordinator) -> None:
        """Refresh through ``coordinator``."""
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        """Ask the app to collect, then fetch the results shortly after."""
        try:
            await self.coordinator.client.refresh()
        except AppError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        async def fetch(_: object) -> None:
            await self.coordinator.async_request_refresh()

        self.async_on_remove(async_call_later(self.hass, REFRESH_DELAY, fetch))
