"""Tests for the app API client and diagnostics."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from custom_components.ai_usage.api import (
    AppAuthError,
    AppClient,
    AppConnectionError,
    AppSnapshot,
)

from .fake_app import TOKEN, FakeApp, default_snapshot


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"providers": None},
        {"providers": {"claude": {"enabled": True}}},
        {
            "providers": {
                "claude": {"enabled": True, "installed": True, "report": {"x": 1}}
            }
        },
    ],
)
def test_snapshot_invalid(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"app|report"):
        AppSnapshot.from_dict(default_snapshot() | change)


def test_snapshot_before_first_run() -> None:
    snapshot = AppSnapshot.from_dict(default_snapshot() | {"updated_at": None})
    assert snapshot.updated_at is None


async def test_client_errors(fake_app: FakeApp, socket_enabled: None) -> None:
    async with TestServer(fake_app.app()) as server, ClientSession() as session:
        client = AppClient(session, "127.0.0.1", server.port, TOKEN)
        assert (await client.get_snapshot()).app_version == "0.1.0"

        fake_app.status = 403
        with pytest.raises(AppAuthError):
            await client.get_snapshot()
        fake_app.status = 500
        with pytest.raises(AppConnectionError, match="HTTP 500"):
            await client.refresh()
        fake_app.status = None
        fake_app.snapshot = {"schema": 1}
        with pytest.raises(AppConnectionError, match="Malformed"):
            await client.get_snapshot()


async def test_client_not_json(socket_enabled: None) -> None:
    app = web.Application()
    app.router.add_get("/api/v1/reports", lambda _: web.Response(text="<html>"))
    async with TestServer(app) as server, ClientSession() as session:
        client = AppClient(session, "127.0.0.1", server.port, TOKEN)
        with pytest.raises(AppConnectionError):
            await client.get_snapshot()


async def test_client_unreachable(socket_enabled: None) -> None:
    async with ClientSession() as session:
        client = AppClient(session, "127.0.0.1", 9, TOKEN)
        with pytest.raises(AppConnectionError, match="Can't reach"):
            await client.get_snapshot()


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    setup_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, setup_entry)
    assert "user@example.com" not in str(diagnostics)
    assert TOKEN not in str(diagnostics)
    assert diagnostics == snapshot(exclude=lambda prop, path: prop == "port")
