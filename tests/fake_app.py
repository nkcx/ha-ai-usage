"""A fake AI Usage app API, served for real on localhost."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from aiohttp import web

from custom_components.ai_usage.core.model import SourceType, Status, UsageReport
from custom_components.ai_usage.core.sources.claude import (
    parse_auth_status,
    parse_usage as parse_claude,
)
from custom_components.ai_usage.core.sources.gemini import parse_credits, parse_usage

from .fake_cli import fixture, fixture_json

TOKEN = "app-token"
INSTANCE_ID = "0123456789abcdef0123456789abcdef"
COLLECTED = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)


def claude_report() -> dict[str, Any]:
    """Return a Claude report parsed from the recorded fixtures."""
    meters, version, extra = parse_claude(fixture("claude_usage.jsonl"))
    return UsageReport(
        provider="claude",
        source=SourceType.CLI,
        status=Status.OK,
        collected_at=COLLECTED,
        account=parse_auth_status(fixture("claude_auth.json")),
        meters=tuple(meters),
        version=version,
        extra=extra,
    ).to_dict()


def gemini_report() -> dict[str, Any]:
    """Return a Gemini report parsed from the recorded fixtures."""
    meters = parse_usage(fixture_json("agy_usage.json")["command"]["data"])
    meters += parse_credits(fixture_json("agy_credits.json")["command"]["data"])
    return UsageReport(
        provider="gemini",
        source=SourceType.CLI,
        status=Status.OK,
        collected_at=COLLECTED,
        meters=tuple(meters),
    ).to_dict()


def signed_out_report(provider: str) -> dict[str, Any]:
    """Return a report for a provider that isn't signed in."""
    return UsageReport(
        provider=provider,
        source=SourceType.CLI,
        status=Status.SIGNED_OUT,
        collected_at=COLLECTED,
        error=f"{provider} is not signed in",
    ).to_dict()


def default_snapshot() -> dict[str, Any]:
    """Return the app's response: Claude and Gemini on, Codex signed out."""
    return {
        "schema": 1,
        "instance_id": INSTANCE_ID,
        "app_version": "0.1.0",
        "updated_at": COLLECTED.isoformat(),
        "interval_minutes": 5,
        "providers": {
            "claude": {
                "enabled": True,
                "installed": True,
                "install_error": None,
                "report": claude_report(),
            },
            "gemini": {
                "enabled": True,
                "installed": True,
                "install_error": None,
                "report": gemini_report(),
            },
            "codex": {
                "enabled": True,
                "installed": True,
                "install_error": None,
                "report": signed_out_report("codex"),
            },
        },
    }


class FakeApp:
    """State behind the fake API; tests change it between polls."""

    def __init__(self) -> None:
        """Start with the default snapshot."""
        self.snapshot: dict[str, Any] = default_snapshot()
        self.token = TOKEN
        self.status: int | None = None
        """Answer every request with this HTTP status instead."""
        self.refreshes = 0

    def provider(self, name: str) -> dict[str, Any]:
        """Return a provider's mutable state."""
        state: dict[str, Any] = self.snapshot["providers"][name]
        return state

    def set_meter(self, provider: str, key: str, **changes: Any) -> None:
        """Change a meter in a provider's report."""
        for meter in self.provider(provider)["report"]["meters"]:
            if meter["key"] == key:
                meter.update(changes)

    def app(self) -> web.Application:
        """Build the aiohttp application."""

        @web.middleware
        async def check(request: web.Request, handler: Any) -> web.StreamResponse:
            if self.status is not None:
                return web.Response(status=self.status, text="nope")
            if request.headers.get("Authorization") != f"Bearer {self.token}":
                return web.Response(status=401, text="Invalid or missing token")
            response: web.StreamResponse = await handler(request)
            return response

        async def reports(request: web.Request) -> web.Response:
            return web.json_response(copy.deepcopy(self.snapshot))

        async def refresh(request: web.Request) -> web.Response:
            self.refreshes += 1
            return web.json_response({"status": "scheduled"}, status=202)

        application = web.Application(middlewares=[check])
        application.router.add_get("/api/v1/reports", reports)
        application.router.add_post("/api/v1/refresh", refresh)
        return application
