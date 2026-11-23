"""MCP server (official Python SDK v2, MCPServer) exposing the Cosmos Cloud REST API as tools.

Three safety levels:

* read tools are always registered;
* write tools are registered unless ``COSMOS_READ_ONLY=true``; the MCP client's own
  permission prompt is the approval;
* destructive tools are registered unless read-only and do nothing without ``confirm=true``:
  they return a description of what would happen instead.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .client import CosmosAPIError, CosmosClient
from .config import ConfigError, Settings
from .redact import redact
from .safety import Level, classify, normalize_path, preview

INSTRUCTIONS = """\
Tools for a Cosmos Cloud server (self-hosted server manager by azukaar, not Azure Cosmos DB).
Read tools are safe. Write tools change containers, routes or jobs. Destructive tools
(remove container, delete volume/network/route, restore backup, forget snapshot, restart Cosmos,
create/delete API token) return a preview unless called with confirm=true: show the preview to the
user and only repeat the call with confirm=true after the user agreed. Secrets such as passwords,
tokens and environment variable values are redacted in responses.
"""

ContainerAction = Literal["start", "stop", "restart", "pause", "unpause", "recreate", "update"]


def _seg(value: str) -> str:
    """Quote a value for use as one URL path segment."""
    if not value or not str(value).strip():
        raise ToolError("identifier must not be empty")
    return quote(str(value), safe="")


def _dump(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)


def _container_summary(item: dict[str, Any]) -> dict[str, Any]:
    names = item.get("Names") or []
    name = names[0].lstrip("/") if names else item.get("Name", "").lstrip("/")
    return {
        "name": name,
        "id": str(item.get("Id", ""))[:12],
        "image": item.get("Image"),
        "state": item.get("State"),
        "status": item.get("Status"),
    }


def _find_backup(config: Any, name: str) -> dict[str, Any] | None:
    """Best-effort lookup of one backup definition in the backups-config response."""
    if not isinstance(config, dict):
        return None
    for container in (config, config.get("Backups"), config.get("backups")):
        if isinstance(container, dict) and isinstance(container.get(name), dict):
            return container[name]
    return None


def build_server(settings: Settings, client: CosmosClient | None = None) -> MCPServer:
    api = client or CosmosClient(settings)
    mcp = MCPServer("cosmos-cloud", instructions=INSTRUCTIONS, version=__version__)

    def tool(level: Level, title: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            if settings.read_only and level is not Level.READ:
                return fn
            annotations = ToolAnnotations(
                title=title,
                read_only_hint=level is Level.READ,
                destructive_hint=level is Level.DESTRUCTIVE,
                open_world_hint=False,
            )
            mcp.tool(annotations=annotations)(fn)
            return fn

        return register

    async def call(
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        redact_output: bool = True,
    ) -> Any:
        try:
            data = await api.request(method, path, params=params, json_body=body)
        except CosmosAPIError as exc:
            raise ToolError(str(exc)) from None
        if redact_output and settings.redact_secrets:
            data = redact(data)
        return data

    # ------------------------------------------------------------------ system

    @tool(Level.READ, "Server status")
    async def server_status() -> str:
        """Cosmos server status: version, resources, Docker state, pending updates, setup flags."""
        return _dump(await call("GET", "/api/status"))

    @tool(Level.READ, "Dashboard")
    async def get_dashboard() -> str:
        """Dashboard data as shown on the Cosmos home page."""
        return _dump(await call("GET", "/api/dashboard"))

    @tool(Level.READ, "Who am I")
    async def whoami() -> str:
        """Identity Cosmos sees for the configured API token."""
        return _dump(await call("GET", "/api/me"))

    @tool(Level.READ, "Get configuration")
    async def get_config() -> str:
        """Full Cosmos configuration. Cosmos masks credentials unless the token has CREDENTIALS_READ
        (40) and hides admin-only routes without CONFIGURATION_READ (30); remaining secrets are redacted."""
        return _dump(await call("GET", "/api/config"))

    @tool(Level.DESTRUCTIVE, "Restart Cosmos")
    async def restart_cosmos(confirm: bool = False) -> str:
        """Restart the Cosmos server process (not the host). All proxied routes are briefly
        unavailable. Without confirm=true only describes the effect."""
        if not confirm:
            return _dump(preview("restart the Cosmos server process; all routes are unavailable until it is back"))
        return _dump(await call("GET", "/api/restart"))

    # -------------------------------------------------------------- containers

    @tool(Level.READ, "List containers")
    async def list_containers(full: bool = False, name_contains: str | None = None) -> str:
        """List Docker containers managed by Cosmos. By default a compact summary
        (name, id, image, state, status); full=true returns the raw Docker data."""
        data = await call("GET", "/api/servapps")
        items = data if isinstance(data, list) else []
        if name_contains:
            needle = name_contains.lower()
            items = [c for c in items if needle in _container_summary(c)["name"].lower()]
        return _dump(items if full else [_container_summary(c) for c in items])

    @tool(Level.READ, "Get container")
    async def get_container(container: str) -> str:
        """Docker inspect data of one container (ID or name). Environment variable values are redacted."""
        return _dump(await call("GET", f"/api/servapps/{_seg(container)}/"))

    @tool(Level.READ, "Container logs")
    async def get_container_logs(
        container: str, limit: int = 100, search: str | None = None, errors_only: bool = False
    ) -> str:
        """Recent log lines of a container. limit is capped at 1000; search filters lines;
        errors_only returns stderr only."""
        limit = max(1, min(int(limit), 1000))
        params = {"limit": limit, "search": search, "errorOnly": errors_only or None}
        return _dump(await call("GET", f"/api/servapps/{_seg(container)}/logs", params=params, redact_output=False))

    @tool(Level.READ, "Check container update")
    async def check_container_update(container: str) -> str:
        """Check whether a newer image is available for the container."""
        return _dump(await call("GET", f"/api/servapps/{_seg(container)}/check-update"))

    @tool(Level.READ, "Export container")
    async def export_container(container: str) -> str:
        """Export the container as a Cosmos service definition (compose-like JSON), secrets redacted."""
        return _dump(await call("GET", f"/api/servapps/{_seg(container)}/export"))

    @tool(Level.WRITE, "Container action")
    async def container_action(container: str, action: ContainerAction) -> str:
        """Run a lifecycle action on a container: start, stop, restart, pause, unpause,
        recreate (rebuild from the same config) or update (pull the newest image and recreate).
        Use remove_container to delete a container."""
        return _dump(await call("GET", f"/api/servapps/{_seg(container)}/manage/{action}"))

    @tool(Level.DESTRUCTIVE, "Remove container")
    async def remove_container(container: str, confirm: bool = False) -> str:
        """Remove a container through Cosmos. Docker defaults apply: the container must be stopped
        first (no force) and its volumes are kept. Without confirm=true only describes the effect."""
        if not confirm:
            return _dump(
                preview(
                    f"remove the container {container!r} (volumes are kept); it has to be recreated to run again",
                    {"container": container, "endpoint": "manage/remove"},
                )
            )
        return _dump(await call("GET", f"/api/servapps/{_seg(container)}/manage/remove"))

    @tool(Level.WRITE, "Set container auto-update")
    async def set_container_auto_update(container: str, enabled: bool) -> str:
        """Enable or disable Cosmos auto-update for a container."""
        status = "true" if enabled else "false"
        return _dump(await call("GET", f"/api/servapps/{_seg(container)}/auto-update/{status}"))

    @tool(Level.WRITE, "Update container settings")
    async def update_container_settings(container: str, settings_patch: dict[str, Any]) -> str:
        """Change container settings; Cosmos applies them by recreating the container.
        Accepted keys (docker.ContainerForm): image, restartPolicy, envVars (list of KEY=VALUE),
        labels, portBindings, Volumes, networkMode, devices, dns, user, cpus, memLimit, interactive.
        Only send the keys to change."""
        if not settings_patch:
            raise ToolError("settings_patch must not be empty")
        return _dump(await call("POST", f"/api/servapps/{_seg(container)}/update", body=settings_patch))

    @tool(Level.WRITE, "Create service")
    async def create_service(service: dict[str, Any]) -> str:
        """Create containers, networks and volumes from a compose-like definition:
        {"services": {name: {...}}, "networks": {...}, "volumes": {...}} (Cosmos service format,
        see export_container for an example)."""
        if not isinstance(service.get("services"), dict) or not service["services"]:
            raise ToolError("service must contain a non-empty 'services' object")
        return _dump(await call("POST", "/api/docker-service", body=service))

    # -------------------------------------------------------- docker resources

    @tool(Level.READ, "List volumes")
    async def list_volumes() -> str:
        """List Docker volumes."""
        return _dump(await call("GET", "/api/volumes"))

    @tool(Level.READ, "List networks")
    async def list_networks() -> str:
        """List Docker networks."""
        return _dump(await call("GET", "/api/networks"))

    @tool(Level.READ, "Inspect image")
    async def inspect_image(image: str) -> str:
        """Inspect a local Docker image by name, e.g. nginx:latest."""
        return _dump(await call("GET", "/api/images", params={"imageName": image}))

    @tool(Level.WRITE, "Pull image")
    async def pull_image(image: str) -> str:
        """Pull a Docker image from its registry, e.g. nginx:latest. Does not touch running containers."""
        return _dump(await call("GET", "/api/images/pull", params={"imageName": image}, redact_output=False))

    @tool(Level.DESTRUCTIVE, "Delete volume")
    async def delete_volume(name: str, confirm: bool = False) -> str:
        """Delete a Docker volume and all data in it. Without confirm=true only describes the effect."""
        if not confirm:
            return _dump(preview(f"permanently delete the Docker volume {name!r} and all data in it", {"volume": name}))
        return _dump(await call("DELETE", f"/api/volume/{_seg(name)}"))

    @tool(Level.DESTRUCTIVE, "Delete network")
    async def delete_network(network_id: str, confirm: bool = False) -> str:
        """Delete a Docker network by ID. Without confirm=true only describes the effect."""
        if not confirm:
            return _dump(preview(f"delete the Docker network {network_id!r}", {"network": network_id}))
        return _dump(await call("DELETE", f"/api/network/{_seg(network_id)}"))

    # ------------------------------------------------------------------ routes

    @tool(Level.READ, "List routes")
    async def list_routes() -> str:
        """List reverse-proxy routes (URLs) configured in Cosmos."""
        return _dump(await call("GET", "/api/routes"))

    @tool(Level.READ, "Get route")
    async def get_route(name: str) -> str:
        """Get one reverse-proxy route by name."""
        return _dump(await call("GET", f"/api/routes/{_seg(name)}"))

    @tool(Level.WRITE, "Create route")
    async def create_route(route: dict[str, Any]) -> str:
        """Create a reverse-proxy route (utils.ProxyRouteConfig). Required: name, target.
        Common keys: mode (SERVAPP, PROXY, STATIC, SPA, REDIRECT), host, useHost, pathPrefix,
        usePathPrefix, authEnabled, description."""
        if not route.get("name") or not route.get("target"):
            raise ToolError("route needs at least 'name' and 'target'")
        return _dump(await call("POST", "/api/routes", body=route))

    @tool(Level.WRITE, "Update route")
    async def update_route(name: str, route: dict[str, Any]) -> str:
        """Replace an existing route with the given full configuration. Call get_route first,
        change the fields and send the whole object back."""
        if not route.get("name") or not route.get("target"):
            raise ToolError("route needs the full configuration including 'name' and 'target'")
        return _dump(await call("PUT", f"/api/routes/{_seg(name)}", body=route))

    @tool(Level.DESTRUCTIVE, "Delete route")
    async def delete_route(name: str, confirm: bool = False) -> str:
        """Delete a reverse-proxy route. Without confirm=true only describes the effect."""
        if not confirm:
            return _dump(preview(f"delete the route {name!r}; its URL stops working", {"route": name}))
        return _dump(await call("DELETE", f"/api/routes/{_seg(name)}"))

    # -------------------------------------------------------------- monitoring

    @tool(Level.READ, "Metrics")
    async def get_metrics(metrics: list[str] | None = None) -> str:
        """Without arguments: list available metric keys. With metrics: aggregated values for
        those keys (e.g. ["cosmos.system.cpu.0", "cosmos.system.ram"])."""
        if not metrics:
            return _dump(await call("GET", "/api/list-metrics"))
        return _dump(await call("GET", "/api/metrics", params={"metrics": ",".join(metrics)}))

    @tool(Level.READ, "List events")
    async def list_events(
        since: str | None = None,
        until: str | None = None,
        log_level: str | None = None,
        search: str | None = None,
        page: str | None = None,
    ) -> str:
        """Cosmos event log. since/until are ISO 8601 timestamps; log_level e.g. info, warning,
        error, important; page is the cursor returned by a previous call."""
        params = {"from": since, "to": until, "logLevel": log_level, "search": search, "page": page}
        return _dump(await call("GET", "/api/events", params=params))

    @tool(Level.READ, "List alerts")
    async def list_alerts() -> str:
        """Configured monitoring alerts."""
        return _dump(await call("GET", "/api/alerts"))

    @tool(Level.READ, "Notifications")
    async def get_notifications() -> str:
        """Notifications for the token owner."""
        return _dump(await call("GET", "/api/notifications"))

    # ----------------------------------------------------------------- backups

    @tool(Level.READ, "List backups")
    async def list_backups() -> str:
        """Backup definitions (restic). Repository passwords are redacted."""
        return _dump(await call("GET", "/api/backups-config"))

    @tool(Level.READ, "List backup snapshots")
    async def list_backup_snapshots(name: str) -> str:
        """Snapshots of one backup, newest information as returned by restic."""
        return _dump(await call("GET", f"/api/backups/{_seg(name)}/snapshots"))

    @tool(Level.READ, "List snapshot folders")
    async def list_snapshot_folders(name: str, snapshot: str, path: str | None = None) -> str:
        """Browse the files and folders inside a backup snapshot. path defaults to the root."""
        return _dump(
            await call("GET", f"/api/backups/{_seg(name)}/{_seg(snapshot)}/folders", params={"path": path})
        )

    @tool(Level.DESTRUCTIVE, "Restore backup")
    async def restore_backup(
        name: str,
        snapshot_id: str,
        target: str,
        include: list[str] | None = None,
        confirm: bool = False,
    ) -> str:
        """Restore files from a backup snapshot into target (absolute path on the Cosmos host).
        include limits the restore to these paths inside the snapshot (as shown by
        list_snapshot_folders). Existing files at the target are overwritten. If the backup has
        auto-stop enabled, Cosmos stops the containers using the source path during the restore.
        Runs as a background job (see list_jobs). Without confirm=true only describes the effect."""
        if not PurePosixPath(target).is_absolute():
            raise ToolError("target must be an absolute path, e.g. /cosmos/myapp")
        body: dict[str, Any] = {"snapshotId": snapshot_id, "target": target}
        if include:
            body["include"] = include
        if not confirm:
            details: dict[str, Any] = {"backup": name, "snapshot": snapshot_id, "target": target,
                                       "include": include or "everything in the snapshot"}
            try:
                backup = _find_backup(await call("GET", "/api/backups-config"), name)
            except ToolError:
                backup = None
            if backup:
                source = backup.get("Source") or backup.get("source")
                details["backup_source"] = source
                details["auto_stop_containers"] = backup.get("AutoStopContainers", backup.get("autoStopContainers"))
                if source and str(PurePosixPath(target)) == str(PurePosixPath(source)):
                    details["warning"] = "target is the original source path: current files there are overwritten"
            return _dump(preview(f"restore snapshot {snapshot_id!r} of backup {name!r} into {target}", details))
        return _dump(await call("POST", f"/api/backups/{_seg(name)}/restore", body=body))

    @tool(Level.DESTRUCTIVE, "Forget snapshot")
    async def forget_snapshot(name: str, snapshot: str, confirm: bool = False) -> str:
        """Delete (forget) one snapshot from a backup repository. Without confirm=true only
        describes the effect."""
        if not confirm:
            return _dump(
                preview(f"permanently forget snapshot {snapshot!r} of backup {name!r}",
                        {"backup": name, "snapshot": snapshot})
            )
        return _dump(await call("DELETE", f"/api/backups/{_seg(name)}/{_seg(snapshot)}/forget"))

    # -------------------------------------------------------------------- jobs

    @tool(Level.READ, "List jobs")
    async def list_jobs(running_only: bool = False) -> str:
        """Scheduled jobs (cron, backups, restores) grouped by scheduler. running_only=true lists
        only jobs currently running."""
        return _dump(await call("GET", "/api/jobs/running" if running_only else "/api/jobs"))

    @tool(Level.READ, "Get job")
    async def get_job(scheduler: str, name: str) -> str:
        """Details and recent logs of one job. scheduler and name as shown by list_jobs."""
        return _dump(await call("POST", "/api/jobs/get", body={"scheduler": scheduler, "name": name}))

    @tool(Level.WRITE, "Run job")
    async def run_job(scheduler: str, name: str) -> str:
        """Start a job now (e.g. a backup)."""
        return _dump(await call("POST", "/api/jobs/run", body={"scheduler": scheduler, "name": name}))

    @tool(Level.WRITE, "Stop job")
    async def stop_job(scheduler: str, name: str) -> str:
        """Stop a running job."""
        return _dump(await call("POST", "/api/jobs/stop", body={"scheduler": scheduler, "name": name}))

    # ----------------------------------------------------------------- storage

    @tool(Level.READ, "Storage overview")
    async def list_storage(kind: Literal["disks", "mounts"] = "disks") -> str:
        """Disks (with SMART/usage data) or mounted filesystems of the host."""
        return _dump(await call("GET", f"/api/{kind}"))

    @tool(Level.READ, "List directory")
    async def list_directory(path: str | None = None, storage: str | None = None) -> str:
        """List a directory on the host (as the Cosmos file picker does). storage selects a
        configured storage, path the folder inside it."""
        return _dump(await call("GET", "/api/list-dir", params={"path": path, "storage": storage}))

    # -------------------------------------------------------------- api tokens

    @tool(Level.READ, "List API tokens")
    async def list_api_tokens() -> str:
        """API tokens (names, owners, permissions, expiry; never the token values)."""
        return _dump(await call("GET", "/api/api-tokens"))

    @tool(Level.DESTRUCTIVE, "Create API token")
    async def create_api_token(
        name: str,
        permissions: list[int] | None = None,
        read_only: bool = False,
        expiry_days: int | None = None,
        ip_whitelist: list[str] | None = None,
        description: str | None = None,
        confirm: bool = False,
    ) -> str:
        """Create a Cosmos API token. permissions are ids: 1 ADMIN_READ, 2 ADMIN, 10 USERS_READ,
        11 USERS, 20 RESOURCES_READ, 21 RESOURCES, 30 CONFIGURATION_READ, 31 CONFIGURATION,
        40 CREDENTIALS_READ. Without permissions, read_only=true grants 1, 10, 20, 30; otherwise
        Cosmos grants its default admin set. The raw token is returned once and cannot be shown
        again. Without confirm=true only describes the effect."""
        body: dict[str, Any] = {"name": name, "readOnly": read_only}
        if permissions:
            body["permissions"] = permissions
        if expiry_days:
            body["expiryDays"] = expiry_days
        if ip_whitelist:
            body["ipWhitelist"] = ip_whitelist
        if description:
            body["description"] = description
        if not confirm:
            granted = permissions or ([1, 10, 20, 30] if read_only else "Cosmos default admin permissions")
            return _dump(
                preview(
                    f"create the API token {name!r}; whoever holds it gets these permissions",
                    {"permissions": granted, "expiry_days": expiry_days or "never",
                     "ip_whitelist": ip_whitelist or "any IP"},
                )
            )
        result = await call("POST", "/api/api-tokens", body=body, redact_output=False)
        return _dump({"created": result, "note": "The token is shown only once. Store it securely."})

    @tool(Level.DESTRUCTIVE, "Delete API token")
    async def delete_api_token(name: str, confirm: bool = False) -> str:
        """Delete (revoke) an API token by name. Clients using it lose access immediately.
        Without confirm=true only describes the effect."""
        if not confirm:
            return _dump(preview(f"revoke the API token {name!r}; clients using it lose access", {"token": name}))
        return _dump(await call("DELETE", "/api/api-tokens", body={"name": name}))

    # ------------------------------------------------------------ users and dns

    @tool(Level.READ, "List users")
    async def list_users() -> str:
        """Cosmos users (needs token permission USERS_READ, 10)."""
        return _dump(await call("GET", "/api/users"))

    @tool(Level.READ, "Check DNS")
    async def check_dns(url: str) -> str:
        """Check whether a hostname resolves to this Cosmos server (as before creating a route)."""
        return _dump(await call("GET", "/api/dns-check", params={"url": url}))

    # ------------------------------------------------------------ escape hatch

    @mcp.tool(annotations=ToolAnnotations(title="Raw Cosmos API request", read_only_hint=False,
                                          destructive_hint=True, open_world_hint=False))
    async def cosmos_request(
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
        confirm: bool = False,
    ) -> str:
        """Call any Cosmos API endpoint not covered by another tool, e.g. GET /api/cron.
        path is relative to /cosmos and starts with /api/. The same safety levels apply:
        read requests run directly, write requests are refused in read-only mode, destructive
        requests (DELETE, restarts, restores, token/user management, kill/remove) need
        confirm=true. Prefer the dedicated tools when one exists."""
        try:
            clean = normalize_path(path)
            level = classify(method, clean)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        if level is not Level.READ and settings.read_only:
            raise ToolError(f"{method} {clean} is a {level.value} request and COSMOS_READ_ONLY is enabled")
        if level is Level.DESTRUCTIVE and not confirm:
            return _dump(preview(f"send {method} {clean}", {"query": query, "body": body, "level": level.value}))
        return _dump(await call(method, clean, params=query, body=body))

    return mcp


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"cosmos-cloud-mcp: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    build_server(settings).run()


if __name__ == "__main__":
    main()
