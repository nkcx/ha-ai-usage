"""Tests for the config flow."""

from __future__ import annotations

from aiohttp.test_utils import TestServer
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER
from homeassistant.const import CONF_API_TOKEN, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ai_usage.const import CONF_APP_SLUG, DOMAIN

from .fake_app import INSTANCE_ID, TOKEN, FakeApp


def user_input(server: TestServer, token: str = TOKEN) -> dict[str, object]:
    return {CONF_HOST: "127.0.0.1", CONF_PORT: server.port, CONF_API_TOKEN: token}


def discovery(server: TestServer, token: str = TOKEN) -> HassioServiceInfo:
    return HassioServiceInfo(
        config={"host": "127.0.0.1", "port": server.port, "token": token},
        name="AI Usage",
        slug="425b2f89_ai_usage",
        uuid="discovery-uuid",
    )


async def test_user(hass: HomeAssistant, app_server: TestServer) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(app_server)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AI Usage"
    assert result["data"] == user_input(app_server)
    assert result["result"].unique_id == INSTANCE_ID


@pytest.mark.parametrize(
    ("token", "status", "error"),
    [("wrong", None, "invalid_auth"), (TOKEN, 500, "cannot_connect")],
)
async def test_user_errors(
    hass: HomeAssistant,
    app_server: TestServer,
    fake_app: FakeApp,
    token: str,
    status: int | None,
    error: str,
) -> None:
    fake_app.status = status
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(app_server, token)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Recovers once the problem is fixed.
    fake_app.status = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(app_server)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_already_configured(
    hass: HomeAssistant, app_server: TestServer, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(app_server)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_hassio(hass: HomeAssistant, app_server: TestServer) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery(app_server)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        **user_input(app_server),
        CONF_APP_SLUG: "425b2f89_ai_usage",
    }
    assert result["result"].unique_id == INSTANCE_ID


@pytest.mark.parametrize(
    ("token", "status", "reason"),
    [("wrong", None, "invalid_auth"), (TOKEN, 503, "cannot_connect")],
)
async def test_hassio_errors(
    hass: HomeAssistant,
    app_server: TestServer,
    fake_app: FakeApp,
    token: str,
    status: int | None,
    reason: str,
) -> None:
    fake_app.status = status
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery(app_server, token)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_hassio_updates_existing(
    hass: HomeAssistant, app_server: TestServer, fake_app: FakeApp
) -> None:
    """A new discovery (new token, e.g. after reinstalling) updates the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=INSTANCE_ID,
        data={CONF_HOST: "old", CONF_PORT: 1, CONF_API_TOKEN: "old"},
    )
    entry.add_to_hass(hass)
    fake_app.token = "new-token"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_HASSIO},
        data=discovery(app_server, "new-token"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_API_TOKEN] == "new-token"
    assert entry.data[CONF_PORT] == app_server.port


async def test_reauth(
    hass: HomeAssistant, setup_entry: MockConfigEntry, fake_app: FakeApp
) -> None:
    fake_app.token = "rotated"
    result = await setup_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "rotated"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_entry.data[CONF_API_TOKEN] == "rotated"


async def test_reauth_wrong_app(
    hass: HomeAssistant, setup_entry: MockConfigEntry, fake_app: FakeApp
) -> None:
    fake_app.snapshot["instance_id"] = "another"
    result = await setup_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_app"


async def test_reconfigure(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    app_server: TestServer,
    fake_app: FakeApp,
) -> None:
    result = await setup_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    fake_app.status = 500
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(app_server)
    )
    assert result["errors"] == {"base": "cannot_connect"}
    fake_app.status = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(app_server)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_entry.data[CONF_HOST] == "127.0.0.1"
    assert setup_entry.data[CONF_APP_SLUG] == "425b2f89_ai_usage"


async def test_reconfigure_wrong_app(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    app_server: TestServer,
    fake_app: FakeApp,
) -> None:
    fake_app.snapshot["instance_id"] = "another"
    result = await setup_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input(app_server)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_app"
