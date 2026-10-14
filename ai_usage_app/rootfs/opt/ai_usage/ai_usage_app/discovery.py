"""Announcing the app to Home Assistant through the Supervisor."""

from __future__ import annotations

import logging
import os
import socket

from aiohttp import ClientError, ClientSession, ClientTimeout

from .settings import API_PORT

_LOGGER = logging.getLogger(__name__)

SUPERVISOR = "http://supervisor"
SERVICE = "ai_usage"


async def announce(token: str, session: ClientSession) -> bool:
    """Send (or refresh) the discovery message for the AI Usage integration.

    Home Assistant then offers to set up the integration, already pointed at
    this app's API with its token.
    """
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        _LOGGER.info("Not running under the Supervisor; skipping discovery")
        return False
    payload = {
        "service": SERVICE,
        "config": {"host": socket.gethostname(), "port": API_PORT, "token": token},
    }
    try:
        async with session.post(
            f"{SUPERVISOR}/discovery",
            json=payload,
            headers={"Authorization": f"Bearer {supervisor_token}"},
            timeout=ClientTimeout(total=30),
        ) as response:
            if response.status != 200:
                _LOGGER.error(
                    "Discovery failed: HTTP %s %s",
                    response.status,
                    await response.text(),
                )
                return False
    except (ClientError, TimeoutError) as err:
        _LOGGER.error("Discovery failed: %s", err)
        return False
    _LOGGER.info("Announced the app to Home Assistant")
    return True
