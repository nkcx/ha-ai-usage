"""Gemini subscription limits, read from the Antigravity CLI.

``agy -p /usage --output-format json`` and ``agy -p /credits`` run built-in
commands without a model call and return structured ``command.data``.
"""

from __future__ import annotations

import json
from typing import Any

from ..model import Account, Meter, MeterKind, Status, parse_time, slugify
from .base import COMMAND_TIMEOUT, FIVE_HOURS, ONE_WEEK, CliSource, SourceError

USAGE = ("-p", "/usage", "--output-format", "json")
CREDITS = ("-p", "/credits", "--output-format", "json")

_WINDOWS = {"5h": (FIVE_HOURS, "5-hour"), "weekly": (ONE_WEEK, "weekly")}
_SIGNED_OUT_HINTS = (
    "sign in",
    "signin",
    "log in",
    "login",
    "not authenticated",
    "unauthenticated",
)


class GeminiSource(CliSource):
    """Antigravity CLI (Google AI Pro / Ultra)."""

    provider = "gemini"
    command = "agy"

    async def _collect(
        self,
    ) -> tuple[Account | None, list[Meter], str | None, dict[str, Any]]:
        usage = await self.runner.run(
            (self.command, *USAGE), time_limit=COMMAND_TIMEOUT
        )
        meters = parse_usage(_command_data(usage.stdout, usage.stderr, "usage"))
        credits_result = await self.runner.run(
            (self.command, *CREDITS), time_limit=COMMAND_TIMEOUT
        )
        meters.extend(
            parse_credits(
                _command_data(credits_result.stdout, credits_result.stderr, "credits")
            )
        )
        # The CLI doesn't report which Google account is signed in.
        return None, meters, None, {}


def _command_data(stdout: str, stderr: str, name: str) -> dict[str, Any]:
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict) or result.get("status") != "SUCCESS":
        text = stdout if isinstance(result, dict) else f"{stdout}\n{stderr}"
        if any(hint in text.lower() for hint in _SIGNED_OUT_HINTS):
            raise SourceError(Status.SIGNED_OUT, "The Antigravity CLI is not signed in")
        raise ValueError(f"/{name} failed: {text.strip()[:200]}")
    command = result.get("command") or {}
    if command.get("name") != name or "data" not in command:
        raise ValueError(f"no /{name} data in the output")
    data: dict[str, Any] = command["data"]
    return data


def parse_usage(data: dict[str, Any]) -> list[Meter]:
    """Parse ``/usage`` data: groups of models sharing 5-hour and weekly limits."""
    meters = []
    for group in data["groups"]:
        group_name = group["name"]
        for bucket in group["buckets"]:
            window = bucket.get("window") or bucket["id"]
            seconds, label = _WINDOWS.get(window, (None, window))
            remaining = float(bucket["remaining_fraction"])
            meters.append(
                Meter(
                    key=f"{slugify(group_name)}_{slugify(window)}",
                    name=f"{group_name}, {label}",
                    kind=MeterKind.WINDOW_USED,
                    value=round(100 * (1 - remaining), 1),
                    unit="%",
                    resets_at=None
                    if bucket.get("reset_time") is None
                    else parse_time(bucket["reset_time"]),
                    window_seconds=seconds,
                    group=group_name,
                )
            )
    return meters


def parse_credits(data: dict[str, Any]) -> list[Meter]:
    """Parse ``/credits`` data: remaining AI credits."""
    return [
        Meter(
            key="credits",
            name="AI credits",
            kind=MeterKind.BALANCE,
            value=float(data["remaining_credits"]),
            unit="credits",
        )
    ]
