"""Installing the official CLIs, only for enabled providers.

Nothing is baked into the image: each CLI is installed with its vendor's
official installer into ``/data/home/.local``, which backups skip. Sign-ins
live elsewhere in ``/data/home`` and are kept when a provider is turned off.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import platform
import shutil
import time
from typing import Any

from ai_usage_core.runner import CommandRunner

from .settings import Paths

_LOGGER = logging.getLogger(__name__)

INSTALL_TIME_LIMIT = 900.0
UPDATE_INTERVAL = 24 * 3600

CODEX_RELEASE = "https://github.com/openai/codex/releases/latest/download/codex-{arch}-unknown-linux-musl.tar.gz"


@dataclass(frozen=True)
class CliSpec:
    """How to install, update and remove one CLI."""

    provider: str
    executable: str
    install: tuple[str, ...]
    update: tuple[str, ...]
    remove: tuple[str, ...]
    """Paths under HOME holding the binaries (never the sign-in)."""


def _codex_install() -> tuple[str, ...]:
    arch = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }[platform.machine().lower()]
    url = CODEX_RELEASE.format(arch=arch)
    script = (
        "set -eu; tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT; "
        f'curl -fsSL "{url}" | tar -xz -C "$tmp"; '
        'mkdir -p "$HOME/.local/bin"; '
        'install -m 755 "$tmp"/codex-*-unknown-linux-musl "$HOME/.local/bin/codex"'
    )
    return ("bash", "-c", script)


SPECS = {
    spec.provider: spec
    for spec in (
        CliSpec(
            provider="claude",
            executable="claude",
            install=(
                "bash",
                "-c",
                "set -o pipefail; curl -fsSL https://claude.ai/install.sh | bash",
            ),
            update=("claude", "update"),
            remove=(".local/bin/claude", ".local/share/claude"),
        ),
        CliSpec(
            provider="gemini",
            executable="agy",
            install=(
                "bash",
                "-c",
                "set -o pipefail; curl -fsSL https://antigravity.google/cli/install.sh | bash",
            ),
            update=("agy", "update"),
            remove=(
                ".local/bin/agy",
                ".gemini/antigravity-cli/bin",
                ".gemini/antigravity-cli/updater",
            ),
        ),
        CliSpec(
            provider="codex",
            executable="codex",
            install=(),  # built at runtime: depends on the CPU architecture
            update=(),  # re-installing fetches the latest release
            remove=(".local/bin/codex",),
        ),
    )
}


class Installer:
    """Keeps the enabled CLIs installed and up to date."""

    def __init__(self, paths: Paths, runner: CommandRunner) -> None:
        """Install into ``paths.home`` using ``runner``."""
        self.paths = paths
        self.runner = runner
        self.errors: dict[str, str] = {}

    def installed(self, provider: str) -> bool:
        """Return whether the provider's CLI is installed."""
        return (self.paths.bin / SPECS[provider].executable).exists()

    async def ensure(self, enabled: tuple[str, ...]) -> None:
        """Install missing CLIs, update stale ones, remove disabled ones."""
        state = self._load_state()
        for provider, spec in SPECS.items():
            if provider not in enabled:
                if self._remove(spec):
                    _LOGGER.info("Removed the %s CLI (provider turned off)", provider)
                state.pop(provider, None)
                continue
            if not self.installed(provider):
                _LOGGER.info("Installing the %s CLI", provider)
                if await self._run(provider, self._install_command(spec)):
                    state[provider] = time.time()
            elif time.time() - state.get(provider, 0) > UPDATE_INTERVAL:
                _LOGGER.info("Updating the %s CLI", provider)
                command = spec.update or self._install_command(spec)
                if await self._run(provider, command):
                    state[provider] = time.time()
        self._save_state(state)

    def _install_command(self, spec: CliSpec) -> tuple[str, ...]:
        return spec.install or _codex_install()

    async def _run(self, provider: str, command: tuple[str, ...]) -> bool:
        try:
            result = await self.runner.run(command, time_limit=INSTALL_TIME_LIMIT)
        except (OSError, TimeoutError) as err:
            self.errors[provider] = f"{type(err).__name__}: {err}"
        else:
            if result.returncode == 0:
                self.errors.pop(provider, None)
                return True
            self.errors[provider] = (result.stderr or result.stdout).strip()[-500:]
        _LOGGER.error(
            "Installing/updating the %s CLI failed: %s", provider, self.errors[provider]
        )
        return False

    def _remove(self, spec: CliSpec) -> bool:
        removed = False
        for relative in spec.remove:
            path = self.paths.home / relative
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
                removed = True
            elif path.exists() or path.is_symlink():
                path.unlink()
                removed = True
        return removed

    def _load_state(self) -> dict[str, Any]:
        try:
            data: dict[str, Any] = json.loads(self.paths.state.read_text())
        except FileNotFoundError, json.JSONDecodeError:
            return {}
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        self.paths.state.write_text(json.dumps(state))
