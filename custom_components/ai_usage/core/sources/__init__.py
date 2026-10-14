"""Usage sources, by provider key."""

from .base import CliSource
from .claude import ClaudeSource
from .codex import CodexSource
from .gemini import GeminiSource

CLI_SOURCES: dict[str, type[CliSource]] = {
    source.provider: source for source in (ClaudeSource, GeminiSource, CodexSource)
}

__all__ = ["CLI_SOURCES", "CliSource"]
