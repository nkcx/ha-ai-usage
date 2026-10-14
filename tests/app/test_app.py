"""Tests for the app: options, installer, collector, discovery and startup."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from ai_usage_app import __main__ as app_main, discovery
from ai_usage_app.collector import Collector
from ai_usage_app.installer import SPECS, Installer, _codex_install
from ai_usage_app.settings import Options, Paths, default_paths
from ai_usage_core.model import Status
from ai_usage_core.runner import CommandResult
from ai_usage_core.sources.claude import AUTH_STATUS, USAGE as CLAUDE_USAGE
from ai_usage_core.sources.gemini import CREDITS, USAGE as GEMINI_USAGE

from ..fake_cli import FakeRunner, fixture, ok

# Options and paths


def test_options() -> None:
    options = Options.from_dict({"claude": True, "codex": True, "interval_minutes": 1})
    assert options.enabled == ("claude", "codex")
    assert options.interval_minutes == 5
    assert Options.from_dict({}).enabled == ()


def test_options_load(paths: Paths) -> None:
    assert Options.load(paths) == Options(enabled=(), interval_minutes=5)
    paths.options.write_text(json.dumps({"gemini": True, "interval_minutes": 15}))
    assert Options.load(paths) == Options(enabled=("gemini",), interval_minutes=15)


def test_paths(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    env = paths.env()
    assert env["HOME"] == str(paths.home)
    assert env["PATH"].startswith(f"{paths.bin}:")
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert paths.bin.is_dir()
    assert paths.work.is_dir()
    monkeypatch.setenv("AI_USAGE_DATA", "/somewhere")
    assert default_paths().data == Path("/somewhere")


# Installer


class InstallRunner:
    """Pretends to install: creates the executable when an install runs."""

    def __init__(
        self, paths: Paths, result: CommandResult | BaseException | None = None
    ) -> None:
        self.paths = paths
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    async def run(self, argv: Sequence[str], *, time_limit: float) -> CommandResult:
        command = tuple(argv)
        self.calls.append(command)
        if isinstance(self.result, BaseException):
            raise self.result
        if self.result is not None:
            return self.result
        text = " ".join(command)
        for spec in SPECS.values():
            if spec.executable in text or (
                spec.provider == "codex" and "codex" in text
            ):
                (self.paths.bin / spec.executable).touch()
        return ok("")

    def session(self, argv: Sequence[str]) -> Any:
        raise NotImplementedError


async def test_install_update_remove(paths: Paths) -> None:
    runner = InstallRunner(paths)
    installer = Installer(paths, runner)
    await installer.ensure(("claude", "codex"))
    assert installer.installed("claude")
    assert installer.installed("codex")
    assert not installer.installed("gemini")
    assert len(runner.calls) == 2
    assert "claude.ai/install.sh" in runner.calls[0][-1]
    assert "unknown-linux-musl.tar.gz" in runner.calls[1][-1]

    # Fresh installs aren't updated again right away.
    await installer.ensure(("claude", "codex"))
    assert len(runner.calls) == 2

    # A day later they are: Claude with its own updater, Codex by re-installing.
    state = json.loads(paths.state.read_text())
    paths.state.write_text(json.dumps(dict.fromkeys(state, 0)))
    await installer.ensure(("claude", "codex"))
    assert runner.calls[2] == ("claude", "update")
    assert "unknown-linux-musl.tar.gz" in runner.calls[3][-1]

    # Turning Claude off removes its binaries but keeps the sign-in.
    (paths.home / ".local" / "share" / "claude" / "versions").mkdir(parents=True)
    (paths.home / ".claude").mkdir()
    (paths.home / ".claude" / ".credentials.json").write_text("{}")
    await installer.ensure(("codex",))
    assert not installer.installed("claude")
    assert not (paths.home / ".local" / "share" / "claude").exists()
    assert (paths.home / ".claude" / ".credentials.json").exists()
    assert "claude" not in json.loads(paths.state.read_text())


async def test_install_failure(paths: Paths) -> None:
    installer = Installer(
        paths,
        InstallRunner(paths, CommandResult(returncode=1, stdout="", stderr="404\n")),
    )
    await installer.ensure(("gemini",))
    assert installer.errors == {"gemini": "404"}
    assert "gemini" not in json.loads(paths.state.read_text())

    installer.runner = InstallRunner(paths, TimeoutError("slow"))
    await installer.ensure(("gemini",))
    assert installer.errors == {"gemini": "TimeoutError: slow"}

    installer.runner = InstallRunner(paths)
    await installer.ensure(("gemini",))
    assert installer.errors == {}


def test_install_state_unreadable(paths: Paths) -> None:
    paths.state.write_text("{")
    assert Installer(paths, InstallRunner(paths))._load_state() == {}


def test_remove_symlink(paths: Paths) -> None:
    link = paths.bin / "agy"
    link.symlink_to(paths.home / "missing")
    assert Installer(paths, InstallRunner(paths))._remove(SPECS["gemini"])
    assert not link.is_symlink()


@pytest.mark.parametrize(
    ("machine", "arch"), [("x86_64", "x86_64"), ("aarch64", "aarch64")]
)
def test_codex_install_arch(machine: str, arch: str) -> None:
    with patch("ai_usage_app.installer.platform.machine", return_value=machine):
        assert f"codex-{arch}-unknown-linux-musl" in _codex_install()[-1]


# Collector


def collector_runner() -> FakeRunner:
    return FakeRunner(
        {
            ("claude", *AUTH_STATUS): ok(fixture("claude_auth.json")),
            ("claude", *CLAUDE_USAGE): ok(fixture("claude_usage.jsonl")),
            ("agy", *GEMINI_USAGE): ok(fixture("agy_usage.json")),
            ("agy", *CREDITS): ok(fixture("agy_credits.json")),
        }
    )


def make_collector(paths: Paths, *enabled: str) -> Collector:
    runner = collector_runner()
    return Collector(
        Options(enabled=enabled, interval_minutes=5),
        paths,
        runner,
        Installer(paths, InstallRunner(paths)),
    )


async def test_collect(paths: Paths) -> None:
    collector = make_collector(paths, "claude", "gemini")
    sessions = paths.home / ".claude" / "projects" / str(paths.work).replace("/", "-")
    sessions.mkdir(parents=True)
    (sessions / "x.jsonl").write_text("{}")

    await collector.collect()
    assert collector.reports["claude"].status is Status.OK
    assert collector.reports["gemini"].status is Status.OK
    assert collector.updated_at is not None
    assert not (sessions / "x.jsonl").exists()

    snapshot = collector.snapshot()
    assert snapshot["claude"]["enabled"] is True
    assert snapshot["claude"]["installed"] is False
    assert snapshot["claude"]["report"]["meters"][0]["key"] == "session"
    assert snapshot["codex"] == {
        "enabled": False,
        "installed": False,
        "install_error": None,
        "report": None,
    }

    # Reports survive a restart, but a provider turned off isn't reported.
    restarted = make_collector(paths, "claude")
    assert set(restarted.reports) == {"claude", "gemini"}
    assert restarted.snapshot()["gemini"]["report"] is None


async def test_collect_only_enabled(paths: Paths) -> None:
    collector = make_collector(paths, "claude")
    await collector.collect(("gemini", "claude"))
    assert set(collector.reports) == {"claude"}


async def test_collect_logs_errors(
    paths: Paths, caplog: pytest.LogCaptureFixture
) -> None:
    collector = make_collector(paths, "codex")
    await collector.collect()
    assert collector.reports["codex"].status is Status.ERROR
    assert "codex: error" in caplog.text


def test_collector_unreadable_reports(
    paths: Paths, caplog: pytest.LogCaptureFixture
) -> None:
    paths.reports.write_text('{"claude": {"schema": 99}}')
    assert make_collector(paths, "claude").reports == {}
    assert "Ignoring unreadable" in caplog.text


async def test_run_loop(paths: Paths) -> None:
    collector = make_collector(paths, "claude")
    collector.options = Options(enabled=("claude",), interval_minutes=5)
    real_timeout = asyncio.timeout
    with patch(
        "ai_usage_app.collector.asyncio.timeout",
        side_effect=lambda _: real_timeout(0.05),
    ):
        task = asyncio.create_task(collector.run())
        await asyncio.sleep(0.2)
        collector.request_refresh()
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert "claude" in collector.reports


# Discovery


async def test_discovery_without_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    async with ClientSession() as session:
        assert not await discovery.announce("t", session)


@pytest.mark.parametrize(("status", "expected"), [(200, True), (403, False)])
async def test_discovery(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: bool
) -> None:
    received: list[dict[str, Any]] = []

    async def handler(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer sup"
        received.append(await request.json())
        return web.json_response({"result": "ok"}, status=status)

    app = web.Application()
    app.router.add_post("/discovery", handler)
    async with TestServer(app) as server, ClientSession() as session:
        monkeypatch.setenv("SUPERVISOR_TOKEN", "sup")
        monkeypatch.setattr(
            discovery, "SUPERVISOR", str(server.make_url("")).rstrip("/")
        )
        monkeypatch.setattr(discovery.socket, "gethostname", lambda: "abc-ai-usage")
        assert await discovery.announce("tok", session) is expected
    assert received == [
        {
            "service": "ai_usage",
            "config": {"host": "abc-ai-usage", "port": 8080, "token": "tok"},
        }
    ]


async def test_discovery_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup")
    monkeypatch.setattr(discovery, "SUPERVISOR", "http://127.0.0.1:9")
    async with ClientSession() as session:
        assert not await discovery.announce("tok", session)


# Startup


def test_load_token(paths: Paths) -> None:
    token = app_main.load_token(paths)
    assert len(token) > 30
    assert app_main.load_token(paths) == token
    assert paths.token.stat().st_mode & 0o777 == 0o600


async def test_main(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_USAGE_DATA", str(paths.data))
    monkeypatch.setattr(app_main, "API_PORT", 0)
    monkeypatch.setattr(app_main, "INGRESS_PORT", 0)
    announce = AsyncMock(return_value=True)
    monkeypatch.setattr(app_main, "announce", announce)
    with patch.object(Collector, "run", AsyncMock()) as run:
        await app_main.main()
    run.assert_awaited_once()
    announce.assert_awaited_once()
    assert paths.token.exists()
