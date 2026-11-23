from __future__ import annotations

import json
from typing import Any

import pytest
import respx

from cosmos_cloud_mcp.config import Settings
from cosmos_cloud_mcp.server import build_server

BASE = "https://cosmos.test/cosmos"
TOKEN = "cosmos_unit_test_token"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"url": "https://cosmos.test", "token": TOKEN}
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def server():
    return build_server(make_settings())


@pytest.fixture
def ro_server():
    return build_server(make_settings(read_only=True))


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"status": "OK"}
    if data is not None:
        body["data"] = data
    body.update(extra)
    return body


def text_of(result: Any) -> str:
    """Extract the text content of an MCPServer.call_tool result (CallToolResult)."""
    assert not result.is_error
    return "".join(getattr(block, "text", "") for block in result.content)


async def call(server, tool_name: str, /, **args: Any) -> Any:
    raw = text_of(await server.call_tool(tool_name, args))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
