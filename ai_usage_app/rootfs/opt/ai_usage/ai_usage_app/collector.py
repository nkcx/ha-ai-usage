"""Collecting usage from the enabled CLIs on a schedule."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
from typing import Any

from ai_usage_core.model import UsageReport
from ai_usage_core.runner import CommandRunner
from ai_usage_core.sources import CLI_SOURCES

from .installer import Installer
from .settings import Options, Paths

_LOGGER = logging.getLogger(__name__)


class Collector:
    """Runs each enabled source and keeps the latest report per provider."""

    def __init__(
        self,
        options: Options,
        paths: Paths,
        runner: CommandRunner,
        installer: Installer,
    ) -> None:
        """Collect for ``options.enabled`` providers."""
        self.options = options
        self.paths = paths
        self.runner = runner
        self.installer = installer
        self.reports: dict[str, UsageReport] = {}
        self.updated_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._load()

    async def collect(self, providers: tuple[str, ...] | None = None) -> None:
        """Collect now (all enabled providers, or the given ones)."""
        async with self._lock:
            for provider in providers or self.options.enabled:
                if provider not in self.options.enabled:
                    continue
                report = await CLI_SOURCES[provider](self.runner).collect()
                if report.error:
                    _LOGGER.warning(
                        "%s: %s (%s)", provider, report.status, report.error
                    )
                self.reports[provider] = report
            self._prune_sessions()
            self.updated_at = datetime.now(UTC)
            self._save()

    def request_refresh(self) -> None:
        """Wake the loop for an immediate collection."""
        self._wake.set()

    async def run(self) -> None:
        """Install the CLIs, then collect every interval, forever."""
        while True:
            await self.installer.ensure(self.options.enabled)
            await self.collect()
            self._wake.clear()
            try:
                async with asyncio.timeout(self.options.interval_minutes * 60):
                    await self._wake.wait()
            except TimeoutError:
                pass

    def snapshot(self) -> dict[str, Any]:
        """Return the API view of every provider."""
        return {
            provider: {
                "enabled": provider in self.options.enabled,
                "installed": self.installer.installed(provider),
                "install_error": self.installer.errors.get(provider),
                "report": None
                if provider not in self.reports or provider not in self.options.enabled
                else self.reports[provider].to_dict(),
            }
            for provider in CLI_SOURCES
        }

    def _prune_sessions(self) -> None:
        """Delete the session logs Claude Code writes for each ``/usage`` run."""
        sessions = (
            self.paths.home
            / ".claude"
            / "projects"
            / str(self.paths.work).replace("/", "-")
        )
        for log in sessions.glob("*.jsonl"):
            log.unlink(missing_ok=True)

    def _save(self) -> None:
        data = {name: report.to_dict() for name, report in self.reports.items()}
        tmp = self.paths.reports.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.paths.reports)

    def _load(self) -> None:
        try:
            data = json.loads(self.paths.reports.read_text())
            self.reports = {
                name: UsageReport.from_dict(report) for name, report in data.items()
            }
        except FileNotFoundError:
            pass
        except ValueError, AttributeError:
            _LOGGER.warning("Ignoring unreadable %s", self.paths.reports)
