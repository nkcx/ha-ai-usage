"""Sensors for AI Usage: one per meter, its reset time, and provider status."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import re

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AiUsageConfigEntry, AiUsageCoordinator
from .core.model import Meter, MeterKind, Status
from .entity import MeterEntity, ProviderEntity

PARALLEL_UPDATES = 0

_CURRENCY = re.compile(r"^[A-Z]{3}$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AiUsageConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors, and more as providers report new meters."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        wanted: dict[str, Callable[[], Entity]] = {}
        for provider, state in coordinator.data.providers.items():
            if not state.enabled:
                continue
            wanted[f"{provider}/status"] = _factory(StatusSensor, coordinator, provider)
            wanted[f"{provider}/collected"] = _factory(
                CollectedSensor, coordinator, provider
            )
            for meter in state.report.meters if state.report else ():
                wanted[f"{provider}/{meter.key}"] = _meter_factory(
                    MeterSensor, coordinator, provider, meter
                )
                if meter.resets_at is not None:
                    wanted[f"{provider}/{meter.key}/resets"] = _meter_factory(
                        ResetSensor, coordinator, provider, meter
                    )
        # Forget entities of providers turned off, so they come back if
        # turned on again (their device and entities were removed).
        known.intersection_update(wanted)
        new = [create() for key, create in wanted.items() if key not in known]
        known.update(wanted)
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


def _factory(
    cls: Callable[[AiUsageCoordinator, str], Entity],
    coordinator: AiUsageCoordinator,
    provider: str,
) -> Callable[[], Entity]:
    return lambda: cls(coordinator, provider)


def _meter_factory(
    cls: Callable[[AiUsageCoordinator, str, Meter], Entity],
    coordinator: AiUsageCoordinator,
    provider: str,
    meter: Meter,
) -> Callable[[], Entity]:
    return lambda: cls(coordinator, provider, meter)


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
