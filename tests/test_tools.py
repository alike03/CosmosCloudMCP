import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from conftest import TOKEN, call, ok

DESTRUCTIVE = {
    "restart_cosmos", "remove_container", "delete_volume", "delete_network", "delete_route",
    "restore_backup", "forget_snapshot", "create_api_token", "delete_api_token",
}
WRITE = {
    "container_action", "set_container_auto_update", "update_container_settings", "create_service",
    "pull_image", "create_route", "update_route", "run_job", "stop_job",
}


async def tool_names(server):
    return {t.name for t in await server.list_tools()}


async def test_tool_registration_levels(server, ro_server):
    full = await tool_names(server)
    ro = await tool_names(ro_server)
    assert DESTRUCTIVE <= full and WRITE <= full
    assert not (ro & (DESTRUCTIVE | WRITE))
    assert {"server_status", "list_containers", "list_routes", "cosmos_request"} <= ro
    assert 30 <= len(full) <= 50


async def test_annotations(server):
    tools = {t.name: t for t in await server.list_tools()}
    assert tools["list_routes"].annotations.read_only_hint is True
    assert tools["delete_route"].annotations.destructive_hint is True
    assert tools["container_action"].annotations.read_only_hint is False


async def test_auth_header_and_envelope(server, api):
    route = api.get("/api/status").mock(return_value=httpx.Response(200, json=ok({"version": "0.24.0"})))
    assert await call(server, "server_status") == {"version": "0.24.0"}
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"


async def test_list_containers_summary_and_filter(server, api):
    containers = [
        {"Id": "a" * 64, "Names": ["/web"], "Image": "nginx", "State": "running", "Status": "Up 1h", "Labels": {}},
        {"Id": "b" * 64, "Names": ["/db"], "Image": "postgres", "State": "exited", "Status": "Exited"},
    ]
    api.get("/api/servapps").mock(return_value=httpx.Response(200, json=ok(containers)))
    out = await call(server, "list_containers", name_contains="WE")
    assert out == [{"name": "web", "id": "a" * 12, "image": "nginx", "state": "running", "status": "Up 1h"}]
    full = await call(server, "list_containers", full=True)
    assert len(full) == 2 and "Labels" in full[0]


async def test_get_container_redacts_env(server, api):
    api.get("/api/servapps/web/").mock(
        return_value=httpx.Response(200, json=ok({"Config": {"Env": ["SECRET=x"]}}))
    )
    out = await call(server, "get_container", container="web")
    assert out["Config"]["Env"] == ["SECRET=***redacted***"]


async def test_path_segments_are_quoted(server, api):
    route = api.get("/api/routes/a%2Fb").mock(return_value=httpx.Response(200, json=ok({})))
    await call(server, "get_route", name="a/b")
    assert route.called


async def test_logs_params(server, api):
    route = api.get("/api/servapps/web/logs").mock(return_value=httpx.Response(200, json=ok(["line"])))
    await call(server, "get_container_logs", container="web", limit=5000, errors_only=True)
    params = route.calls.last.request.url.params
    assert params["limit"] == "1000" and params["errorOnly"] == "true" and "search" not in params


async def test_container_action(server, api):
    route = api.get("/api/servapps/web/manage/restart").mock(return_value=httpx.Response(200, json=ok("done")))
    assert await call(server, "container_action", container="web", action="restart") == "done"
    assert route.called


async def test_container_action_rejects_remove(server, api):
    with pytest.raises(ToolError):
        await call(server, "container_action", container="web", action="remove")


@pytest.mark.parametrize(
    "tool, args",
    [
        ("remove_container", {"container": "web"}),
        ("delete_volume", {"name": "v"}),
        ("delete_network", {"network_id": "n"}),
        ("delete_route", {"name": "r"}),
        ("forget_snapshot", {"name": "b", "snapshot": "s"}),
        ("restart_cosmos", {}),
        ("create_api_token", {"name": "t", "read_only": True}),
        ("delete_api_token", {"name": "t"}),
    ],
)
async def test_destructive_without_confirm_does_nothing(server, api, tool, args):
    out = await call(server, tool, **args)
    assert out["executed"] is False and out["confirm_required"] is True
    assert len(api.calls) == 0


async def test_remove_container_with_confirm(server, api):
    route = api.get("/api/servapps/web/manage/remove").mock(return_value=httpx.Response(200, json=ok()))
    await call(server, "remove_container", container="web", confirm=True)
    assert route.called


async def test_delete_api_token_sends_body(server, api):
    route = api.delete("/api/api-tokens").mock(return_value=httpx.Response(200, json=ok()))
    await call(server, "delete_api_token", name="old", confirm=True)
    assert json.loads(route.calls.last.request.content) == {"name": "old"}


async def test_create_api_token_returns_raw_token(server, api):
    route = api.post("/api/api-tokens").mock(
        return_value=httpx.Response(200, json=ok({"token": "cosmos_new", "name": "t", "tokenSuffix": "_new"}))
    )
    out = await call(server, "create_api_token", name="t", permissions=[20], expiry_days=30, confirm=True)
    assert out["created"]["token"] == "cosmos_new"
    assert json.loads(route.calls.last.request.content) == {
        "name": "t", "readOnly": False, "permissions": [20], "expiryDays": 30
    }


async def test_restore_preview_uses_backup_config(server, api):
    api.get("/api/backups-config").mock(
        return_value=httpx.Response(
            200, json=ok({"Backups": {"b1": {"Source": "/data/app", "AutoStopContainers": True, "Password": "p"}}})
        )
    )
    restore = api.post("/api/backups/b1/restore")
    out = await call(server, "restore_backup", name="b1", snapshot_id="abc", target="/data/app")
    assert out["executed"] is False
    assert out["details"]["backup_source"] == "/data/app"
    assert out["details"]["auto_stop_containers"] is True
    assert "overwritten" in out["details"]["warning"]
    assert not restore.called


async def test_restore_with_confirm_sends_handler_body(server, api):
    route = api.post("/api/backups/b1/restore").mock(
        return_value=httpx.Response(200, json={"status": "OK", "message": "Restore job created"})
    )
    await call(server, "restore_backup", name="b1", snapshot_id="abc", target="/restore", include=["/x"], confirm=True)
    assert json.loads(route.calls.last.request.content) == {"snapshotId": "abc", "target": "/restore", "include": ["/x"]}


async def test_restore_requires_absolute_target(server, api):
    with pytest.raises(ToolError, match="absolute"):
        await call(server, "restore_backup", name="b1", snapshot_id="abc", target="relative", confirm=True)


async def test_create_route_validation(server, api):
    with pytest.raises(ToolError, match="target"):
        await call(server, "create_route", route={"name": "x"})


async def test_job_body(server, api):
    route = api.post("/api/jobs/run").mock(return_value=httpx.Response(200, json=ok()))
    await call(server, "run_job", scheduler="Restic", name="backup b1")
    assert json.loads(route.calls.last.request.content) == {"scheduler": "Restic", "name": "backup b1"}


async def test_metrics_switches_endpoint(server, api):
    keys = api.get("/api/list-metrics").mock(return_value=httpx.Response(200, json=ok({"k": "l"})))
    values = api.get("/api/metrics").mock(return_value=httpx.Response(200, json=ok([])))
    await call(server, "get_metrics")
    await call(server, "get_metrics", metrics=["a", "b"])
    assert keys.called and values.calls.last.request.url.params["metrics"] == "a,b"


async def test_permission_error_is_tool_error_with_hint(server, api):
    api.get("/api/users").mock(
        return_value=httpx.Response(403, json={"status": "error", "code": "AT003", "message": "API token missing permission: USERS_READ"})
    )
    with pytest.raises(ToolError) as info:
        await call(server, "list_users")
    msg = str(info.value)
    assert "403" in msg and "AT003" in msg and "Settings > API Tokens" in msg
    assert TOKEN not in msg


async def test_transport_error(server, api):
    api.get("/api/status").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ToolError, match="request to Cosmos failed"):
        await call(server, "server_status")


async def test_redirect_is_reported(server, api):
    api.get("/api/status").mock(return_value=httpx.Response(302, headers={"Location": "/login"}))
    with pytest.raises(ToolError, match="redirect"):
        await call(server, "server_status")


# ---------------------------------------------------------------- cosmos_request

async def test_raw_request_read(server, api):
    route = api.get("/api/cron").mock(return_value=httpx.Response(200, json=ok([1])))
    assert await call(server, "cosmos_request", method="GET", path="api/cron") == [1]
    assert route.called


async def test_raw_request_destructive_needs_confirm(server, api):
    out = await call(server, "cosmos_request", method="GET", path="/api/restart-server")
    assert out["executed"] is False
    assert len(api.calls) == 0


async def test_raw_request_read_only_blocks_writes(ro_server, api):
    with pytest.raises(ToolError, match="READ_ONLY"):
        await call(ro_server, "cosmos_request", method="GET", path="/api/servapps/web/manage/stop")
    with pytest.raises(ToolError, match="READ_ONLY"):
        await call(ro_server, "cosmos_request", method="DELETE", path="/api/routes/x", confirm=True)
    assert len(api.calls) == 0


async def test_raw_request_read_only_allows_reads(ro_server, api):
    api.post("/api/jobs/get").mock(return_value=httpx.Response(200, json=ok({})))
    await call(ro_server, "cosmos_request", method="POST", path="/api/jobs/get", body={"name": "a", "scheduler": "b"})


async def test_raw_request_rejects_foreign_url(server, api):
    with pytest.raises(ToolError, match="full URL"):
        await call(server, "cosmos_request", method="GET", path="https://evil.test/api/status")
