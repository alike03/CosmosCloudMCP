"""Streamable HTTP transport with bearer-token authentication.

The MCP endpoint (default ``/mcp``) requires ``Authorization: Bearer <COSMOS_MCP_AUTH_TOKEN>``.
``/health`` answers without authentication and reveals nothing but ``{"status": "ok"}``.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings

log = logging.getLogger("cosmos_cloud_mcp.http")

HEALTH_PATH = "/health"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


async def _send_json(send: Any, status: int, body: dict[str, Any], extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    headers += extra_headers or []
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class BearerAuthMiddleware:
    """ASGI middleware: health endpoint plus constant-time bearer-token check for everything else."""

    def __init__(self, app: Any, token: str, health_path: str = HEALTH_PATH):
        if not token:
            raise ValueError("an auth token is required")
        self.app = app
        self._expected = f"Bearer {token}".encode()
        self._health_path = health_path

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":  # lifespan and others pass through
            await self.app(scope, receive, send)
            return
        if scope.get("path") == self._health_path and scope.get("method") in {"GET", "HEAD"}:
            await _send_json(send, 200, {"status": "ok"})
            return
        provided = b""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"authorization":
                provided = value
                break
        if not hmac.compare_digest(provided, self._expected):
            log.warning("rejected request without valid bearer token: %s %s", scope.get("method"), scope.get("path"))
            await _send_json(
                send,
                401,
                {"error": "unauthorized", "message": "missing or invalid bearer token"},
                [(b"www-authenticate", b'Bearer realm="cosmos-cloud-mcp"')],
            )
            return
        await self.app(scope, receive, send)


def transport_security(settings: Settings) -> TransportSecuritySettings | None:
    """DNS-rebinding protection: automatic for loopback hosts, explicit via COSMOS_MCP_ALLOWED_HOSTS."""
    if settings.http_allowed_hosts:
        hosts = list(settings.http_allowed_hosts)
        origins = [f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")]
        return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)
    return None  # the SDK enables loopback-only protection itself when bound to a loopback host


def build_http_app(server: MCPServer, settings: Settings) -> tuple[Any, Any]:
    """Return ``(asgi_app, starlette_app)``; the Starlette app owns the lifespan."""
    inner = server.streamable_http_app(
        streamable_http_path=settings.http_path,
        stateless_http=True,  # no server-side sessions: clients survive restarts and auto-updates
        json_response=True,
        transport_security=transport_security(settings),
        host=settings.http_host,
    )
    return BearerAuthMiddleware(inner, settings.http_auth_token), inner


def run_http(server: MCPServer, settings: Settings) -> None:
    import uvicorn

    app, _ = build_http_app(server, settings)
    if settings.http_host not in _LOOPBACK:
        log.warning(
            "listening on %s: the MCP endpoint is reachable from the network; keep it on a trusted network "
            "and protect the bearer token", settings.http_host,
        )
    uvicorn.run(app, host=settings.http_host, port=settings.http_port, log_level="info", proxy_headers=False)
