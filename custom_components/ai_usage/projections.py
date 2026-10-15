"""Projections from the recorder's statistics.

The usage sensors have a state class, so the recorder already keeps their
history as statistics: hourly long-term statistics (kept indefinitely,
whatever ``purge_keep_days`` says) and 5-minute short-term statistics (kept
for the purge period). Projections read those instead of keeping a second
copy:

* hourly maxima over the past weeks → the typical-week profile,
* 5-minute maxima over the last hours → the current pace,
* the latest reported value.

Hours without statistics (Home Assistant down, sensor excluded) are unknown
to the forecast, not idle.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api import AppSnapshot
from .const import DOMAIN, LOGGER
from .core.forecast import RETENTION_HOURS, Projection, project, replay
from .core.model import Meter, MeterKind

type MeterId = tuple[str, str]
"""(provider, meter key)."""

type Sample = tuple[datetime, float, datetime | None]

#: Recent stretch read from 5-minute statistics (finer than hourly).
RECENT = timedelta(hours=2)
HISTORY = timedelta(hours=RETENTION_HOURS)


class Projections:
    """Projects each usage window from its sensor's recorded statistics."""

    def __init__(self, hass: HomeAssistant, instance_id: str) -> None:
        """Project the windows of the app installation ``instance_id``."""
        self.hass = hass
        self.instance_id = instance_id
        self.projections: dict[MeterId, Projection] = {}
        self._collected: dict[str, datetime] = {}
        self._hourly: dict[str, tuple[datetime, list[Sample]]] = {}

    async def async_update(self, snapshot: AppSnapshot) -> dict[MeterId, Projection]:
        """Recompute the projections of providers with a new report."""
        current: dict[MeterId, Projection] = {}
        for provider, state in snapshot.providers.items():
            report = state.report
            if not state.enabled or report is None:
                continue
            fresh = self._collected.get(provider) != report.collected_at
            for meter in report.meters:
                if not _projectable(meter):
                    continue
                key = (provider, meter.key)
                if fresh or key not in self.projections:
                    current[key] = await self._project(
                        provider, meter, report.collected_at
                    )
                else:
                    current[key] = self.projections[key]
            self._collected[provider] = report.collected_at
        self.projections = current
        return current

    async def _project(self, provider: str, meter: Meter, now: datetime) -> Projection:
        assert meter.value is not None
        assert meter.resets_at is not None
        samples: list[Sample] = []
        entity_id = er.async_get(self.hass).async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{self.instance_id}_{provider}_{meter.key}"
        )
        if entity_id is not None and "recorder" in self.hass.config.components:
            recent_start = now - RECENT
            hourly = await self._hourly_samples(entity_id, now)
            samples = [sample for sample in hourly if sample[0] <= recent_start]
            samples += await self._samples(entity_id, recent_start, now, "5minute")
        samples.append((now, meter.value, meter.resets_at))
        return project(
            replay(sorted(samples, key=lambda s: s[0])),
            meter.value,
            meter.resets_at,
            now,
        )

    async def _hourly_samples(self, entity_id: str, now: datetime) -> list[Sample]:
        """Hourly maxima for the history window; refetched once per hour."""
        hour = now.replace(minute=0, second=0, microsecond=0)
        cached = self._hourly.get(entity_id)
        if cached is None or cached[0] != hour:
            samples = await self._samples(entity_id, hour - HISTORY, hour, "hour")
            self._hourly[entity_id] = cached = (hour, samples)
        return cached[1]

    async def _samples(
        self,
        entity_id: str,
        start: datetime,
        end: datetime,
        period: Literal["5minute", "hour"],
    ) -> list[Sample]:
        """Each statistics period's maximum, as a sample at the period's end."""
        try:
            rows = await fetch_statistics(self.hass, entity_id, start, end, period)
        except (KeyError, RuntimeError, ValueError) as err:  # pragma: no cover
            LOGGER.debug("No statistics for %s: %s", entity_id, err)
            return []
        return [
            (datetime.fromtimestamp(row_end, start.tzinfo), maximum, None)
            for row_end, maximum in rows
        ]


def _projectable(meter: Meter) -> bool:
    return (
        meter.kind is MeterKind.WINDOW_USED
        and meter.value is not None
        and meter.resets_at is not None
    )


async def fetch_statistics(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
    period: Literal["5minute", "hour"],
) -> Iterable[tuple[float, float]]:
    """Return (period end timestamp, maximum) rows from the recorder."""
    # Imported here: the recorder is an optional (after-)dependency.
    from homeassistant.helpers.recorder import get_instance  # noqa: PLC0415
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )

    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, period, None, {"max"}
    )
    return [
        (row["end"], maximum)
        for row in result.get(entity_id, [])
        if (maximum := row.get("max")) is not None
    ]
