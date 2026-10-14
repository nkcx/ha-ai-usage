"""Constants for the AI Usage integration."""

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "ai_usage"
LOGGER = logging.getLogger(__package__)

CONF_APP_SLUG: Final = "app_slug"
DEFAULT_PORT: Final = 8080

#: The app reads the CLIs every few minutes; asking it is cheap and local.
SCAN_INTERVAL: Final = timedelta(seconds=60)
#: After "Refresh", the app needs a moment to run the CLIs.
REFRESH_DELAY: Final = 20


@dataclass(frozen=True)
class ProviderInfo:
    """Static facts about a provider, for device info."""

    name: str
    manufacturer: str
    cli: str


PROVIDERS: Final = {
    "claude": ProviderInfo("Claude", "Anthropic", "Claude Code"),
    "gemini": ProviderInfo("Gemini", "Google", "Antigravity CLI"),
    "codex": ProviderInfo("Codex", "OpenAI", "Codex CLI"),
}
