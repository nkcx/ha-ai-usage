"""Base class for sources that read an official CLI."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
import logging
from typing import Any, ClassVar

from ..model import Account, Meter, SourceType, Status, UsageReport
from ..runner import CommandNotFoundError, CommandRunner

_LOGGER = logging.getLogger(__name__)

#: Seconds allowed for one CLI call.
COMMAND_TIMEOUT = 60.0

FIVE_HOURS = 5 * 3600
ONE_WEEK = 7 * 24 * 3600


class SourceError(Exception):
    """A collection run failed; the report status says how."""

    def __init__(self, status: Status, message: str) -> None:
        """Fail with a report status and a human-readable reason."""
        super().__init__(message)
        self.status = status


class CliSource(ABC):
    """Collects usage by running a provider's official CLI."""

    provider: ClassVar[str]
    """Provider key, e.g. ``"claude"``."""

    command: ClassVar[str]
    """The CLI executable."""

    def __init__(self, runner: CommandRunner) -> None:
        """Use ``runner`` to start the CLI."""
        self.runner = runner

    async def collect(self) -> UsageReport:
        """Run the CLI and return a report; never raises for expected failures."""
        now = datetime.now(UTC)
        try:
            account, meters, version, extra = await self._collect()
        except SourceError as err:
            return self._failed(now, err.status, str(err))
        except CommandNotFoundError:
            return self._failed(
                now, Status.NOT_INSTALLED, f"{self.command} is not installed"
            )
        except TimeoutError:
            return self._failed(
                now, Status.ERROR, f"{self.command} did not answer in time"
            )
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.debug("Unexpected %s output", self.provider, exc_info=True)
            return self._failed(
                now, Status.ERROR, f"Unrecognised {self.command} output: {err!r}"
            )
        return UsageReport(
            provider=self.provider,
            source=SourceType.CLI,
            status=Status.OK,
            collected_at=now,
            account=account,
            meters=tuple(meters),
            version=version,
            extra=extra,
        )

    def _failed(self, now: datetime, status: Status, message: str) -> UsageReport:
        return UsageReport(
            provider=self.provider,
            source=SourceType.CLI,
            status=status,
            collected_at=now,
            error=message,
        )

    @abstractmethod
    async def _collect(
        self,
    ) -> tuple[Account | None, list[Meter], str | None, dict[str, Any]]:
        """Return the account, meters, CLI version and diagnostic extras.

        Raise ``SourceError`` for expected failures (signed out, rate limited).
        """
