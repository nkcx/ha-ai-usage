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
import tempfile
from typing import IO, Protocol

#: Output beyond this is cut off; usage reports are a few kilobytes.
MAX_OUTPUT = 4 * 1024 * 1024
#: How often a running command's output is checked for ``abort_on`` phrases.
POLL_INTERVAL = 0.25
#: Seconds a command gets to exit after SIGTERM, then after SIGKILL.
STOP_GRACE = 5.0


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
        self,
        argv: Sequence[str],
        *,
        stdin: int | None,
        stdout: int | IO[bytes] = asyncio.subprocess.PIPE,
        stderr: int | IO[bytes] = asyncio.subprocess.PIPE,
    ) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=self._env,
                cwd=self._cwd,
                limit=MAX_OUTPUT,
                # Own process group, so stopping it also stops helper processes
                # the CLI started.
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
        """Run a command to completion (see ``CommandRunner.run``).

        Output goes to temporary files rather than pipes: a helper process the
        CLI leaves behind could hold a pipe open and keep the run waiting.
        """
        phrases = [phrase.encode() for phrase in abort_on]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = await self._start(
                argv, stdin=asyncio.subprocess.DEVNULL, stdout=out, stderr=err
            )
            aborted = False
            try:
                async with asyncio.timeout(time_limit):
                    while process.returncode is None:
                        with contextlib.suppress(TimeoutError):
                            async with asyncio.timeout(POLL_INTERVAL):
                                await process.wait()
                        if phrases and process.returncode is None:
                            text = _read(out) + _read(err)
                            if any(phrase in text for phrase in phrases):
                                aborted = True
                                await _stop(process)
            except TimeoutError:
                await _stop(process)
                raise CommandTimeoutError(
                    _read(out).decode(errors="replace"),
                    _read(err).decode(errors="replace"),
                ) from None
            finally:
                # Leftover helpers in the group don't outlive the run.
                _signal_group(process, signal.SIGKILL)
            return CommandResult(
                returncode=process.returncode or 0,
                stdout=_read(out).decode(errors="replace"),
                stderr=_read(err).decode(errors="replace"),
                aborted=aborted,
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


def _read(file: IO[bytes]) -> bytes:
    """Return what has been written to ``file`` so far (up to MAX_OUTPUT)."""
    return os.pread(file.fileno(), MAX_OUTPUT, 0)


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Stop a process and its group: close stdin, then terminate, then kill."""
    if process.stdin is not None:
        process.stdin.close()
    _signal_group(process, signal.SIGTERM)
    try:
        async with asyncio.timeout(STOP_GRACE):
            await process.wait()
    except TimeoutError:
        _signal_group(process, signal.SIGKILL)
        # With pipes, waiting can hang on a helper outside the group holding
        # them open; the process itself is gone, so stop waiting.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(STOP_GRACE):
                await process.wait()
    _signal_group(process, signal.SIGKILL)


def _signal_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, sig)
