# CosmosCloudMCP

An [MCP](https://modelcontextprotocol.io) server for **[Cosmos Cloud](https://github.com/azukaar/Cosmos-Server)** (azukaar/Cosmos-Server), the self-hosted server manager and reverse proxy. It lets an MCP client such as Claude Code read and manage your Cosmos instance through its REST API: containers, reverse-proxy routes, Docker volumes and networks, backups, scheduled jobs, metrics and API tokens.

Not related to Azure Cosmos DB, the Cosmos blockchain or cosmos.so.

- Python, official MCP Python SDK v2 (`MCPServer`)
- Two transports: **HTTP** (run it once as a service, e.g. in Cosmos itself, with auto-update) or **stdio** (the MCP client starts it per session)
- Read and write tools in three safety levels, plus a global read-only switch
- Secrets (passwords, tokens, environment variable values) are redacted in responses
- Built and tested against Cosmos **v0.24.0**; other versions may work if the API is unchanged

## Install

Requires a Cosmos instance with API tokens (v0.24.0 or newer). Three options:

**Prebuilt Docker image** (built by GitHub Actions for linux/amd64 and linux/arm64):

```bash
docker pull ghcr.io/alike03/cosmoscloudmcp:latest
```

Tags: `latest` (main branch), `X.Y.Z` and `X.Y` (release tags `vX.Y.Z`), `sha-<commit>`.

**Build the Docker image yourself** (no local Python needed):

```bash
git clone https://github.com/alike03/CosmosCloudMCP.git
cd CosmosCloudMCP
docker build -t cosmos-cloud-mcp .
```

**Python package** (Python 3.11 or newer):

```bash
git clone https://github.com/alike03/CosmosCloudMCP.git
cd CosmosCloudMCP
python3 -m venv .venv
.venv/bin/pip install .
```

## Create an API token

In the Cosmos web UI open **Management > Configuration**, tab **API**, and click **New Token**. Set an expiry and, if possible, an IP whitelist.

The web form has no per-permission selection, only the **Read Only** switch:

| Form setting | Permissions granted | Effect on this server |
|---|---|---|
| Read Only **off** (default, shown as "Full Access") | all: 1, 2, 10, 11, 20, 21, 30, 31, 40 | every tool works; includes user management (11) and unmasked credentials (40) |
| Read Only **on** | 1, 10, 20, 30 | read tools work, write tools fail with HTTP 403 `AT003`; pair it with `COSMOS_READ_ONLY=true` |

A token with a custom permission list can only be created through the API, for example with this server's `create_api_token` tool (using a Full Access token once), and Cosmos refuses to grant permissions the creating token does not hold (`AT016`). Permission ids:

| id | Permission | Needed for |
|---|---|---|
| 1 | ADMIN_READ | list API tokens, logs |
| 2 | ADMIN | restart Cosmos, create/delete API tokens |
| 10 | USERS_READ | `list_users` |
| 11 | USERS | not used by dedicated tools |
| 20 | RESOURCES_READ | containers, volumes, networks, backups, jobs, storage, metrics |
| 21 | RESOURCES | container actions, volumes/networks, backups, jobs |
| 30 | CONFIGURATION_READ | routes; without it `get_config` hides admin-only routes |
| 31 | CONFIGURATION | create/update/delete routes |
| 40 | CREDENTIALS_READ | unmasked passwords, env vars and secrets; not needed |

Useful custom sets:

- **Day-to-day management:** 1, 10, 20, 21, 30, 31
- **Everything this server uses, without user management and credentials:** 1, 2, 10, 20, 21, 30, 31

API tokens bypass the sudo check of the web UI; only the token's permission list counts. A missing permission returns HTTP 403 with code `AT003`, which the tools report together with a hint. With a Full Access token Cosmos returns credentials unmasked; this server still redacts them by field name (`COSMOS_REDACT_SECRETS=true`), but a narrower token is the better protection.

## Configuration

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `COSMOS_URL` | required | Base URL of Cosmos, e.g. `https://cosmos.example.com` (the `/cosmos` API prefix is added automatically) |
| `COSMOS_TOKEN` | required | API token, starts with `cosmos_` |
| `COSMOS_READ_ONLY` | `false` | `true` registers read tools only |
| `COSMOS_VERIFY_TLS` | `true` | `false` for self-signed certificates, e.g. when connecting to `https://localhost` |
| `COSMOS_REDACT_SECRETS` | `true` | `false` returns passwords and env var values unmodified |
| `COSMOS_TIMEOUT` | `30` | Request timeout in seconds |
| `COSMOS_ENV_FILE` | unset | Path to a `.env` file with the variables above; variables set in the environment take precedence |
| `COSMOS_MCP_TRANSPORT` | `stdio` | `http` serves MCP over Streamable HTTP |
| `COSMOS_MCP_AUTH_TOKEN` | required for `http` | Bearer secret clients must send, at least 24 characters. Generate one with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` |
| `COSMOS_MCP_HOST` | `127.0.0.1` | Address the HTTP server binds to |
| `COSMOS_MCP_PORT` | `8000` | HTTP port |
| `COSMOS_MCP_PATH` | `/mcp` | Path of the MCP endpoint |
| `COSMOS_MCP_ALLOWED_HOSTS` | unset | Comma-separated `host:port` values accepted in the `Host` header when not bound to loopback (DNS-rebinding protection) |

Copy `.env.example` to `.env`, fill it in and keep it out of version control.

## Run as a service (HTTP)

In HTTP mode one long-running container serves all MCP clients. The endpoint is `http://<host>:<port>/mcp` and every request needs `Authorization: Bearer <COSMOS_MCP_AUTH_TOKEN>`. `GET /health` answers without authentication. The server is stateless, so clients keep working across restarts and image updates.

### In Cosmos

In Cosmos open **ServApps**, choose **Import Compose File** and paste this, with your own values:

```yaml
services:
  cosmos-mcp:
    image: ghcr.io/alike03/cosmoscloudmcp:latest
    container_name: cosmos-mcp
    restart: unless-stopped
    network_mode: host
    environment:
      COSMOS_URL: https://localhost
      COSMOS_VERIFY_TLS: "false"
      COSMOS_TOKEN: cosmos_xxx
      COSMOS_MCP_TRANSPORT: http
      COSMOS_MCP_HOST: 127.0.0.1
      COSMOS_MCP_PORT: "8000"
      COSMOS_MCP_AUTH_TOKEN: replace-with-a-long-random-secret
    labels:
      cosmos-auto-update: "true"
```

- `network_mode: host` with `COSMOS_MCP_HOST: 127.0.0.1` makes the endpoint reachable only from the server itself, and lets the container reach Cosmos at `https://localhost`.
- `cosmos-auto-update: "true"` is the label Cosmos sets when you enable auto-update for a container; new image versions are then rolled out by Cosmos.
- The environment variables, including both tokens, are stored in the container configuration and are visible to anyone who can inspect the container. Use a Cosmos token with only the permissions you need.

Register it in Claude Code:

```bash
claude mcp add --transport http cosmos http://127.0.0.1:8000/mcp --scope user \
  --header "Authorization: Bearer <COSMOS_MCP_AUTH_TOKEN>"
```

### Access from other machines

Bind to all interfaces with `COSMOS_MCP_HOST: 0.0.0.0` and list the host names clients use in `COSMOS_MCP_ALLOWED_HOSTS` (e.g. `myserver.lan:8000`). Plain HTTP sends the bearer token unencrypted, so only do this on a trusted network or VPN (for example Cosmos Constellation), or put the endpoint behind an HTTPS route that is restricted to your LAN. Never expose it to the internet: whoever holds the bearer token can manage your containers.

## Run per session (stdio)

The MCP client starts the server for each session and talks to it over stdin/stdout; nothing listens on the network. Keep the Cosmos token in the `.env` file so it does not end up in the client configuration.

**Docker:**

```bash
claude mcp add cosmos --scope user -- \
  docker run -i --rm --pull always --env-file /path/to/.env ghcr.io/alike03/cosmoscloudmcp:latest
```

`--pull always` fetches new image versions when a session starts. `docker run --env-file` takes values literally, so write them without quotes in `.env`. If Cosmos runs on the same host and you use `COSMOS_URL=https://localhost`, add `--network host` and set `COSMOS_VERIFY_TLS=false` if the certificate is not issued for `localhost`. Use `cosmos-cloud-mcp` instead of the `ghcr.io/...` name if you built the image yourself.

**Python package:**

```bash
claude mcp add cosmos --scope user \
  -e COSMOS_ENV_FILE=/path/to/CosmosCloudMCP/.env \
  -- /path/to/CosmosCloudMCP/.venv/bin/cosmos-cloud-mcp
```

Check the connection with `claude mcp list` or `/mcp` inside Claude Code. Other MCP clients: use the HTTP endpoint, or run the Docker command / `.venv/bin/cosmos-cloud-mcp` as a stdio server with the same environment variables.

## Safety levels

| Level | Behaviour | Tools |
|---|---|---|
| Read | Always available, no side effects | `server_status`, `get_dashboard`, `whoami`, `get_config`, `list_containers`, `get_container`, `get_container_logs`, `check_container_update`, `export_container`, `list_volumes`, `list_networks`, `inspect_image`, `list_routes`, `get_route`, `get_metrics`, `list_events`, `list_alerts`, `get_notifications`, `list_backups`, `list_backup_snapshots`, `list_snapshot_folders`, `list_jobs`, `get_job`, `list_storage`, `list_directory`, `list_api_tokens`, `list_users`, `check_dns` |
| Write | Run directly; the MCP client's per-tool permission prompt is the approval | `container_action` (start, stop, restart, pause, unpause, recreate, update), `set_container_auto_update`, `update_container_settings`, `create_service`, `pull_image`, `create_route`, `update_route`, `run_job`, `stop_job` |
| Destructive | Without `confirm=true` they only return a description of what would happen | `remove_container`, `delete_volume`, `delete_network`, `delete_route`, `restore_backup`, `forget_snapshot`, `restart_cosmos`, `create_api_token`, `delete_api_token` |

`COSMOS_READ_ONLY=true` does not register write and destructive tools at all.

`cosmos_request` is an escape hatch for endpoints without a dedicated tool (for example `GET /api/cron`). It applies the same levels: it classifies the method and path (several Cosmos endpoints change state via GET, such as `manage/{action}` or `/api/restart`), refuses non-read requests in read-only mode and requires `confirm=true` for destructive ones (any `DELETE`, restarts, restores, kill/remove, token and user management). It only reaches paths under `/cosmos/api/` on the configured host.

Tool annotations (`readOnlyHint`, `destructiveHint`) are set accordingly, so clients can apply their own policies.

### Notes

- `restore_backup` posts `{snapshotId, target, include}` as the Cosmos web UI does. `target` must be an absolute path on the host; existing files there are overwritten. If the backup has auto-stop enabled, Cosmos stops the containers that use the source path during the restore. The restore runs as a background job: follow it with `list_jobs` / `get_job`.
- `remove_container` uses Docker defaults: the container must be stopped first and its volumes are kept.
- `create_api_token` returns the raw token once. Cosmos refuses to grant permissions the calling token does not hold (`AT016`).
- Redaction is a best-effort safety net based on field names. Container logs are returned unmodified.

## Development

```bash
docker build --target test .          # run the unit tests in Docker
# or locally
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Tests mock HTTP with [respx](https://lundberg.github.io/respx/); no Cosmos instance is needed. The OpenAPI spec of Cosmos v0.24.0 is in `docs/api/`.

## License

MIT, see [LICENSE](LICENSE).
