"""Binary sensors for AI Usage: whether a window is projected to run out."""

from __future__ import annotations

from functools import partial

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ProviderState
from .coordinator import AiUsageConfigEntry, AiUsageCoordinator
from .core.model import Meter
from .entity import EntityFactories, ProjectionEntity, track_entities, window_meters

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AiUsageConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a "projected to run out" sensor per usage window."""
    coordinator = entry.runtime_data

    def wanted(provider: str, state: ProviderState) -> EntityFactories:
        return {
            f"{provider}/{meter.key}/runs_out": partial(
                RunsOutBinarySensor, coordinator, provider, meter
            )
            for meter in window_meters(state)
        }

    track_entities(entry, async_add_entities, wanted)


class RunsOutBinarySensor(ProjectionEntity, BinarySensorEntity):
    """On when a window is projected to reach 100 % before it resets."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "runs_out_projected"

    def __init__(
        self, coordinator: AiUsageCoordinator, provider: str, meter: Meter
    ) -> None:
        """Watch ``meter``'s projection."""
        super().__init__(coordinator, provider, meter, "_runs_out_projected")

    @property
    def is_on(self) -> bool | None:
        """Whether the window is projected to run out before it resets."""
        projection = self.projection
        return None if projection is None else projection.runs_out is not None
