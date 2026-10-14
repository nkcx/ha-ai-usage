"""Fixtures for AI Usage tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import PropertyMock, patch

from aiohttp.test_utils import TestServer
from homeassistant.const import CONF_API_TOKEN, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import (
    HomeAssistantSnapshotExtension,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ai_usage.const import CONF_APP_SLUG, DOMAIN

from .fake_app import INSTANCE_ID, TOKEN, FakeApp


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Enable loading custom_components/ai_usage."""


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Use the Home Assistant snapshot extension (snapshots/ directory)."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def entity_registry_enabled_by_default() -> Iterator[None]:
    """Create entities that are disabled by default as enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
        new_callable=PropertyMock,
    ):
        yield


@pytest.fixture
def fake_app() -> FakeApp:
    """Return the fake app's state."""
    return FakeApp()


@pytest.fixture
async def app_server(
    fake_app: FakeApp, socket_enabled: None
) -> AsyncIterator[TestServer]:
    """Serve the fake app on localhost."""
    async with TestServer(fake_app.app()) as server:
        yield server


@pytest.fixture
def entry_data(app_server: TestServer) -> dict[str, object]:
    """Config entry data pointing at the fake app."""
    return {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: app_server.port,
        CONF_API_TOKEN: TOKEN,
        CONF_APP_SLUG: "425b2f89_ai_usage",
    }


@pytest.fixture
def config_entry(entry_data: dict[str, object]) -> MockConfigEntry:
    """Return a config entry for the fake app."""
    return MockConfigEntry(
        domain=DOMAIN, title="AI Usage", unique_id=INSTANCE_ID, data=entry_data
    )


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration against the fake app."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
