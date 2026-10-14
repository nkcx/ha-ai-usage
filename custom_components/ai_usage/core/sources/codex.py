"""ChatGPT plan limits for Codex, read from the Codex CLI.

``codex app-server`` is the Codex CLI's official JSON-RPC interface (the one
the IDE extensions use). ``account/read`` gives the signed-in account and
``account/rateLimits/read`` the plan's usage windows.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..model import Account, Meter, MeterKind, Status, account_id, parse_time
from ..runner import LineSession
from .base import COMMAND_TIMEOUT, FIVE_HOURS, ONE_WEEK, CliSource, SourceError

APP_SERVER = ("app-server",)
CLIENT_INFO = {
    "name": "ai_usage",
    "title": "AI Usage for Home Assistant",
    "version": "1",
}

#: The default limit; its windows get the plain keys ``primary``/``secondary``.
DEFAULT_LIMIT = "codex"


class RpcError(Exception):
    """A JSON-RPC error response."""

    def __init__(self, error: dict[str, Any]) -> None:
        """Wrap the ``error`` object of a response."""
        super().__init__(error.get("message", "unknown error"))
        self.code = error.get("code")


class CodexSource(CliSource):
    """Codex CLI (ChatGPT Plus / Pro / Business plans)."""

    provider = "codex"
    command = "codex"

    async def _collect(
        self,
    ) -> tuple[Account | None, list[Meter], str | None, dict[str, Any]]:
        async with (
            asyncio.timeout(COMMAND_TIMEOUT),
            self.runner.session((self.command, *APP_SERVER)) as session,
        ):
            rpc = _Rpc(session)
            init = await rpc.call("initialize", {"clientInfo": CLIENT_INFO})
            await rpc.notify("initialized")
            account = parse_account(await rpc.call("account/read", {}))
            try:
                limits = await rpc.call("account/rateLimits/read")
            except RpcError as err:
                raise SourceError(Status.ERROR, f"Codex: {err}") from err
        meters, extra = parse_rate_limits(limits)
        return account, meters, _version(init), extra


class _Rpc:
    def __init__(self, session: LineSession) -> None:
        self._session = session
        self._next_id = 0

    async def notify(self, method: str) -> None:
        await self._session.send(json.dumps({"jsonrpc": "2.0", "method": method}))

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params
        await self._session.send(json.dumps(request))
        while (line := await self._session.readline()) is not None:
            if not line.startswith("{"):
                continue
            message = json.loads(line)
            # Skip notifications and anything that isn't our response.
            if message.get("id") != self._next_id:
                continue
            if message.get("error"):
                raise RpcError(message["error"])
            return message.get("result")
        raise ValueError(f"codex app-server exited before answering {method}")


def _version(init: Any) -> str | None:
    """Pick the Codex version out of the user agent ("ai_usage/0.162.1 (…)")."""
    user_agent = (init or {}).get("userAgent") or ""
    first = user_agent.split(" ", 1)[0]
    return first.split("/", 1)[1] if "/" in first else None


def parse_account(result: dict[str, Any]) -> Account:
    """Parse ``account/read``; only a ChatGPT sign-in has plan limits."""
    account = result.get("account")
    if not account:
        raise SourceError(Status.SIGNED_OUT, "Codex is not signed in")
    if account.get("type") != "chatgpt":
        raise SourceError(
            Status.SIGNED_OUT,
            f"Codex is signed in with {account.get('type')!r}, not a ChatGPT account",
        )
    email = account.get("email")
    return Account(
        id=account_id(email) if email else "chatgpt",
        name=email,
        plan=account.get("planType"),
    )


def parse_rate_limits(result: dict[str, Any]) -> tuple[list[Meter], dict[str, Any]]:
    """Parse ``account/rateLimits/read``."""
    snapshots: dict[str, dict[str, Any]] = result.get("rateLimitsByLimitId") or {}
    if not snapshots:
        main = result["rateLimits"]
        snapshots = {main.get("limitId") or DEFAULT_LIMIT: main}

    meters: list[Meter] = []
    for limit_id, snapshot in snapshots.items():
        label = (
            None if limit_id == DEFAULT_LIMIT else snapshot.get("limitName") or limit_id
        )
        meters.extend(
            _window_meter(limit_id, label, slot, window)
            for slot in ("primary", "secondary")
            if (window := snapshot.get(slot))
        )
        credit = snapshot.get("credits") or {}
        if (
            limit_id == DEFAULT_LIMIT
            and credit.get("hasCredits")
            and not credit.get("unlimited")
            and credit.get("balance") is not None
        ):
            meters.append(
                Meter(
                    key="credits",
                    name="Credits",
                    kind=MeterKind.BALANCE,
                    value=float(credit["balance"]),
                    unit="credits",
                )
            )
    main = snapshots.get(DEFAULT_LIMIT) or next(iter(snapshots.values()))
    extra = {
        "limit_reached": main.get("rateLimitReachedType"),
        "spend_control_reached": main.get("spendControlReached"),
    }
    return meters, extra


def _window_meter(
    limit_id: str, label: str | None, slot: str, window: dict[str, Any]
) -> Meter:
    minutes = window.get("windowDurationMins")
    seconds = None if minutes is None else int(minutes) * 60
    name = (
        _window_name(seconds)
        if label is None
        else f"{label}, {_window_name(seconds).lower()}"
    )
    return Meter(
        key=slot if limit_id == DEFAULT_LIMIT else f"{limit_id}_{slot}",
        name=name,
        kind=MeterKind.WINDOW_USED,
        value=float(window["usedPercent"]),
        unit="%",
        resets_at=None
        if window.get("resetsAt") is None
        else parse_time(window["resetsAt"]),
        window_seconds=seconds,
        group=limit_id,
    )


def _window_name(seconds: int | None) -> str:
    if seconds == FIVE_HOURS:
        return "5-hour"
    if seconds == ONE_WEEK:
        return "Week"
    if seconds is None:
        return "Limit"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}-day"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}-hour"
    return f"{seconds // 60}-minute"
