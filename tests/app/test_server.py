"""Tests for the app's API, ingress page and terminal proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import gzip
from pathlib import Path

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from ai_usage_app import server
from ai_usage_app.collector import Collector
from ai_usage_app.installer import Installer
from ai_usage_app.settings import Options, Paths
from ai_usage_app.ui import _relative, render_page
from ai_usage_core.model import Meter, MeterKind, SourceType, Status, UsageReport

from ..fake_cli import FakeRunner

TOKEN = "secret-token"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def collector(paths: Paths) -> Collector:
    collector = Collector(
        Options(enabled=("claude", "gemini"), interval_minutes=5),
        paths,
        FakeRunner(),
        Installer(paths, FakeRunner()),
    )
    collector.reports["claude"] = UsageReport(
        provider="claude",
        source=SourceType.CLI,
        status=Status.OK,
        collected_at=NOW,
        meters=(
            Meter(
                key="weekly",
                name="Week",
                kind=MeterKind.WINDOW_USED,
                value=46,
                unit="%",
                resets_at=datetime.now(UTC) + timedelta(days=1, hours=3),
            ),
        ),
        version="2.1.296",
    )
    collector.updated_at = NOW
    return collector


@pytest.fixture
async def api(
    collector: Collector,
) -> AsyncIterator[TestClient[web.Request, web.Application]]:
    async with TestClient(
        TestServer(server.api_app(collector, TOKEN, "inst"))
    ) as client:
        yield client


async def test_api_requires_token(
    api: TestClient[web.Request, web.Application],
) -> None:
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = await api.get("/api/v1/reports", headers=headers)
        assert response.status == 401


async def test_api_reports(api: TestClient[web.Request, web.Application]) -> None:
    auth = {"Authorization": f"Bearer {TOKEN}"}
    response = await api.get("/api/v1/reports", headers=auth)
    assert response.status == 200
    data = await response.json()
    assert data["schema"] == 1
    assert data["instance_id"] == "inst"
    assert data["updated_at"] == NOW.isoformat()
    assert data["interval_minutes"] == 5
    assert set(data["providers"]) == {"claude", "gemini", "codex"}
    assert data["providers"]["claude"]["report"]["meters"][0]["value"] == 46
    assert data["providers"]["gemini"]["report"] is None

    response = await api.get("/api/v1/health", headers=auth)
    assert await response.json() == {
        "status": "ok",
        "instance_id": "inst",
        "app_version": "dev",
    }


async def test_api_reports_before_first_run(paths: Paths) -> None:
    collector = Collector(
        Options(enabled=(), interval_minutes=5),
        paths,
        FakeRunner(),
        Installer(paths, FakeRunner()),
    )
    async with TestClient(
        TestServer(server.api_app(collector, TOKEN, "inst"))
    ) as client:
        response = await client.get(
            "/api/v1/reports", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert (await response.json())["updated_at"] is None


async def test_api_refresh(
    api: TestClient[web.Request, web.Application], collector: Collector
) -> None:
    response = await api.post(
        "/api/v1/refresh", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status == 202
    assert collector._wake.is_set()


# Ingress


@pytest.fixture
async def fake_ttyd() -> AsyncIterator[TestServer]:
    async def page(request: web.Request) -> web.Response:
        return web.Response(
            text=f"ttyd {request.path_qs}",
            headers={"X-Upstream": "1", "Connection": "close"},
        )

    async def socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(protocols=["tty"])
        await ws.prepare(request)
        async for message in ws:
            if message.type is WSMsgType.TEXT:
                await ws.send_str(message.data.upper())
            elif message.type is WSMsgType.BINARY:
                await ws.send_bytes(message.data[::-1])
        return ws

    async def gzipped(request: web.Request) -> web.Response:
        return web.Response(
            body=gzip.compress(b"<html>terminal</html>"),
            headers={"Content-Encoding": "gzip", "Content-Type": "text/html"},
        )

    app = web.Application()
    app.router.add_get("/term/ws", socket)
    app.router.add_get("/term/gz", gzipped)
    app.router.add_route("*", "/term/{tail:.*}", page)
    async with TestServer(app) as ttyd:
        yield ttyd


@pytest.fixture
async def ingress(
    collector: Collector, fake_ttyd: TestServer, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[TestClient[web.Request, web.Application]]:
    monkeypatch.setattr(server, "INGRESS_PROXY", "127.0.0.1")
    app = server.ingress_app(collector, str(fake_ttyd.make_url("")).rstrip("/"))
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_ingress_rejects_other_clients(collector: Collector) -> None:
    async with TestClient(TestServer(server.ingress_app(collector))) as client:
        response = await client.get("/")
        assert response.status == 403


async def test_ingress_page(ingress: TestClient[web.Request, web.Application]) -> None:
    response = await ingress.get("/")
    assert response.status == 200
    page = await response.text()
    assert "Claude Code 2.1.296" in page
    assert "46% used" in page
    assert "Resets in 1 d" in page
    assert 'href="term/?arg=shell"' in page


async def test_ingress_refresh(
    ingress: TestClient[web.Request, web.Application], collector: Collector
) -> None:
    response = await ingress.post("/refresh", allow_redirects=False)
    assert response.status == 303
    assert response.headers["Location"] == "./"
    assert collector._wake.is_set()


async def test_terminal_http(ingress: TestClient[web.Request, web.Application]) -> None:
    response = await ingress.get("/term/?arg=claude")
    assert response.status == 200
    assert await response.text() == "ttyd /term/?arg=claude"
    assert response.headers["X-Upstream"] == "1"


async def test_terminal_compressed(
    ingress: TestClient[web.Request, web.Application],
) -> None:
    """The gzipped terminal page reaches the browser still marked as gzip."""
    response = await ingress.get("/term/gz")
    assert response.headers["Content-Encoding"] == "gzip"
    assert await response.text() == "<html>terminal</html>"


async def test_terminal_websocket(
    ingress: TestClient[web.Request, web.Application],
) -> None:
    async with ingress.ws_connect("/term/ws", protocols=["tty"]) as ws:
        assert ws.protocol == "tty"
        await ws.send_str("hello")
        assert (await ws.receive()).data == "HELLO"
        await ws.send_bytes(b"abc")
        assert (await ws.receive()).data == b"cba"


# Page rendering


def test_render_states(tmp_path: Path) -> None:
    signed_out = UsageReport(
        provider="codex",
        source=SourceType.CLI,
        status=Status.SIGNED_OUT,
        collected_at=NOW,
        error="Codex is not signed in",
    ).to_dict()
    ok_report = UsageReport(
        provider="gemini",
        source=SourceType.CLI,
        status=Status.OK,
        collected_at=NOW,
        meters=(
            Meter(key="a", name="Hot", kind=MeterKind.WINDOW_USED, value=95, unit="%"),
            Meter(key="b", name="Warm", kind=MeterKind.WINDOW_USED, value=80, unit="%"),
            Meter(
                key="c",
                name="Unknown",
                kind=MeterKind.WINDOW_USED,
                value=None,
                unit="%",
            ),
            Meter(
                key="d",
                name="Credits <b>",
                kind=MeterKind.BALANCE,
                value=3,
                unit="credits",
            ),
            Meter(
                key="e", name="None", kind=MeterKind.BALANCE, value=None, unit="credits"
            ),
        ),
    ).to_dict()
    page = render_page(
        {
            "claude": {
                "enabled": False,
                "installed": False,
                "install_error": None,
                "report": None,
            },
            "gemini": {
                "enabled": True,
                "installed": True,
                "install_error": None,
                "report": ok_report,
            },
            "codex": {
                "enabled": True,
                "installed": True,
                "install_error": None,
                "report": signed_out,
            },
        },
        5,
        now=NOW,
    )
    assert "Turn on <b>claude</b>" in page
    assert 'class="max" style="width:95.0%"' in page
    assert 'class="hi" style="width:80.0%"' in page
    assert "Credits &lt;b&gt;" in page
    assert "3 credits" in page
    assert '<a class="btn primary" href="term/?arg=codex">Sign in</a>' in page
    assert "device-code sign-in" in page.lower()
    assert "Sign in again" in page

    installing = render_page(
        {
            "claude": {
                "enabled": True,
                "installed": False,
                "install_error": None,
                "report": None,
            }
        },
        5,
    )
    assert "Installing…" in installing
    failed = render_page(
        {
            "claude": {
                "enabled": True,
                "installed": False,
                "install_error": "404",
                "report": None,
            }
        },
        5,
    )
    assert "Installing the Claude Code failed: 404" in failed


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (timedelta(seconds=-5), "now"),
        (timedelta(seconds=20), "1 min"),
        (timedelta(minutes=42), "42 min"),
        (timedelta(hours=4, minutes=31), "4 h 31 min"),
        (timedelta(days=2, hours=3), "2 d 3 h"),
    ],
)
def test_relative(delta: timedelta, text: str) -> None:
    assert _relative(NOW + delta, NOW) == text
