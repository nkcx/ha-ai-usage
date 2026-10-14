"""A command runner that replays recorded CLI output."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
import contextlib
import json
from pathlib import Path
from typing import Any

from custom_components.ai_usage.core.runner import (
    CommandNotFoundError,
    CommandResult,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    """Return a fixture file's text."""
    return (FIXTURES / name).read_text()


def fixture_json(name: str) -> Any:
    """Return a fixture file parsed as JSON."""
    return json.loads(fixture(name))


type Reply = CommandResult | BaseException


class FakeRunner:
    """Answers ``run`` from a table and ``session`` with a JSON-RPC peer."""

    def __init__(
        self,
        runs: dict[tuple[str, ...], Reply] | None = None,
        rpc: dict[str, Any] | None = None,
        *,
        installed: bool = True,
    ) -> None:
        """Reply to commands from ``runs`` and RPC methods from ``rpc``.

        An ``rpc`` value ``{"__error__": {...}}`` is sent as an error response.
        """
        self.runs = runs or {}
        self.rpc = rpc or {}
        self.installed = installed
        self.calls: list[tuple[str, ...]] = []

    async def run(self, argv: Sequence[str], *, time_limit: float) -> CommandResult:
        """Return the recorded result for ``argv``."""
        key = tuple(argv)
        self.calls.append(key)
        if not self.installed:
            raise CommandNotFoundError(argv[0])
        reply = self.runs[key]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @contextlib.asynccontextmanager
    async def session(self, argv: Sequence[str]) -> AsyncIterator[FakeSession]:
        """Start a fake JSON-RPC peer."""
        self.calls.append(tuple(argv))
        if not self.installed:
            raise CommandNotFoundError(argv[0])
        yield FakeSession(self.rpc)


class FakeSession:
    """Answers JSON-RPC requests line by line, with a notification first."""

    def __init__(self, rpc: dict[str, Any]) -> None:
        """Answer from ``rpc`` (method → result)."""
        self.rpc = rpc
        self.sent: list[dict[str, Any]] = []
        self._out: list[str] = []

    async def send(self, line: str) -> None:
        """Record a request and queue its response."""
        message = json.loads(line)
        self.sent.append(message)
        if "id" not in message:
            return
        method = message["method"]
        if method not in self.rpc:
            return  # never answered: the peer exits
        # Unrelated output first: log noise and a notification.
        self._out.append("starting up\n")
        self._out.append(json.dumps({"method": "configWarning", "params": {}}) + "\n")
        reply = self.rpc[method]
        if isinstance(reply, dict) and "__error__" in reply:
            response = {"id": message["id"], "error": reply["__error__"]}
        else:
            response = {"id": message["id"], "result": reply}
        self._out.append(json.dumps(response) + "\n")

    async def readline(self) -> str | None:
        """Return the next queued line, or ``None`` when there is none."""
        return self._out.pop(0) if self._out else None


def ok(stdout: str) -> CommandResult:
    """Return a successful command result."""
    return CommandResult(returncode=0, stdout=stdout, stderr="")
