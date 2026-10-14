"""Config flow for AI Usage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import voluptuous as vol

from .api import AppAuthError, AppClient, AppConnectionError, AppSnapshot
from .const import CONF_APP_SLUG, DEFAULT_PORT, DOMAIN, LOGGER

TITLE = "AI Usage"

_TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(CONF_API_TOKEN): _TOKEN_SELECTOR,
        }
    )


class AiUsageConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the connection to the AI Usage app."""

    VERSION = 1

    def __init__(self) -> None:
        """Start a flow."""
        self._discovery: dict[str, Any] = {}

    async def _validate(
        self, data: Mapping[str, Any]
    ) -> tuple[AppSnapshot | None, dict[str, str]]:
        """Try the connection; return the app's snapshot or form errors."""
        client = AppClient(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_API_TOKEN],
        )
        try:
            return await client.get_snapshot(), {}
        except AppAuthError:
            return None, {"base": "invalid_auth"}
        except AppConnectionError as err:
            LOGGER.debug("Can't connect to the app: %s", err)
            return None, {"base": "cannot_connect"}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect to an app by address and token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            snapshot, errors = await self._validate(user_input)
            if snapshot is not None:
                await self.async_set_unique_id(snapshot.instance_id)
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title=TITLE, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema({}), user_input
            ),
            errors=errors,
        )

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle discovery by the AI Usage app."""
        config = discovery_info.config
        self._discovery = {
            CONF_HOST: config[CONF_HOST],
            CONF_PORT: int(config[CONF_PORT]),
            CONF_API_TOKEN: config["token"],
            CONF_APP_SLUG: discovery_info.slug,
        }
        snapshot, errors = await self._validate(self._discovery)
        if snapshot is None:
            return self.async_abort(reason=errors["base"])
        await self.async_set_unique_id(snapshot.instance_id)
        # Already set up: take the new address/token (e.g. after a reinstall).
        self._abort_if_unique_id_configured(updates=self._discovery)
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setting up the discovered app."""
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data=self._discovery)
        self._set_confirm_only()
        return self.async_show_form(step_id="hassio_confirm")

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle the app rejecting the token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the app's current token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            snapshot, errors = await self._validate(data)
            if snapshot is not None:
                await self.async_set_unique_id(snapshot.instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_app")
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_TOKEN): _TOKEN_SELECTOR}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the app's address or token."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            snapshot, errors = await self._validate(data)
            if snapshot is not None:
                await self.async_set_unique_id(snapshot.instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_app")
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(entry.data),
                {CONF_HOST: entry.data[CONF_HOST], CONF_PORT: entry.data[CONF_PORT]},
            ),
            errors=errors,
        )
