"""HTTP servers: the usage API and the ingress page with its sign-in terminal.

* API (port 8080, internal network only): ``GET /api/v1/reports``,
  ``GET /api/v1/health``, ``POST /api/v1/refresh``. Bearer token required.
* Ingress (port 8099): only Home Assistant's ingress proxy may connect. Serves
  the status page and proxies ``/term/`` to ttyd on localhost, where each CLI's
  own sign-in runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import hmac
import logging

from aiohttp import ClientSession, WSMsgType, web

from . import __version__
from .collector import Collector
from .settings import INGRESS_PROXY, TTYD_PORT
from .ui import render_page

_LOGGER = logging.getLogger(__name__)

PROVIDERS_KEY = web.AppKey("collector", Collector)
TOKEN_KEY = web.AppKey("token", str)
INSTANCE_KEY = web.AppKey("instance_id", str)
TTYD_KEY = web.AppKey("ttyd", str)
SESSION_KEY = web.AppKey("session", ClientSession)

type Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
}


# Usage API


@web.middleware
async def require_token(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Reject API calls without the bearer token."""
    expected = f"Bearer {request.app[TOKEN_KEY]}"
    if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
        raise web.HTTPUnauthorized(text="Invalid or missing token")
    return await handler(request)


async def reports(request: web.Request) -> web.Response:
    """Return the latest report of every provider."""
    collector = request.app[PROVIDERS_KEY]
    return web.json_response(
        {
            "schema": 1,
            "instance_id": request.app[INSTANCE_KEY],
            "app_version": __version__,
            "updated_at": None
            if collector.updated_at is None
            else collector.updated_at.isoformat(),
            "interval_minutes": collector.options.interval_minutes,
            "providers": collector.snapshot(),
        }
    )


async def health(request: web.Request) -> web.Response:
    """Return liveness information."""
    return web.json_response(
        {
            "status": "ok",
            "instance_id": request.app[INSTANCE_KEY],
            "app_version": __version__,
        }
    )


async def refresh(request: web.Request) -> web.Response:
    """Collect again now."""
    request.app[PROVIDERS_KEY].request_refresh()
    return web.json_response({"status": "scheduled"}, status=202)


def api_app(collector: Collector, token: str, instance_id: str) -> web.Application:
    """Build the usage API."""
    app = web.Application(middlewares=[require_token])
    app[PROVIDERS_KEY] = collector
    app[TOKEN_KEY] = token
    app[INSTANCE_KEY] = instance_id
    app.router.add_get("/api/v1/reports", reports)
    app.router.add_get("/api/v1/health", health)
    app.router.add_post("/api/v1/refresh", refresh)
    return app


# Ingress


@web.middleware
async def ingress_only(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Only Home Assistant's ingress proxy may use the page and terminal."""
    if request.remote != INGRESS_PROXY:
        raise web.HTTPForbidden(text="Use the AI Usage page in Home Assistant")
    return await handler(request)


async def index(request: web.Request) -> web.Response:
    """Render the status page."""
    collector = request.app[PROVIDERS_KEY]
    return web.Response(
        text=render_page(collector.snapshot(), collector.options.interval_minutes),
        content_type="text/html",
    )


async def ingress_refresh(request: web.Request) -> web.Response:
    """Collect again now, then go back to the page."""
    request.app[PROVIDERS_KEY].request_refresh()
    raise web.HTTPSeeOther(location="./")


async def terminal(request: web.Request) -> web.StreamResponse:
    """Proxy the ttyd terminal (HTTP and its WebSocket)."""
    target = f"{request.app[TTYD_KEY]}{request.rel_url}"
    session = request.app[SESSION_KEY]
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await _proxy_websocket(request, session, target)
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS
    }
    async with session.request(
        request.method, target, headers=headers, data=await request.read()
    ) as upstream:
        body = await upstream.read()
        response_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_HEADERS
        }
        return web.Response(status=upstream.status, body=body, headers=response_headers)


async def _proxy_websocket(
    request: web.Request, session: ClientSession, target: str
) -> web.WebSocketResponse:
    protocols = [
        p.strip()
        for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if p
    ]
    client = web.WebSocketResponse(protocols=protocols)
    await client.prepare(request)
    async with session.ws_connect(target, protocols=protocols) as upstream:

        async def client_to_upstream() -> None:
            async for message in client:
                if message.type is WSMsgType.TEXT:
                    await upstream.send_str(message.data)
                elif message.type is WSMsgType.BINARY:
                    await upstream.send_bytes(message.data)

        async def upstream_to_client() -> None:
            async for message in upstream:
                if message.type is WSMsgType.TEXT:
                    await client.send_str(message.data)
                elif message.type is WSMsgType.BINARY:
                    await client.send_bytes(message.data)

        tasks = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    await client.close()
    return client


async def _session_ctx(app: web.Application) -> AsyncIterator[None]:
    app[SESSION_KEY] = ClientSession(auto_decompress=False)
    yield
    await app[SESSION_KEY].close()


def ingress_app(collector: Collector, ttyd_url: str | None = None) -> web.Application:
    """Build the ingress app."""
    app = web.Application(middlewares=[ingress_only])
    app[PROVIDERS_KEY] = collector
    app[TTYD_KEY] = ttyd_url or f"http://127.0.0.1:{TTYD_PORT}"
    app.cleanup_ctx.append(_session_ctx)
    app.router.add_get("/", index)
    app.router.add_post("/refresh", ingress_refresh)
    app.router.add_route("*", "/term/{tail:.*}", terminal)
    return app
