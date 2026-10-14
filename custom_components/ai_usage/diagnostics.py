"""Diagnostics for AI Usage."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import AiUsageConfigEntry

TO_REDACT = {CONF_API_TOKEN, "id", "name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AiUsageConfigEntry
) -> dict[str, Any]:
    """Return the entry's settings and the app's latest snapshot, redacted."""
    snapshot = entry.runtime_data.data
    providers: dict[str, Any] = {}
    for name, state in snapshot.providers.items():
        report = None if state.report is None else state.report.to_dict()
        if report is not None and report["account"] is not None:
            # Account IDs and names are redacted; meter names are kept.
            report["account"] = async_redact_data(report["account"], TO_REDACT)
        providers[name] = {
            "enabled": state.enabled,
            "installed": state.installed,
            "install_error": state.install_error,
            "report": report,
        }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "app": {
            "app_version": snapshot.app_version,
            "interval_minutes": snapshot.interval_minutes,
            "updated_at": None
            if snapshot.updated_at is None
            else snapshot.updated_at.isoformat(),
        },
        "providers": providers,
    }
