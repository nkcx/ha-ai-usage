"""Tests for the CLI sources, against recorded and sanitized CLI output."""

from datetime import UTC, datetime
import json
from typing import Any

import pytest

from custom_components.ai_usage.core.model import MeterKind, Status, account_id
from custom_components.ai_usage.core.runner import CommandResult, CommandTimeoutError
from custom_components.ai_usage.core.sources import CLI_SOURCES
from custom_components.ai_usage.core.sources.claude import (
    AUTH_STATUS,
    USAGE as CLAUDE_USAGE,
    ClaudeSource,
)
from custom_components.ai_usage.core.sources.codex import CodexSource
from custom_components.ai_usage.core.sources.gemini import (
    CREDITS,
    USAGE as GEMINI_USAGE,
    GeminiSource,
)

from .fake_cli import FakeRunner, fixture, fixture_json, ok

CLAUDE_AUTH = ("claude", *AUTH_STATUS)
CLAUDE_USAGE_CMD = ("claude", *CLAUDE_USAGE)
AGY_USAGE = ("agy", *GEMINI_USAGE)
AGY_CREDITS = ("agy", *CREDITS)


def claude_runner(
    auth: dict[str, Any] | str | None = None, usage: str | None = None
) -> FakeRunner:
    auth_text = (
        fixture("claude_auth.json")
        if auth is None
        else auth
        if isinstance(auth, str)
        else json.dumps(auth)
    )
    return FakeRunner(
        {
            CLAUDE_AUTH: ok(auth_text),
            CLAUDE_USAGE_CMD: ok(
                fixture("claude_usage.jsonl") if usage is None else usage
            ),
        }
    )


def usage_lines(rate_limits: Any) -> str:
    return json.dumps(
        {"type": "assistant", "usage_report": {"rate_limits": rate_limits}}
    )


def test_registry() -> None:
    assert set(CLI_SOURCES) == {"claude", "gemini", "codex"}


# Claude


async def test_claude() -> None:
    report = await ClaudeSource(claude_runner()).collect()
    assert report.status is Status.OK
    assert report.version == "2.1.296"
    assert report.account is not None
    assert report.account.id == account_id("user@example.com")
    assert report.account.name == "user@example.com"
    assert report.account.plan == "max"
    meters = {meter.key: meter for meter in report.meters}
    assert list(meters) == ["session", "weekly", "weekly_fable"]
    session = meters["session"]
    assert session.kind is MeterKind.WINDOW_USED
    assert session.value == 27
    assert session.unit == "%"
    assert session.window_seconds == 5 * 3600
    assert session.resets_at == datetime(2026, 10, 15, 1, 30, 0, 270759, UTC)
    assert meters["weekly"].value == 46
    assert meters["weekly"].active is True
    assert meters["weekly_fable"].name == "Week (Fable)"
    assert meters["weekly_fable"].model == "Fable"
    assert meters["weekly_fable"].window_seconds == 7 * 24 * 3600
    assert report.extra == {"extra_usage_enabled": False}


async def test_claude_extra_usage_and_unknown_limit() -> None:
    usage = usage_lines(
        {
            "limits": [
                {
                    "kind": "monthly_cap",
                    "group": "monthly",
                    "percent": 3,
                    "resets_at": None,
                },
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 1,
                    "scope": None,
                },
            ],
            "extra_usage": {
                "is_enabled": True,
                "monthly_limit": 10000,
                "used_credits": 1234,
                "currency": "EUR",
            },
        }
    )
    report = await ClaudeSource(claude_runner(usage=usage)).collect()
    assert report.status is Status.OK
    assert report.version is None
    meters = {meter.key: meter for meter in report.meters}
    assert meters["monthly_cap"].name == "Monthly cap"
    assert meters["monthly_cap"].resets_at is None
    assert meters["monthly_cap"].window_seconds is None
    assert meters["weekly_scoped"].model is None
    extra = meters["extra_usage"]
    assert extra.kind is MeterKind.SPEND
    assert extra.value == 12.34
    assert extra.limit == 100
    assert extra.unit == "EUR"


async def test_claude_no_limits_free_plan() -> None:
    """Free plans report no rate limits: an empty, successful report."""
    auth = json.loads(fixture("claude_auth.json")) | {"subscriptionType": "free"}
    runner = claude_runner(auth=auth, usage=usage_lines(None))
    report = await ClaudeSource(runner).collect()
    assert report.status is Status.OK
    assert report.meters == ()


async def test_claude_no_limits_paid_plan() -> None:
    """A paid plan without limits means Claude Code couldn't fetch them."""
    report = await ClaudeSource(claude_runner(usage=usage_lines(None))).collect()
    assert report.status is Status.ERROR
    assert report.error == "Claude Code returned no usage limits for the max plan"


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        ({"loggedIn": False}, "not signed in"),
        ("not json", "not signed in"),
        ({"loggedIn": True, "authMethod": "apiKey"}, "'apiKey'"),
    ],
)
async def test_claude_signed_out(auth: dict[str, Any] | str, message: str) -> None:
    report = await ClaudeSource(claude_runner(auth=auth)).collect()
    assert report.status is Status.SIGNED_OUT
    assert report.error is not None
    assert message in report.error
    assert report.meters == ()


@pytest.mark.parametrize(
    "usage", ["", '{"type": "result"}\n', "garbage\n", "{not json\n"]
)
async def test_claude_bad_output(usage: str) -> None:
    report = await ClaudeSource(claude_runner(usage=usage)).collect()
    assert report.status is Status.ERROR
    assert report.error is not None
    assert report.error.startswith("Unrecognised claude output")


async def test_claude_no_identity() -> None:
    report = await ClaudeSource(
        claude_runner(auth={"loggedIn": True, "authMethod": "claude.ai"})
    ).collect()
    assert report.status is Status.ERROR


async def test_claude_org_identity() -> None:
    report = await ClaudeSource(
        claude_runner(auth={"loggedIn": True, "authMethod": "claude.ai", "orgId": "o"})
    ).collect()
    assert report.account is not None
    assert report.account.id == account_id("o")
    assert report.account.name is None


async def test_not_installed() -> None:
    report = await ClaudeSource(FakeRunner(installed=False)).collect()
    assert report.status is Status.NOT_INSTALLED
    assert report.error == "claude is not installed"


async def test_timeout() -> None:
    runner = FakeRunner({CLAUDE_AUTH: TimeoutError()})
    report = await ClaudeSource(runner).collect()
    assert report.status is Status.ERROR
    assert report.error == "claude did not answer in time"


async def test_timeout_with_output() -> None:
    runner = FakeRunner(
        {CLAUDE_AUTH: CommandTimeoutError("", "Starting\nWaiting for X\n\n")}
    )
    report = await ClaudeSource(runner).collect()
    assert report.error == "claude did not answer in time (last output: Waiting for X)"
    assert report.extra == {"output": {"stderr": "Starting\nWaiting for X\n\n"}}


# Gemini


def gemini_runner(
    usage: str | None = None, credits_out: str | None = None
) -> FakeRunner:
    return FakeRunner(
        {
            AGY_USAGE: ok(fixture("agy_usage.json") if usage is None else usage),
            AGY_CREDITS: ok(
                fixture("agy_credits.json") if credits_out is None else credits_out
            ),
        }
    )


async def test_gemini() -> None:
    report = await GeminiSource(gemini_runner()).collect()
    assert report.status is Status.OK
    assert report.account is None
    meters = {meter.key: meter for meter in report.meters}
    assert list(meters) == [
        "gemini_models_weekly",
        "gemini_models_5h",
        "claude_and_gpt_models_weekly",
        "claude_and_gpt_models_5h",
        "credits",
    ]
    weekly = meters["gemini_models_weekly"]
    assert weekly.name == "Gemini Models, weekly"
    assert weekly.value == 2.1  # remaining_fraction 0.9791
    assert weekly.window_seconds == 7 * 24 * 3600
    assert weekly.resets_at == datetime(2026, 10, 14, 21, 35, 7, tzinfo=UTC)
    assert weekly.group == "Gemini Models"
    assert meters["gemini_models_5h"].name == "Gemini Models, 5-hour"
    assert meters["gemini_models_5h"].value == 2.8
    assert meters["credits"].kind is MeterKind.BALANCE
    assert meters["credits"].value == 0
    assert meters["credits"].unit == "credits"


async def test_gemini_unknown_window() -> None:
    usage = json.dumps(
        {
            "status": "SUCCESS",
            "command": {
                "name": "usage",
                "data": {
                    "groups": [
                        {
                            "name": "Media",
                            "buckets": [{"id": "daily", "remaining_fraction": 0.5}],
                        }
                    ]
                },
            },
        }
    )
    report = await GeminiSource(gemini_runner(usage=usage)).collect()
    meter = report.meters[0]
    assert meter.key == "media_daily"
    assert meter.name == "Media, daily"
    assert meter.window_seconds is None
    assert meter.resets_at is None
    assert meter.value == 50


@pytest.mark.parametrize(
    "usage",
    [
        json.dumps({"status": "ERROR", "response": "Please sign in to continue"}),
        json.dumps({"status": "ERROR", "error": "authentication failed or timed out"}),
        "Error: not authenticated, run agy to log in",
    ],
)
async def test_gemini_signed_out(usage: str) -> None:
    report = await GeminiSource(gemini_runner(usage=usage)).collect()
    assert report.status is Status.SIGNED_OUT


async def test_gemini_auth_prompt() -> None:
    """Not signed in, agy asks for a sign-in on stderr (and is stopped there)."""
    prompt = CommandResult(
        returncode=0,
        stdout="",
        stderr="Authentication required. Please visit the URL to log in:\n",
        aborted=True,
    )
    runner = FakeRunner({AGY_USAGE: prompt, AGY_CREDITS: prompt})
    report = await GeminiSource(runner).collect()
    assert report.status is Status.SIGNED_OUT
    assert report.error == "The Antigravity CLI is not signed in"


async def test_gemini_stops_at_auth_prompt() -> None:
    """Both agy calls ask the runner to stop at the sign-in prompt."""
    runner = gemini_runner()
    await GeminiSource(runner).collect()
    assert runner.abort_on == {
        AGY_USAGE: ("Authentication required",),
        AGY_CREDITS: ("Authentication required",),
    }


@pytest.mark.parametrize(
    "usage",
    [
        json.dumps({"status": "ERROR", "response": "backend unavailable"}),
        json.dumps({"status": "SUCCESS", "command": {"name": "credits", "data": {}}}),
        json.dumps({"status": "SUCCESS", "response": "text only"}),
        "panic: something",
    ],
)
async def test_gemini_errors(usage: str) -> None:
    report = await GeminiSource(gemini_runner(usage=usage)).collect()
    assert report.status is Status.ERROR


# Codex


async def test_codex() -> None:
    runner = FakeRunner(rpc=fixture_json("codex_chatgpt.json"))
    report = await CodexSource(runner).collect()
    assert report.status is Status.OK
    assert report.version == "0.162.1"
    assert report.account is not None
    assert report.account.id == account_id("user@example.com")
    assert report.account.plan == "pro"
    meters = {meter.key: meter for meter in report.meters}
    assert list(meters) == ["primary", "secondary", "credits", "codex_other_primary"]
    assert meters["primary"].name == "5-hour"
    assert meters["primary"].value == 42
    assert meters["primary"].window_seconds == 5 * 3600
    assert meters["primary"].resets_at == datetime.fromtimestamp(1792027800, UTC)
    assert meters["secondary"].name == "Week"
    assert meters["credits"].value == 12.5
    assert meters["codex_other_primary"].name == "GPT-5.5 Spark, 5-hour"
    assert report.extra == {"limit_reached": None, "spend_control_reached": False}
    assert runner.calls == [("codex", "app-server")]


async def test_codex_single_snapshot() -> None:
    """Older servers only send ``rateLimits``."""
    rpc = fixture_json("codex_chatgpt.json")
    limits = rpc["account/rateLimits/read"]
    del limits["rateLimitsByLimitId"]
    del limits["rateLimits"]["limitId"]
    limits["rateLimits"]["credits"] = {"hasCredits": True, "unlimited": True}
    limits["rateLimits"]["primary"]["windowDurationMins"] = 60
    limits["rateLimits"]["secondary"]["windowDurationMins"] = 1440
    report = await CodexSource(FakeRunner(rpc=rpc)).collect()
    names = [meter.name for meter in report.meters]
    assert names == ["1-hour", "1-day"]


@pytest.mark.parametrize(
    ("minutes", "name"), [(None, "Limit"), (90, "90-minute"), (14 * 1440, "14-day")]
)
async def test_codex_window_names(minutes: int | None, name: str) -> None:
    rpc = fixture_json("codex_chatgpt.json")
    limits = rpc["account/rateLimits/read"]
    del limits["rateLimitsByLimitId"]
    limits["rateLimits"]["primary"]["windowDurationMins"] = minutes
    limits["rateLimits"]["primary"]["resetsAt"] = None
    report = await CodexSource(FakeRunner(rpc=rpc)).collect()
    assert report.meters[0].name == name
    assert report.meters[0].resets_at is None


async def test_codex_api_key() -> None:
    report = await CodexSource(
        FakeRunner(rpc=fixture_json("codex_apikey.json"))
    ).collect()
    assert report.status is Status.SIGNED_OUT
    assert report.error == "Codex is signed in with 'apiKey', not a ChatGPT account"


async def test_codex_signed_out() -> None:
    rpc = fixture_json("codex_apikey.json")
    rpc["account/read"] = {"account": None, "requiresOpenaiAuth": True}
    report = await CodexSource(FakeRunner(rpc=rpc)).collect()
    assert report.status is Status.SIGNED_OUT


async def test_codex_no_email() -> None:
    rpc = fixture_json("codex_chatgpt.json")
    rpc["account/read"]["account"]["email"] = None
    rpc["initialize"] = {}
    report = await CodexSource(FakeRunner(rpc=rpc)).collect()
    assert report.account is not None
    assert report.account.id == "chatgpt"
    assert report.version is None


async def test_codex_rate_limit_error() -> None:
    rpc = fixture_json("codex_chatgpt.json")
    rpc["account/rateLimits/read"] = {"__error__": {"code": -32603, "message": "boom"}}
    report = await CodexSource(FakeRunner(rpc=rpc)).collect()
    assert report.status is Status.ERROR
    assert report.error == "Codex: boom"


async def test_codex_exits_early() -> None:
    rpc = fixture_json("codex_chatgpt.json")
    del rpc["account/read"]
    report = await CodexSource(FakeRunner(rpc=rpc)).collect()
    assert report.status is Status.ERROR
    assert "exited before answering account/read" in (report.error or "")


async def test_codex_not_installed() -> None:
    report = await CodexSource(FakeRunner(installed=False)).collect()
    assert report.status is Status.NOT_INSTALLED


def test_command_result() -> None:
    assert CommandResult(returncode=1, stdout="", stderr="x").stderr == "x"
