"""Tests for the subprocess runner, with real subprocesses."""

from pathlib import Path
import sys

import pytest

from custom_components.ai_usage.core.runner import (
    CommandNotFoundError,
    CommandTimeoutError,
    SubprocessRunner,
)

PYTHON = sys.executable


async def test_run() -> None:
    runner = SubprocessRunner({"AI_USAGE_TEST": "hello"})
    result = await runner.run(
        [
            PYTHON,
            "-c",
            "import os, sys; print(os.environ['AI_USAGE_TEST']); "
            "print('err', file=sys.stderr); sys.exit(3)",
        ],
        time_limit=30,
    )
    assert result.returncode == 3
    assert result.stdout == "hello\n"
    assert result.stderr == "err\n"


async def test_run_cwd(tmp_path: Path) -> None:
    result = await SubprocessRunner(cwd=str(tmp_path)).run(
        [PYTHON, "-c", "import os; print(os.getcwd())"], time_limit=30
    )
    assert result.stdout.strip() == str(tmp_path)


async def test_run_not_found() -> None:
    with pytest.raises(CommandNotFoundError):
        await SubprocessRunner().run(["/nonexistent/ai-usage-cli"], time_limit=5)


async def test_run_timeout() -> None:
    script = "import sys, time; print('waiting...', file=sys.stderr, flush=True); time.sleep(30)"
    with pytest.raises(CommandTimeoutError) as err:
        await SubprocessRunner().run([PYTHON, "-c", script], time_limit=1)
    assert err.value.stderr == "waiting...\n"
    assert isinstance(err.value, TimeoutError)


async def test_session() -> None:
    script = "import sys\nfor line in sys.stdin:\n    print(line.strip().upper(), flush=True)\n"
    async with SubprocessRunner().session([PYTHON, "-c", script]) as session:
        await session.send("ping")
        assert await session.readline() == "PING\n"
        await session.send("pong")
        assert await session.readline() == "PONG\n"


async def test_session_eof() -> None:
    async with SubprocessRunner().session([PYTHON, "-c", "print('bye')"]) as session:
        assert await session.readline() == "bye\n"
        assert await session.readline() is None


async def test_session_kills_stubborn_process() -> None:
    script = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "sys.stdin.close()\n"
        "time.sleep(60)\n"
    )
    async with SubprocessRunner().session([PYTHON, "-c", script]) as session:
        assert await session.readline() == "ready\n"


async def test_session_not_found() -> None:
    with pytest.raises(CommandNotFoundError):
        async with SubprocessRunner().session(["/nonexistent/ai-usage-cli"]):
            pass  # pragma: no cover


async def test_run_abort_on() -> None:
    """A command waiting at a prompt is stopped as soon as the prompt appears."""
    script = (
        "import sys, time\n"
        "print('Authentication required. Visit the URL', file=sys.stderr, flush=True)\n"
        "time.sleep(30)\n"
    )
    result = await SubprocessRunner().run(
        [PYTHON, "-c", script], time_limit=20, abort_on=("Authentication required",)
    )
    assert result.aborted
    assert "Authentication required" in result.stderr


async def test_run_abort_on_not_seen() -> None:
    result = await SubprocessRunner().run(
        [PYTHON, "-c", "print('x' * 100000)"], time_limit=20, abort_on=("never",)
    )
    assert not result.aborted
    assert len(result.stdout) == 100001
