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
import signal
from typing import Protocol

#: Output beyond this is cut off; usage reports are a few kilobytes.
MAX_OUTPUT = 4 * 1024 * 1024


class CommandNotFoundError(Exception):
    """The CLI isn't installed."""


class CommandTimeoutError(TimeoutError):
    """The CLI didn't finish in time; carries what it printed until then."""

    def __init__(self, stdout: str, stderr: str) -> None:
        """Keep the partial output."""
        super().__init__("timed out")
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, kw_only=True)
class CommandResult:
    """A finished command."""

    returncode: int
    stdout: str
    stderr: str
    aborted: bool = False
    """Stopped early because the output contained an ``abort_on`` phrase."""


class LineSession(Protocol):
    """A running command exchanging lines over stdin and stdout."""

    async def send(self, line: str) -> None:
        """Write one line to the command's stdin."""

    async def readline(self) -> str | None:
        """Read one line from stdout; ``None`` once the command closed it."""


class CommandRunner(Protocol):
    """Starts CLI commands."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        time_limit: float,
        abort_on: Sequence[str] = (),
    ) -> CommandResult:
        """Run a command to completion.

        Stops it early (``aborted``) once its output contains any of
        ``abort_on``, e.g. a prompt it would wait at. Raises
        ``CommandNotFoundError`` if it isn't installed and ``TimeoutError`` if it
        doesn't finish in time.
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
                # Own process group, so stopping it also stops helper processes
                # the CLI started (which would otherwise keep its pipes open).
                start_new_session=True,
            )
        except FileNotFoundError as err:
            raise CommandNotFoundError(argv[0]) from err

    async def run(
        self,
        argv: Sequence[str],
        *,
        time_limit: float,
        abort_on: Sequence[str] = (),
    ) -> CommandResult:
        """Run a command to completion (see ``CommandRunner.run``)."""
        process = await self._start(argv, stdin=asyncio.subprocess.DEVNULL)
        assert process.stdout is not None
        assert process.stderr is not None
        out_stream, err_stream = process.stdout, process.stderr
        stdout, stderr = bytearray(), bytearray()
        phrases = [phrase.encode() for phrase in abort_on]
        abort = asyncio.Event()

        async def pump(stream: asyncio.StreamReader, buffer: bytearray) -> None:
            while chunk := await stream.read(65536):
                if len(buffer) < MAX_OUTPUT:
                    buffer.extend(chunk[: MAX_OUTPUT - len(buffer)])
                if any(p in stdout or p in stderr for p in phrases):
                    abort.set()

        async def finish() -> None:
            await asyncio.gather(pump(out_stream, stdout), pump(err_stream, stderr))
            await process.wait()

        finished = asyncio.create_task(finish())
        aborted = asyncio.create_task(abort.wait())
        try:
            async with asyncio.timeout(time_limit):
                await asyncio.wait(
                    {finished, aborted}, return_when=asyncio.FIRST_COMPLETED
                )
                if abort.is_set():
                    await _stop(process)
        except TimeoutError:
            await _stop(process)
            raise CommandTimeoutError(
                stdout.decode(errors="replace"), stderr.decode(errors="replace")
            ) from None
        finally:
            for task in (finished, aborted):
                task.cancel()
            await asyncio.gather(finished, aborted, return_exceptions=True)
        return CommandResult(
            returncode=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            aborted=abort.is_set(),
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
    """Stop a process and its group: close stdin, then terminate, then kill."""
    if process.stdin is not None:
        process.stdin.close()
    _signal_group(process, signal.SIGTERM)
    try:
        async with asyncio.timeout(5):
            await process.wait()
    except TimeoutError:
        _signal_group(process, signal.SIGKILL)
        await process.wait()
    # Helpers may outlive the main process; make sure they're gone too.
    _signal_group(process, signal.SIGKILL)


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)
