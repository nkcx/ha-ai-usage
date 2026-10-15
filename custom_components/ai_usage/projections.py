"""Projections from the recorder's history of each usage sensor.

The recorder already keeps the usage sensors' history, so projections read it
instead of keeping a copy:

* **recent** (the last day): the recorded states, i.e. each reported value at
  the time it was reported. Precise timing matters here: it sets the current
  pace.
* **older** (up to 8 weeks): the hourly long-term statistics (kept
  indefinitely, whatever ``purge_keep_days`` says). An hour's maximum is
  plenty for the typical-week profile.
* the latest reported value.

A value that stayed the same is known to have used nothing (the recorder only
stores changes); an ``unavailable`` stretch or hours without statistics stay
unknown, not idle.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api import AppSnapshot
from .const import DOMAIN, LOGGER
from .core.forecast import RETENTION_HOURS, Projection, project, replay
from .core.model import Meter, MeterKind

type MeterId = tuple[str, str]
"""(provider, meter key)."""

type Sample = tuple[datetime, float | None, datetime | None]
"""(time, value or None for "unknown from here", resets_at)."""

#: Recent stretch read from the recorded states (exact report times).
RECENT = timedelta(days=1)
HISTORY = timedelta(hours=RETENTION_HOURS)


class Projections:
    """Projects each usage window from its sensor's recorded history."""

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
            samples += await self._state_samples(entity_id, recent_start, now)
        samples.append((now, meter.value, meter.resets_at))
        samples.sort(key=lambda sample: sample[0])
        return project(replay(samples), meter.value, meter.resets_at, now)

    async def _hourly_samples(self, entity_id: str, now: datetime) -> list[Sample]:
        """Hourly maxima over the history window; refetched once per hour."""
        hour = now.replace(minute=0, second=0, microsecond=0)
        cached = self._hourly.get(entity_id)
        if cached is None or cached[0] != hour:
            try:
                rows = await fetch_statistics(
                    self.hass, entity_id, hour - HISTORY, hour
                )
            except (KeyError, RuntimeError, ValueError) as err:  # pragma: no cover
                LOGGER.debug("No statistics for %s: %s", entity_id, err)
                rows = []
            samples: list[Sample] = [
                (datetime.fromtimestamp(end, now.tzinfo), maximum, None)
                for end, maximum in rows
            ]
            self._hourly[entity_id] = cached = (hour, samples)
        return cached[1]

    async def _state_samples(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[Sample]:
        """Return reported values at their report times; unknown states as gaps."""
        try:
            states = await fetch_states(self.hass, entity_id, start, end)
        except (KeyError, RuntimeError, ValueError) as err:  # pragma: no cover
            LOGGER.debug("No history for %s: %s", entity_id, err)
            return []
        return [(when, value, None) for when, value in states]


def _projectable(meter: Meter) -> bool:
    return (
        meter.kind is MeterKind.WINDOW_USED
        and meter.value is not None
        and meter.resets_at is not None
    )


def _number(state: str) -> float | None:
    if state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        return float(state)
    except ValueError:
        return None


async def fetch_states(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> Iterable[tuple[datetime, float | None]]:
    """Return (time, value) for each recorded state; None where unknown."""
    # Imported here: the recorder is an optional (after-)dependency.
    from homeassistant.components.recorder.history import (  # noqa: PLC0415
        state_changes_during_period,
    )
    from homeassistant.helpers.recorder import get_instance  # noqa: PLC0415

    result = await get_instance(hass).async_add_executor_job(
        state_changes_during_period, hass, start, end, entity_id, True
    )
    return [
        (state.last_changed, _number(state.state))
        for state in result.get(entity_id, [])
    ]


async def fetch_statistics(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> Iterable[tuple[float, float]]:
    """Return (period end timestamp, maximum) for each hour in the recorder."""
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        statistics_during_period,
    )
    from homeassistant.helpers.recorder import get_instance  # noqa: PLC0415

    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, "hour", None, {"max"}
    )
    return [
        (row["end"], maximum)
        for row in result.get(entity_id, [])
        if (maximum := row.get("max")) is not None
    ]
