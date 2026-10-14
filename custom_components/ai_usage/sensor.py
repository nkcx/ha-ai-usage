"""Sensors for AI Usage: one per meter, its reset time, and provider status."""

from __future__ import annotations

from datetime import datetime
from functools import partial
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ProviderState
from .coordinator import AiUsageConfigEntry, AiUsageCoordinator
from .core.model import Meter, MeterKind, Status
from .entity import (
    EntityFactories,
    MeterEntity,
    ProjectionEntity,
    ProviderEntity,
    track_entities,
    window_meters,
)

PARALLEL_UPDATES = 0

_CURRENCY = re.compile(r"^[A-Z]{3}$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AiUsageConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors, and more as providers report new meters."""
    coordinator = entry.runtime_data

    def wanted(provider: str, state: ProviderState) -> EntityFactories:
        factories: EntityFactories = {
            f"{provider}/status": partial(StatusSensor, coordinator, provider),
            f"{provider}/collected": partial(CollectedSensor, coordinator, provider),
        }
        for meter in state.report.meters if state.report else ():
            factories[f"{provider}/{meter.key}"] = partial(
                MeterSensor, coordinator, provider, meter
            )
            if meter.resets_at is not None:
                factories[f"{provider}/{meter.key}/resets"] = partial(
                    ResetSensor, coordinator, provider, meter
                )
        for meter in window_meters(state):
            factories[f"{provider}/{meter.key}/projected"] = partial(
                ProjectedSensor, coordinator, provider, meter
            )
            factories[f"{provider}/{meter.key}/runs_out"] = partial(
                RunsOutSensor, coordinator, provider, meter
            )
        return factories

    track_entities(entry, async_add_entities, wanted)


class MeterSensor(MeterEntity, SensorEntity):
    """The value of a meter: % of a window used, credits left, money spent."""

    def __init__(
        self, coordinator: AiUsageCoordinator, provider: str, meter: Meter
    ) -> None:
        """Show ``meter``."""
        super().__init__(coordinator, provider, meter)
        self._attr_translation_key = meter.kind.value
        if meter.kind is MeterKind.WINDOW_USED:
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 0
        elif _CURRENCY.match(meter.unit):
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = meter.unit
            self._attr_suggested_display_precision = 2
        else:
            self._attr_native_unit_of_measurement = meter.unit
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """The meter's value."""
        meter = self.meter
        return None if meter is None else meter.value


class ResetSensor(MeterEntity, SensorEntity):
    """When a meter's window resets."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "resets"

    def __init__(
        self, coordinator: AiUsageCoordinator, provider: str, meter: Meter
    ) -> None:
        """Show when ``meter`` resets."""
        super().__init__(coordinator, provider, meter, "_resets")

    @property
    def native_value(self) -> datetime | None:
        """The reset time."""
        meter = self.meter
        return None if meter is None else meter.resets_at


class StatusSensor(ProviderEntity, SensorEntity):
    """Whether the provider's CLI is installed, signed in and reporting."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "status"
    _attr_options = [status.value for status in Status] + ["installing"]

    def __init__(self, coordinator: AiUsageCoordinator, provider: str) -> None:
        """Show the provider's status."""
        super().__init__(coordinator, provider, "status")

    @property
    def native_value(self) -> str | None:
        """The status of the last collection."""
        state = self.state_info
        if state is None:
            return None
        if state.install_error:
            return Status.ERROR.value
        if state.report is None:
            return "installing"
        return state.report.status.value


class CollectedSensor(ProviderEntity, SensorEntity):
    """When the app last read the provider's usage."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "collected"

    def __init__(self, coordinator: AiUsageCoordinator, provider: str) -> None:
        """Show when usage was last read."""
        super().__init__(coordinator, provider, "collected")

    @property
    def native_value(self) -> datetime | None:
        """The time of the last collection."""
        report = self.report
        return None if report is None else report.collected_at


class ProjectedSensor(ProjectionEntity, SensorEntity):
    """Projected % of a window used when it resets (may exceed 100)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_translation_key = "projected"

    def __init__(
        self, coordinator: AiUsageCoordinator, provider: str, meter: Meter
    ) -> None:
        """Project ``meter``."""
        super().__init__(coordinator, provider, meter, "_projected")

    @property
    def native_value(self) -> float | None:
        """The projected % at reset."""
        projection = self.projection
        return None if projection is None else projection.at_reset

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """How the projection was made."""
        projection = self.projection
        if projection is None:
            return None
        return {
            "confidence": projection.confidence.value,
            "current_rate": projection.current_rate,
            "typical_rate": projection.typical_rate,
        }


class RunsOutSensor(ProjectionEntity, SensorEntity):
    """When a window is projected to reach 100 % (unknown if not before reset)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "runs_out"

    def __init__(
        self, coordinator: AiUsageCoordinator, provider: str, meter: Meter
    ) -> None:
        """Project when ``meter`` runs out."""
        super().__init__(coordinator, provider, meter, "_runs_out")

    @property
    def native_value(self) -> datetime | None:
        """The projected time of reaching 100 %."""
        projection = self.projection
        return None if projection is None else projection.runs_out
