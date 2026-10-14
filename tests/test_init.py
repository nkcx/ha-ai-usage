"""Tests for integration setup."""

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.ai_usage.const import DOMAIN


async def test_setup(hass: HomeAssistant) -> None:
    """The integration loads."""
    assert await async_setup_component(hass, DOMAIN, {})
