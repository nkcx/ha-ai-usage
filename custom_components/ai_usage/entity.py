"""Base entities for AI Usage."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ProviderState
from .const import DOMAIN, PROVIDERS
from .coordinator import AiUsageConfigEntry, AiUsageCoordinator
from .core.forecast import Projection
from .core.model import Meter, MeterKind, UsageReport

type EntityFactories = dict[str, Callable[[], Entity]]
"""Entity key → how to create it."""


def track_entities(
    entry: AiUsageConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    wanted: Callable[[str, ProviderState], EntityFactories],
) -> None:
    """Add a platform's entities, and new ones as the app reports them.

    ``wanted`` returns the entities an enabled provider should have. Entities
    of providers turned off are forgotten (the coordinator removes their
    device), so they're added again if the provider is turned back on.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        factories: EntityFactories = {}
        for provider, state in coordinator.data.providers.items():
            if state.enabled:
                factories.update(wanted(provider, state))
        known.intersection_update(factories)
        new = [create() for key, create in factories.items() if key not in known]
        known.update(factories)
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


def window_meters(state: ProviderState) -> list[Meter]:
    """Return the provider's meters that have a usage window to project."""
    return [
        meter
        for meter in (state.report.meters if state.report else ())
        if meter.kind is MeterKind.WINDOW_USED and meter.resets_at is not None
    ]


class AiUsageEntity(CoordinatorEntity[AiUsageCoordinator]):
    """An entity on the app's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AiUsageCoordinator, key: str) -> None:
        """Identify the entity by ``key`` within the app."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.instance_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.instance_id)}
        )


class ProviderEntity(CoordinatorEntity[AiUsageCoordinator]):
    """An entity on a provider's device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AiUsageCoordinator, provider: str, key: str
    ) -> None:
        """Identify the entity by ``key`` within ``provider``."""
        super().__init__(coordinator)
        self.provider = provider
        info = PROVIDERS[provider]
        self._attr_unique_id = f"{coordinator.instance_id}_{provider}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.instance_id}_{provider}")},
            name=info.name,
            manufacturer=info.manufacturer,
            model=info.cli,
            entry_type=DeviceEntryType.SERVICE,
            via_device_id=coordinator.hub_device_id,
        )

    @property
    def state_info(self) -> ProviderState | None:
        """The provider's state in the app, if it's enabled there."""
        state = self.coordinator.data.providers.get(self.provider)
        return state if state is not None and state.enabled else None

    @property
    def report(self) -> UsageReport | None:
        """The provider's latest report."""
        state = self.state_info
        return state.report if state else None

    @property
    def available(self) -> bool:
        """Available while the provider is enabled in the app."""
        return super().available and self.state_info is not None


class MeterEntity(ProviderEntity):
    """An entity showing one meter of a provider."""

    def __init__(
        self,
        coordinator: AiUsageCoordinator,
        provider: str,
        meter: Meter,
        suffix: str = "",
    ) -> None:
        """Track ``meter`` (looked up by key in each new report)."""
        super().__init__(coordinator, provider, f"{meter.key}{suffix}")
        self.meter_key = meter.key
        self._attr_translation_placeholders = {"meter": meter.name}

    @property
    def meter(self) -> Meter | None:
        """The meter in the latest report, if reported."""
        report = self.report
        if report is None:
            return None
        return next((m for m in report.meters if m.key == self.meter_key), None)

    @property
    def available(self) -> bool:
        """Available while the provider reports this meter."""
        return super().available and self.meter is not None


class ProjectionEntity(MeterEntity):
    """An entity showing where a window meter is heading."""

    @property
    def projection(self) -> Projection | None:
        """The meter's latest projection."""
        return self.coordinator.projections.get((self.provider, self.meter_key))

    @property
    def available(self) -> bool:
        """Available while there's a projection."""
        return super().available and self.projection is not None
