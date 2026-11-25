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
    "container_action", "set_container_auto_update", "update_container_settings", "set_container_env",
    "set_container_mounts", "create_service", "pull_image", "create_route", "update_route", "run_job",
    "stop_job", "set_cron_job",
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


ENV = ["PATH=/usr/bin", "ADMIN_TOKEN=s3cr3t-value", "PORT=3909", "FLAG"]


def mock_env(api, env=ENV):
    api.get("/api/servapps/web/").mock(return_value=httpx.Response(200, json=ok({"Config": {"Env": env}})))
    return api.post("/api/servapps/web/update").mock(return_value=httpx.Response(200, json=ok("updated")))


async def test_set_container_env_merges_and_hides_values(server, api):
    update = mock_env(api)
    out = await call(server, "set_container_env", container="web", values={"PORT": "8080", "NEW": "1"}, remove=["FLAG"])
    body = json.loads(update.calls.last.request.content)
    assert body == {"envVars": ["PATH=/usr/bin", "ADMIN_TOKEN=s3cr3t-value", "PORT=8080", "NEW=1"]}
    assert out["changed"] == ["PORT"] and out["added"] == ["NEW"] and out["removed"] == ["FLAG"]
    assert out["applied"] is True and "s3cr3t" not in json.dumps(out)


async def test_set_container_env_no_change_skips_update(server, api):
    update = mock_env(api)
    out = await call(server, "set_container_env", container="web", values={"PORT": "3909"}, remove=["MISSING"])
    assert out["applied"] is False and out["unchanged"] == ["PORT"] and out["not_found"] == ["MISSING"]
    assert not update.called


@pytest.mark.parametrize(
    "args",
    [{}, {"values": {"A=B": "x"}}, {"values": {"A": "x"}, "remove": ["A"]}, {"remove": [""]}],
)
async def test_set_container_env_rejects_bad_input(server, api, args):
    update = mock_env(api)
    with pytest.raises(ToolError):
        await call(server, "set_container_env", container="web", **args)
    assert not update.called


INSPECT_MOUNTS = {
    "HostConfig": {
        "Mounts": [
            {"Type": "bind", "Source": "/srv/data", "Target": "/data"},
            {"Type": "bind", "Source": "/srv/old", "Target": "/old", "ReadOnly": True},
        ],
        "Binds": ["/srv/conf:/config:ro,z", "cache:/cache"],
    }
}


def mock_mounts(api):
    api.get("/api/servapps/web/").mock(return_value=httpx.Response(200, json=ok(INSPECT_MOUNTS)))
    return api.post("/api/servapps/web/update").mock(return_value=httpx.Response(200, json=ok("updated")))


async def test_set_container_mounts_keeps_binds_and_merges(server, api):
    update = mock_mounts(api)
    out = await call(
        server, "set_container_mounts", container="web",
        add=[{"source": "/srv/new", "target": "/new", "read_only": True},
             {"source": "/srv/data2", "target": "/data/"}],
        remove=["/old"],
    )
    body = json.loads(update.calls.last.request.content)
    assert body == {"Volumes": [
        {"Type": "bind", "Source": "/srv/data2", "Target": "/data", "ReadOnly": False},
        {"Type": "bind", "Source": "/srv/conf", "Target": "/config", "ReadOnly": True},
        {"Type": "volume", "Source": "cache", "Target": "/cache", "ReadOnly": False},
        {"Type": "bind", "Source": "/srv/new", "Target": "/new", "ReadOnly": True},
    ]}
    assert out["replaced"] == ["/data"] and out["added"] == ["/new"] and out["removed"] == ["/old"]
    assert out["converted_binds"] == 2 and out["applied"] is True


async def test_set_container_mounts_no_change_skips_update(server, api):
    update = mock_mounts(api)
    out = await call(server, "set_container_mounts", container="web",
                     add=[{"source": "/srv/data", "target": "/data"}], remove=["/missing"])
    assert out["applied"] is False and out["unchanged"] == ["/data"] and out["not_found"] == ["/missing"]
    assert not update.called


@pytest.mark.parametrize(
    "args",
    [{}, {"add": [{"source": "rel/path", "target": "/x"}]}, {"add": [{"source": "/a", "target": "x"}]},
     {"add": [{"source": "/a", "target": "/x"}], "remove": ["/x"]}, {"add": [{"source": "/a", "target": "/"}]}],
)
async def test_set_container_mounts_rejects_bad_input(server, api, args):
    update = mock_mounts(api)
    with pytest.raises(ToolError):
        await call(server, "set_container_mounts", container="web", **args)
    assert not update.called


CRON = {"nightly": {"Enabled": True, "Name": "nightly", "Crontab": "0 0 3 * * *", "Command": "/x.sh", "Container": ""}}


def mock_cron(api, jobs=CRON):
    api.get("/api/cron").mock(return_value=httpx.Response(200, json=ok(jobs)))
    create = api.post("/api/cron").mock(return_value=httpx.Response(200, json=ok({})))
    update = api.put("/api/cron/nightly").mock(return_value=httpx.Response(200, json=ok({})))
    return create, update


async def test_set_cron_job_create_needs_confirm(server, api):
    create, _ = mock_cron(api, {})
    out = await call(server, "set_cron_job", name="nightly", crontab="0 0 4 * * *", command="/y.sh")
    assert out["executed"] is False and "as root on the Cosmos host" in out["would"]
    assert not create.called
    out = await call(server, "set_cron_job", name="nightly", crontab="0 0 4 * * *", command="/y.sh", confirm=True)
    assert json.loads(create.calls.last.request.content) == {
        "Enabled": True, "Name": "nightly", "Crontab": "0 0 4 * * *", "Command": "/y.sh", "Container": ""}
    assert out["applied"] is True and out["action"] == "create"


async def test_set_cron_job_schedule_change_keeps_other_fields(server, api):
    _, update = mock_cron(api)
    out = await call(server, "set_cron_job", name="nightly", crontab="0 30 5 * * *")
    assert json.loads(update.calls.last.request.content) == {
        "Enabled": True, "Name": "nightly", "Crontab": "0 30 5 * * *", "Command": "/x.sh", "Container": ""}
    assert out["applied"] is True


async def test_set_cron_job_command_change_needs_confirm(server, api):
    _, update = mock_cron(api)
    out = await call(server, "set_cron_job", name="nightly", command="/z.sh")
    assert out["executed"] is False and not update.called


async def test_set_cron_job_no_change_and_validation(server, api):
    create, update = mock_cron(api)
    out = await call(server, "set_cron_job", name="nightly", crontab="0 0 3 * * *", enabled=True)
    assert out["applied"] is False
    with pytest.raises(ToolError, match="six fields"):
        await call(server, "set_cron_job", name="nightly", crontab="0 3 * * *")
    with pytest.raises(ToolError, match="required"):
        await call(server, "set_cron_job", name="other", crontab="0 0 1 * * *")
    assert not create.called and not update.called


async def test_path_segments_are_quoted(server, api):
    route = api.get("/api/routes/a%2Fb").mock(return_value=httpx.Response(200, json=ok({})))
    await call(server, "get_route", name="a/b")
    assert route.called


async def test_dot_segment_identifiers_are_rejected(server, api):
    with pytest.raises(ToolError, match="'.' or '..'"):
        await call(server, "forget_snapshot", name="b1", snapshot="..", confirm=True)
    assert len(api.calls) == 0


async def test_cosmos_request_keeps_trailing_slash(server, api):
    route = api.get("/api/servapps/web/").mock(return_value=httpx.Response(200, json=ok({})))
    await call(server, "cosmos_request", method="GET", path="/api/servapps/web/")
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
    assert "403" in msg and "AT003" in msg and "Management > Configuration > API" in msg
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


async def test_streamed_output_is_shortened(server, api):
    stream = "\n".join(f'{{"status":"Downloading","id":"{i}"}}' for i in range(100)) + "\nDone"
    api.get("/api/images/pull").mock(return_value=httpx.Response(200, text=stream))
    out = await call(server, "pull_image", image="nginx:latest")
    lines = out.splitlines()
    assert lines[0].startswith("... 86 earlier lines omitted") and lines[-1] == "Done" and len(lines) == 16
