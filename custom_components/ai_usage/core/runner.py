"""Running the official CLIs.

CLI sources talk to a ``CommandRunner`` rather than to ``asyncio`` directly, so
tests can replay recorded output. ``SubprocessRunner`` is the real
implementation the app uses.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
import contextlib
from dataclasses import dataclass
import os
from typing import Protocol

#: Output beyond this is cut off; usage reports are a few kilobytes.
MAX_OUTPUT = 4 * 1024 * 1024


class CommandNotFoundError(Exception):
    """The CLI isn't installed."""


@dataclass(frozen=True, kw_only=True)
class CommandResult:
    """A finished command."""

    returncode: int
    stdout: str
    stderr: str


class LineSession(Protocol):
    """A running command exchanging lines over stdin and stdout."""

    async def send(self, line: str) -> None:
        """Write one line to the command's stdin."""

    async def readline(self) -> str | None:
        """Read one line from stdout; ``None`` once the command closed it."""


class CommandRunner(Protocol):
    """Starts CLI commands."""

    async def run(self, argv: Sequence[str], *, time_limit: float) -> CommandResult:
        """Run a command to completion.

        Raises ``CommandNotFoundError`` if it isn't installed and
        ``TimeoutError`` if it doesn't finish in time.
        """

    def session(
        self, argv: Sequence[str]
    ) -> contextlib.AbstractAsyncContextManager[LineSession]:
        """Start a command for a line-based conversation; stopped on exit."""


class SubprocessRunner:
    """Runs commands as local subprocesses."""

    def __init__(
        self, env: Mapping[str, str] | None = None, cwd: str | None = None
    ) -> None:
        """Run commands in ``cwd`` with ``env`` added to the current environment."""
        self._env = {**os.environ, **(env or {})}
        self._cwd = cwd

    async def _start(
        self, argv: Sequence[str], *, stdin: int | None
    ) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
                limit=MAX_OUTPUT,
            )
        except FileNotFoundError as err:
            raise CommandNotFoundError(argv[0]) from err

    async def run(self, argv: Sequence[str], *, time_limit: float) -> CommandResult:
        """Run a command to completion."""
        process = await self._start(argv, stdin=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(time_limit):
                stdout, stderr = await process.communicate()
        except TimeoutError:
            await _stop(process)
            raise
        return CommandResult(
            returncode=process.returncode or 0,
            stdout=stdout[:MAX_OUTPUT].decode(errors="replace"),
            stderr=stderr[:MAX_OUTPUT].decode(errors="replace"),
        )

    @contextlib.asynccontextmanager
    async def session(self, argv: Sequence[str]) -> AsyncIterator[LineSession]:
        """Start a command for a line-based conversation."""
        process = await self._start(argv, stdin=asyncio.subprocess.PIPE)
        try:
            yield _ProcessSession(process)
        finally:
            await _stop(process)


class _ProcessSession:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    async def send(self, line: str) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(line.encode() + b"\n")
        await self._process.stdin.drain()

    async def readline(self) -> str | None:
        assert self._process.stdout is not None
        line = await self._process.stdout.readline()
        return line.decode(errors="replace") if line else None


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Stop a process: close stdin, then terminate, then kill."""
    if process.returncode is not None:
        return
    if process.stdin is not None:
        process.stdin.close()
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        async with asyncio.timeout(5):
            await process.wait()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
