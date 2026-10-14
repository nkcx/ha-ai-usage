"""Client for the AI Usage app's API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from .core.model import UsageReport, parse_time

TIMEOUT = ClientTimeout(total=20)


class AppError(Exception):
    """Talking to the app failed."""


class AppConnectionError(AppError):
    """The app can't be reached or answered with something unexpected."""


class AppAuthError(AppError):
    """The app rejected the token."""


@dataclass(frozen=True, kw_only=True)
class ProviderState:
    """One provider as the app sees it."""

    enabled: bool
    installed: bool
    install_error: str | None = None
    report: UsageReport | None = None


@dataclass(frozen=True, kw_only=True)
class AppSnapshot:
    """Everything the app reports."""

    instance_id: str
    app_version: str
    interval_minutes: int
    updated_at: datetime | None
    providers: Mapping[str, ProviderState] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AppSnapshot:
        """Parse the ``/api/v1/reports`` response; ``ValueError`` if malformed."""
        try:
            if data["schema"] != 1:
                raise ValueError(f"Unsupported app API schema {data['schema']!r}")
            providers = {
                name: ProviderState(
                    enabled=bool(state["enabled"]),
                    installed=bool(state["installed"]),
                    install_error=state.get("install_error"),
                    report=None
                    if state.get("report") is None
                    else UsageReport.from_dict(state["report"]),
                )
                for name, state in data["providers"].items()
            }
            return cls(
                instance_id=str(data["instance_id"]),
                app_version=str(data["app_version"]),
                interval_minutes=int(data["interval_minutes"]),
                updated_at=None
                if data.get("updated_at") is None
                else parse_time(data["updated_at"]),
                providers=providers,
            )
        except (KeyError, TypeError, AttributeError) as err:
            raise ValueError(f"Malformed app response: {err!r}") from err


class AppClient:
    """Reads usage from the AI Usage app."""

    def __init__(
        self, session: ClientSession, host: str, port: int, token: str
    ) -> None:
        """Talk to the app at ``host:port`` with ``token``."""
        self._session = session
        self._base = f"http://{host}:{port}/api/v1"
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                headers=self._headers,
                timeout=TIMEOUT,
                raise_for_status=True,
            ) as response:
                return await response.json()
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise AppAuthError("The app rejected the token") from err
            raise AppConnectionError(f"The app answered HTTP {err.status}") from err
        except (ClientError, TimeoutError, ValueError) as err:
            raise AppConnectionError(f"Can't reach the app: {err!r}") from err

    async def get_snapshot(self) -> AppSnapshot:
        """Return the latest reports of every provider."""
        data = await self._request("GET", "/reports")
        try:
            return AppSnapshot.from_dict(data)
        except ValueError as err:
            raise AppConnectionError(str(err)) from err

    async def refresh(self) -> None:
        """Ask the app to collect again now."""
        await self._request("POST", "/refresh")
