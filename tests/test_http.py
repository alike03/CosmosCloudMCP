import httpx
import pytest

from conftest import make_settings
from cosmos_cloud_mcp.config import ConfigError, Settings
from cosmos_cloud_mcp.http_transport import BearerAuthMiddleware, build_http_app
from cosmos_cloud_mcp.server import build_server

AUTH = "test-auth-token-0123456789abcdef"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
}

BASE_ENV = {"COSMOS_URL": "https://c.test", "COSMOS_TOKEN": "cosmos_abc"}


# ------------------------------------------------------------------ settings

def test_http_settings():
    s = Settings.from_env({**BASE_ENV, "COSMOS_MCP_TRANSPORT": "http", "COSMOS_MCP_AUTH_TOKEN": AUTH,
                           "COSMOS_MCP_PORT": "9000", "COSMOS_MCP_ALLOWED_HOSTS": "mcp.lan:9000, 10.0.0.5:9000"})
    assert (s.transport, s.http_host, s.http_port, s.http_path) == ("http", "127.0.0.1", 9000, "/mcp")
    assert s.http_allowed_hosts == ("mcp.lan:9000", "10.0.0.5:9000")
    assert AUTH not in repr(s)


def test_stdio_is_default_and_needs_no_auth_token():
    assert Settings.from_env(BASE_ENV).transport == "stdio"


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"COSMOS_MCP_TRANSPORT": "http"}, "required"),
        ({"COSMOS_MCP_TRANSPORT": "http", "COSMOS_MCP_AUTH_TOKEN": "short"}, "at least"),
        ({"COSMOS_MCP_TRANSPORT": "sse"}, "stdio or http"),
        ({"COSMOS_MCP_PORT": "99999"}, "between"),
        ({"COSMOS_MCP_PATH": "/health"}, "must not be /health"),
    ],
)
def test_http_settings_errors(extra, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env({**BASE_ENV, **extra})


# ---------------------------------------------------------------- middleware

async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _client(app, host="127.0.0.1:8000"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}")


async def test_middleware_health_without_auth():
    async with _client(BearerAuthMiddleware(_ok_app, AUTH)) as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Basic {AUTH}", f"Bearer {AUTH}x", f"bearer{AUTH}"])
async def test_middleware_rejects(header):
    headers = {"Authorization": header} if header else {}
    async with _client(BearerAuthMiddleware(_ok_app, AUTH)) as c:
        r = await c.post("/mcp", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert AUTH not in r.text


async def test_middleware_accepts_valid_token():
    async with _client(BearerAuthMiddleware(_ok_app, AUTH)) as c:
        r = await c.post("/mcp", headers={"Authorization": f"Bearer {AUTH}"})
    assert r.status_code == 204


def test_middleware_requires_token():
    with pytest.raises(ValueError):
        BearerAuthMiddleware(_ok_app, "")


# ------------------------------------------------------------- full MCP app

def _http_settings(**overrides):
    values = {"transport": "http", "http_auth_token": AUTH}
    values.update(overrides)
    return make_settings(**values)


async def _mcp_post(settings, body, host="127.0.0.1:8000", auth=AUTH):
    app, inner = build_http_app(build_server(settings), settings)
    headers = dict(MCP_HEADERS)
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    async with inner.router.lifespan_context(inner):
        async with _client(app, host) as c:
            return await c.post(settings.http_path, json=body, headers=headers)


async def test_mcp_initialize_over_http():
    r = await _mcp_post(_http_settings(), INIT)
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "cosmos-cloud"


async def test_mcp_tools_list_over_http_respects_read_only():
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    full = await _mcp_post(_http_settings(), body)
    ro = await _mcp_post(_http_settings(read_only=True), body)
    full_names = {t["name"] for t in full.json()["result"]["tools"]}
    ro_names = {t["name"] for t in ro.json()["result"]["tools"]}
    assert "remove_container" in full_names and "remove_container" not in ro_names


async def test_mcp_without_token_is_rejected():
    r = await _mcp_post(_http_settings(), INIT, auth=None)
    assert r.status_code == 401


async def test_dns_rebinding_protection_on_loopback():
    r = await _mcp_post(_http_settings(), INIT, host="evil.example:8000")
    assert r.status_code in (400, 403, 421)


async def test_allowed_hosts_for_lan_binding():
    s = _http_settings(http_host="0.0.0.0", http_allowed_hosts=("mcp.lan:8000",))
    assert (await _mcp_post(s, INIT, host="mcp.lan:8000")).status_code == 200
    assert (await _mcp_post(s, INIT, host="evil.example:8000")).status_code in (400, 403, 421)
