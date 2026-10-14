"""Provider-neutral usage model shared by the integration and the app.

Every source, whether it reads an official API or an official CLI, produces a
``UsageReport``. The integration builds devices and entities from this model
only, so adding a provider never needs entity code. The JSON form (``to_dict``
/ ``from_dict``) is the contract between the app and the integration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import re
from typing import Any

SCHEMA_VERSION = 1


class Status(StrEnum):
    """Outcome of one collection run."""

    OK = "ok"
    SIGNED_OUT = "signed_out"
    NOT_INSTALLED = "not_installed"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class SourceType(StrEnum):
    """How a source gets its data."""

    API = "api"
    CLI = "cli"


class MeterKind(StrEnum):
    """What a meter measures."""

    WINDOW_USED = "window_used"
    """Percentage of a usage window used (0 to 100), usually with a reset time."""

    QUOTA_REMAINING = "quota_remaining"
    """Amount left of a quota, in ``unit``."""

    BALANCE = "balance"
    """Prepaid credit left, in ``unit`` (a currency code or "credits")."""

    SPEND = "spend"
    """Amount spent in the current period, in ``unit``."""


@dataclass(frozen=True, kw_only=True)
class Account:
    """The account a report belongs to."""

    id: str
    """Stable identifier. Never an email address; see ``account_id``."""

    name: str | None = None
    plan: str | None = None


@dataclass(frozen=True, kw_only=True)
class Meter:
    """One measured value, such as "weekly limit, 46 % used"."""

    key: str
    """Stable within the provider; becomes part of the entity unique ID."""

    name: str
    kind: MeterKind
    value: float | None
    unit: str
    limit: float | None = None
    resets_at: datetime | None = None
    window_seconds: int | None = None
    group: str | None = None
    model: str | None = None
    active: bool | None = None


@dataclass(frozen=True, kw_only=True)
class UsageReport:
    """The result of collecting usage from one provider."""

    provider: str
    source: SourceType
    status: Status
    collected_at: datetime
    account: Account | None = None
    meters: tuple[Meter, ...] = ()
    error: str | None = None
    version: str | None = None
    """Version of the CLI or API that produced the report, when known."""

    extra: Mapping[str, Any] = field(default_factory=dict)
    """Provider details that aren't meters (shown in diagnostics only)."""

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form."""
        return {
            "schema": SCHEMA_VERSION,
            "provider": self.provider,
            "source": self.source.value,
            "status": self.status.value,
            "collected_at": self.collected_at.isoformat(),
            "account": None
            if self.account is None
            else {
                "id": self.account.id,
                "name": self.account.name,
                "plan": self.account.plan,
            },
            "meters": [_meter_to_dict(meter) for meter in self.meters],
            "error": self.error,
            "version": self.version,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UsageReport:
        """Parse the JSON form; raises ``ValueError`` on anything malformed."""
        try:
            if data["schema"] != SCHEMA_VERSION:
                raise ValueError(f"Unsupported report schema {data['schema']!r}")
            account = data.get("account")
            return cls(
                provider=str(data["provider"]),
                source=SourceType(data["source"]),
                status=Status(data["status"]),
                collected_at=parse_time(data["collected_at"]),
                account=None
                if account is None
                else Account(
                    id=str(account["id"]),
                    name=account.get("name"),
                    plan=account.get("plan"),
                ),
                meters=tuple(_meter_from_dict(meter) for meter in data["meters"]),
                error=data.get("error"),
                version=data.get("version"),
                extra=dict(data.get("extra") or {}),
            )
        except (KeyError, TypeError, AttributeError) as err:
            raise ValueError(f"Malformed usage report: {err!r}") from err


def _meter_to_dict(meter: Meter) -> dict[str, Any]:
    return {
        "key": meter.key,
        "name": meter.name,
        "kind": meter.kind.value,
        "value": meter.value,
        "unit": meter.unit,
        "limit": meter.limit,
        "resets_at": None if meter.resets_at is None else meter.resets_at.isoformat(),
        "window_seconds": meter.window_seconds,
        "group": meter.group,
        "model": meter.model,
        "active": meter.active,
    }


def _meter_from_dict(data: Mapping[str, Any]) -> Meter:
    resets_at = data.get("resets_at")
    return Meter(
        key=str(data["key"]),
        name=str(data["name"]),
        kind=MeterKind(data["kind"]),
        value=None if data.get("value") is None else float(data["value"]),
        unit=str(data["unit"]),
        limit=None if data.get("limit") is None else float(data["limit"]),
        resets_at=None if resets_at is None else parse_time(resets_at),
        window_seconds=data.get("window_seconds"),
        group=data.get("group"),
        model=data.get("model"),
        active=data.get("active"),
    )


def parse_time(value: Any) -> datetime:
    """Parse an ISO 8601 string or Unix timestamp into an aware UTC datetime."""
    if isinstance(value, bool):
        raise ValueError(f"Not a timestamp: {value!r}")
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(f"Timestamp without a time zone: {value!r}")
        return parsed.astimezone(UTC)
    raise ValueError(f"Not a timestamp: {value!r}")


def slugify(text: str) -> str:
    """Return a lowercase ``a-z0-9_`` slug for meter keys."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "unnamed"


def account_id(identity: str) -> str:
    """Derive a stable account ID from an identity such as an email address.

    Hashed, so unique IDs, diagnostics and the app API never carry the
    address itself.
    """
    return hashlib.sha256(identity.strip().lower().encode()).hexdigest()[:16]
