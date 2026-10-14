"""App options and file locations."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

PROVIDERS = ("claude", "gemini", "codex")

DEFAULT_INTERVAL_MINUTES = 5
MIN_INTERVAL_MINUTES = 5

API_PORT = 8080
INGRESS_PORT = 8099
TTYD_PORT = 7681
INGRESS_PROXY = "172.30.32.2"


@dataclass(frozen=True)
class Paths:
    """Where the app keeps its state. Everything lives under ``/data``."""

    data: Path

    @property
    def options(self) -> Path:
        """Options written by the Supervisor."""
        return self.data / "options.json"

    @property
    def home(self) -> Path:
        """HOME for the CLIs: their sign-ins and settings live here."""
        return self.data / "home"

    @property
    def bin(self) -> Path:
        """Where the official installers put the CLIs (excluded from backups)."""
        return self.home / ".local" / "bin"

    @property
    def work(self) -> Path:
        """Working directory for CLI runs."""
        return self.data / "work"

    @property
    def token(self) -> Path:
        """Bearer token for the usage API."""
        return self.data / "api_token"

    @property
    def instance_id(self) -> Path:
        """Random ID identifying this app installation."""
        return self.data / "instance_id"

    @property
    def reports(self) -> Path:
        """Latest reports, so a restart doesn't start empty."""
        return self.data / "reports.json"

    @property
    def state(self) -> Path:
        """Installer bookkeeping (installed versions, last update check)."""
        return self.data / "install_state.json"

    def env(self) -> dict[str, str]:
        """Environment for running the CLIs."""
        return {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            # The app updates the CLIs itself, on its own schedule.
            "DISABLE_AUTOUPDATER": "1",
            "AGY_CLI_DISABLE_AUTO_UPDATE": "true",
            # Claude Code on Alpine uses the system ripgrep.
            "USE_BUILTIN_RIPGREP": "0",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }

    def ensure(self) -> None:
        """Create the directories."""
        for path in (self.home, self.bin, self.work):
            path.mkdir(parents=True, exist_ok=True)


def default_paths() -> Paths:
    """Return the paths for this container (``AI_USAGE_DATA`` overrides /data)."""
    return Paths(Path(os.environ.get("AI_USAGE_DATA", "/data")))


@dataclass(frozen=True)
class Options:
    """The app's configuration tab."""

    enabled: tuple[str, ...]
    interval_minutes: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Options:
        """Read the Supervisor's options.json content."""
        interval = int(data.get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
        return cls(
            enabled=tuple(name for name in PROVIDERS if data.get(name)),
            interval_minutes=max(interval, MIN_INTERVAL_MINUTES),
        )

    @classmethod
    def load(cls, paths: Paths) -> Options:
        """Read the options file; everything off when there is none yet."""
        try:
            data = json.loads(paths.options.read_text())
        except FileNotFoundError:
            data = {}
        return cls.from_dict(data)
