"""Claude subscription limits, read from Claude Code.

``claude -p /usage --output-format stream-json --verbose`` runs the built-in
``/usage`` command without a model call and emits a structured
``usage_report`` alongside the text. ``claude auth status --json`` gives the
signed-in account.
"""

from __future__ import annotations

import json
from typing import Any

from ..model import Account, Meter, MeterKind, Status, account_id, parse_time, slugify
from .base import COMMAND_TIMEOUT, FIVE_HOURS, ONE_WEEK, CliSource, SourceError

AUTH_STATUS = ("auth", "status", "--json")
USAGE = ("-p", "/usage", "--output-format", "stream-json", "--verbose")

_WINDOWS = {"session": FIVE_HOURS, "weekly": ONE_WEEK}


class ClaudeSource(CliSource):
    """Claude Code (Claude Pro / Max / Team plans)."""

    provider = "claude"
    command = "claude"

    async def _collect(
        self,
    ) -> tuple[Account | None, list[Meter], str | None, dict[str, Any]]:
        auth = await self.runner.run(
            (self.command, *AUTH_STATUS), time_limit=COMMAND_TIMEOUT
        )
        account = parse_auth_status(auth.stdout)
        usage = await self.runner.run(
            (self.command, *USAGE), time_limit=COMMAND_TIMEOUT
        )
        meters, version, extra = parse_usage(usage.stdout)
        return account, meters, version, extra


def parse_auth_status(output: str) -> Account:
    """Parse ``claude auth status --json``."""
    try:
        status = json.loads(output)
    except json.JSONDecodeError as err:
        raise SourceError(Status.SIGNED_OUT, "Claude Code is not signed in") from err
    if not status.get("loggedIn"):
        raise SourceError(Status.SIGNED_OUT, "Claude Code is not signed in")
    if status.get("authMethod") != "claude.ai":
        raise SourceError(
            Status.SIGNED_OUT,
            "Claude Code is not signed in with a Claude subscription "
            f"(signed in with {status.get('authMethod')!r})",
        )
    identity = status.get("email") or status.get("orgId")
    if not identity:
        raise ValueError("auth status has no account identity")
    return Account(
        id=account_id(identity),
        name=status.get("email"),
        plan=status.get("subscriptionType"),
    )


def parse_usage(output: str) -> tuple[list[Meter], str | None, dict[str, Any]]:
    """Parse the stream-json output of ``/usage``."""
    version: str | None = None
    report: dict[str, Any] | None = None
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        message = json.loads(line)
        if message.get("type") == "system" and message.get("subtype") == "init":
            version = message.get("claude_code_version")
        if "usage_report" in message:
            report = message["usage_report"]
    if report is None:
        raise ValueError("no usage_report in the /usage output")
    rate_limits = report.get("rate_limits")
    if not rate_limits:
        # Free plans and API-key sign-ins have no limits to report.
        return [], version, {}

    meters = [_limit_meter(limit) for limit in rate_limits["limits"]]
    extra_usage = rate_limits.get("extra_usage") or {}
    if extra_usage.get("is_enabled"):
        currency = extra_usage.get("currency") or "USD"
        # Amounts are in minor units (cents).
        scale = 10 ** int(extra_usage.get("decimal_places", 2))
        meters.append(
            Meter(
                key="extra_usage",
                name="Extra usage",
                kind=MeterKind.SPEND,
                value=extra_usage["used_credits"] / scale,
                unit=currency,
                limit=None
                if extra_usage.get("monthly_limit") is None
                else extra_usage["monthly_limit"] / scale,
            )
        )
    extra = {"extra_usage_enabled": bool(extra_usage.get("is_enabled"))}
    return meters, version, extra


def _limit_meter(limit: dict[str, Any]) -> Meter:
    kind = limit["kind"]
    model = ((limit.get("scope") or {}).get("model") or {}).get("display_name")
    group = limit.get("group")
    if kind == "session":
        key, name = "session", "Session"
    elif kind == "weekly_all":
        key, name = "weekly", "Week"
    elif model:
        key, name = f"{group or kind}_{slugify(model)}", f"Week ({model})"
    else:
        key, name = slugify(kind), kind.replace("_", " ").capitalize()
    return Meter(
        key=key,
        name=name,
        kind=MeterKind.WINDOW_USED,
        value=float(limit["percent"]),
        unit="%",
        resets_at=None
        if limit.get("resets_at") is None
        else parse_time(limit["resets_at"]),
        window_seconds=_WINDOWS.get(group or ""),
        group=group,
        model=model,
        active=limit.get("is_active"),
    )
