"""Tests for the usage model."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ai_usage.core.model import (
    SCHEMA_VERSION,
    Account,
    Meter,
    MeterKind,
    SourceType,
    Status,
    UsageReport,
    account_id,
    parse_time,
    slugify,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def full_report() -> UsageReport:
    return UsageReport(
        provider="claude",
        source=SourceType.CLI,
        status=Status.OK,
        collected_at=NOW,
        account=Account(id="abc", name="user@example.com", plan="max"),
        meters=(
            Meter(
                key="session",
                name="Session",
                kind=MeterKind.WINDOW_USED,
                value=27.0,
                unit="%",
                resets_at=NOW + timedelta(hours=2),
                window_seconds=18000,
                group="session",
                active=True,
            ),
            Meter(
                key="extra_usage",
                name="Extra usage",
                kind=MeterKind.SPEND,
                value=1.5,
                unit="USD",
                limit=100.0,
            ),
            Meter(
                key="x", name="X", kind=MeterKind.BALANCE, value=None, unit="credits"
            ),
        ),
        version="2.1.296",
        extra={"a": 1},
    )


def test_round_trip() -> None:
    report = full_report()
    data = report.to_dict()
    assert data["schema"] == SCHEMA_VERSION
    assert data["meters"][0]["resets_at"] == "2026-10-14T14:00:00+00:00"
    assert UsageReport.from_dict(data) == report


def test_round_trip_minimal() -> None:
    report = UsageReport(
        provider="gemini",
        source=SourceType.CLI,
        status=Status.NOT_INSTALLED,
        collected_at=NOW,
        error="agy is not installed",
    )
    assert UsageReport.from_dict(report.to_dict()) == report


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 99},
        {"provider": None, "meters": None},
        {"status": "weird"},
        {"account": {"name": "no id"}},
        {"meters": [{"key": "k"}]},
    ],
)
def test_from_dict_invalid(change: dict[str, object]) -> None:
    data = full_report().to_dict() | change
    with pytest.raises(ValueError, match=r"report|weird"):
        UsageReport.from_dict(data)


def test_from_dict_missing_key() -> None:
    data = full_report().to_dict()
    del data["schema"]
    with pytest.raises(ValueError, match="Malformed"):
        UsageReport.from_dict(data)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-14T21:35:07Z", datetime(2026, 10, 14, 21, 35, 7, tzinfo=UTC)),
        (
            "2026-10-14T14:35:07-07:00",
            datetime(2026, 10, 14, 21, 35, 7, tzinfo=UTC),
        ),
        (1792027800, datetime.fromtimestamp(1792027800, UTC)),
        (1792027800.5, datetime.fromtimestamp(1792027800.5, UTC)),
    ],
)
def test_parse_time(value: object, expected: datetime) -> None:
    parsed = parse_time(value)
    assert parsed == expected
    assert parsed.tzinfo is UTC


@pytest.mark.parametrize("value", ["2026-10-14T21:35:07", True, None, [1]])
def test_parse_time_invalid(value: object) -> None:
    with pytest.raises(ValueError, match="imestamp"):
        parse_time(value)


def test_parse_time_converts_to_utc() -> None:
    parsed = parse_time("2026-10-14T23:35:07+02:00")
    assert parsed.utcoffset() == UTC.utcoffset(None)


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Gemini Models", "gemini_models"),
        ("Claude and GPT models", "claude_and_gpt_models"),
        ("  GPT-5.5 Spark!", "gpt_5_5_spark"),
        ("!!!", "unnamed"),
    ],
)
def test_slugify(text: str, slug: str) -> None:
    assert slugify(text) == slug


def test_account_id() -> None:
    assert account_id("User@Example.com ") == account_id("user@example.com")
    assert len(account_id("user@example.com")) == 16
    assert "@" not in account_id("user@example.com")
