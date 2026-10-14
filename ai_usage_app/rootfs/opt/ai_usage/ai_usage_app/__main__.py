"""Run the AI Usage app."""

from __future__ import annotations

import asyncio
import logging
import secrets

from aiohttp import ClientSession, web

from ai_usage_core.runner import SubprocessRunner

from . import __version__
from .collector import Collector
from .discovery import announce
from .installer import Installer
from .server import api_app, ingress_app
from .settings import API_PORT, INGRESS_PORT, Options, Paths, default_paths

_LOGGER = logging.getLogger("ai_usage_app")


def load_token(paths: Paths) -> str:
    """Return the API token, creating it on first start."""
    if paths.token.exists():
        return paths.token.read_text().strip()
    token = secrets.token_urlsafe(32)
    paths.token.write_text(token)
    paths.token.chmod(0o600)
    return token


async def main() -> None:
    """Start the servers, announce the app, then collect forever."""
    paths = default_paths()
    paths.ensure()
    options = Options.load(paths)
    _LOGGER.info(
        "AI Usage app %s: providers %s, every %d min",
        __version__,
        ", ".join(options.enabled) or "none (turn them on in the Configuration tab)",
        options.interval_minutes,
    )
    runner = SubprocessRunner(paths.env(), cwd=str(paths.work))
    collector = Collector(options, paths, runner, Installer(paths, runner))
    token = load_token(paths)

    runners = []
    for app, port in (
        (api_app(collector, token), API_PORT),
        (ingress_app(collector), INGRESS_PORT),
    ):
        app_runner = web.AppRunner(app, access_log=None)
        await app_runner.setup()
        await web.TCPSite(app_runner, "0.0.0.0", port).start()
        runners.append(app_runner)

    async with ClientSession() as session:
        await announce(token, session)
    try:
        await collector.run()
    finally:
        for app_runner in runners:
            await app_runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())
