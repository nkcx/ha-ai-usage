"""Fixtures for the app tests: the app package and its core copy on sys.path."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

APP_ROOT = (
    Path(__file__).resolve().parents[2] / "ai_usage_app" / "rootfs" / "opt" / "ai_usage"
)
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from ai_usage_app.settings import Paths  # noqa: E402


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    """App paths in a temporary /data."""
    app_paths = Paths(tmp_path / "data")
    app_paths.ensure()
    return app_paths


@pytest.fixture(autouse=True)
def allow_local_sockets(socket_enabled: None) -> None:
    """Allow sockets: the app tests run real local HTTP servers."""
