"""Coordinator polling the AI Usage app."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AppAuthError, AppClient, AppConnectionError, AppSnapshot
from .const import CONF_APP_SLUG, DOMAIN, LOGGER, PROVIDERS, SCAN_INTERVAL
from .core.forecast import Projection
from .core.model import Status
from .history import MeterId, UsageHistory

type AiUsageConfigEntry = ConfigEntry[AiUsageCoordinator]


class AiUsageCoordinator(DataUpdateCoordinator[AppSnapshot]):
    """Fetches every provider's latest report from the app."""

    config_entry: AiUsageConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AiUsageConfigEntry,
        client: AppClient,
        history: UsageHistory,
    ) -> None:
        """Poll ``client`` for ``entry``."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.history = history
        self.projections: dict[MeterId, Projection] = {}
        self.hub_device_id = ""
        """Device registry ID of the app's device (set during setup)."""

    @property
    def instance_id(self) -> str:
        """The app installation this entry belongs to."""
        assert self.config_entry.unique_id is not None
        return self.config_entry.unique_id

    async def _async_update_data(self) -> AppSnapshot:
        try:
            snapshot = await self.client.get_snapshot()
        except AppAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_token"
            ) from err
        except AppConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        self._update_issues(snapshot)
        self._remove_stale_devices(snapshot)
        self.projections = self.history.update(snapshot)
        return snapshot

    def _update_issues(self, snapshot: AppSnapshot) -> None:
        """Raise a repair when a provider needs signing in or failed to install."""
        slug = self.config_entry.data.get(CONF_APP_SLUG)
        link = f"/app/{slug}" if slug else None
        for provider, info in PROVIDERS.items():
            state = snapshot.providers.get(provider)
            enabled = state is not None and state.enabled
            report = state.report if state else None
            issues = {
                "signed_out": enabled
                and report is not None
                and report.status is Status.SIGNED_OUT,
                "install_failed": enabled and bool(state and state.install_error),
            }
            for issue, active in issues.items():
                issue_id = f"{issue}_{self.instance_id}_{provider}"
                if not active:
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                    continue
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    is_persistent=False,
                    severity=ir.IssueSeverity.WARNING,
                    learn_more_url=link,
                    translation_key=issue,
                    translation_placeholders={
                        "provider": info.name,
                        "cli": info.cli,
                        "error": (state.install_error if state else None) or "",
                    },
                )

    def _remove_stale_devices(self, snapshot: AppSnapshot) -> None:
        """Remove the devices of providers turned off in the app."""
        registry = dr.async_get(self.hass)
        for provider in PROVIDERS:
            state = snapshot.providers.get(provider)
            if state is not None and state.enabled:
                continue
            device = registry.async_get_device_by_identifier(
                (DOMAIN, f"{self.instance_id}_{provider}"), self.config_entry.entry_id
            )
            if device is not None:
                registry.async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )
