"""Usage history for projections, kept in Home Assistant's storage.

Independent of the recorder: projections need weeks of history whatever the
recorder keeps, and only a compact hourly summary per window meter.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import AppSnapshot
from .const import DOMAIN, LOGGER
from .core.forecast import History, Projection, project
from .core.model import MeterKind

STORAGE_VERSION = 1
SAVE_DELAY = 60

type MeterId = tuple[str, str]
"""(provider, meter key)."""


class UsageHistory:
    """Hourly consumption per window meter, persisted per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Store the history of config entry ``entry_id``."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.history"
        )
        self.meters: dict[MeterId, History] = {}

    async def async_load(self) -> None:
        """Load the saved history."""
        data = await self._store.async_load() or {}
        for key, saved in data.get("meters", {}).items():
            provider, _, meter = key.partition("/")
            try:
                self.meters[(provider, meter)] = History.from_dict(saved)
            except KeyError, TypeError, ValueError, AttributeError:
                LOGGER.warning("Discarding unreadable usage history for %s", key)

    def update(self, snapshot: AppSnapshot) -> dict[MeterId, Projection]:
        """Record the snapshot's window meters; return their projections."""
        projections: dict[MeterId, Projection] = {}
        for provider, state in snapshot.providers.items():
            report = state.report
            if not state.enabled or report is None:
                continue
            for meter in report.meters:
                if (
                    meter.kind is not MeterKind.WINDOW_USED
                    or meter.value is None
                    or meter.resets_at is None
                ):
                    continue
                history = self.meters.setdefault((provider, meter.key), History())
                history.add_sample(report.collected_at, meter.value, meter.resets_at)
                projections[(provider, meter.key)] = project(
                    history, meter.value, meter.resets_at, report.collected_at
                )
        self._store.async_delay_save(self._data, SAVE_DELAY)
        return projections

    def _data(self) -> dict[str, Any]:
        return {
            "meters": {
                f"{provider}/{meter}": history.to_dict()
                for (provider, meter), history in self.meters.items()
            }
        }

    async def async_remove(self) -> None:
        """Delete the saved history (the entry was removed)."""
        await self._store.async_remove()
