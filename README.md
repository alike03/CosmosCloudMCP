# CosmosCloudMCP

An [MCP](https://modelcontextprotocol.io) server for **[Cosmos Cloud](https://github.com/azukaar/Cosmos-Server)** (azukaar/Cosmos-Server), the self-hosted server manager and reverse proxy. It lets an MCP client such as Claude Code read and manage your Cosmos instance through its REST API: containers, reverse-proxy routes, Docker volumes and networks, backups, scheduled jobs, metrics and API tokens.

Not related to Azure Cosmos DB, the Cosmos blockchain or cosmos.so.

- Python, official MCP Python SDK v2 (`MCPServer`), stdio transport
- Read and write tools in three safety levels, plus a global read-only switch
- Secrets (passwords, tokens, environment variable values) are redacted in responses
- Built and tested against Cosmos **v0.24.0**; other versions may work if the API is unchanged

## Install

Requires a Cosmos instance with API tokens (v0.24.0 or newer). Two options:

**Docker** (no local Python needed):

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

In the Cosmos UI open **Settings > API Tokens** and create a token. Grant only what you want the MCP client to be able to do:

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
| 40 | CREDENTIALS_READ | unmasked passwords, env vars and secrets; not needed, leave it out |

Recommended sets:

- **Read only:** 1, 10, 20, 30
- **Day-to-day management:** 1, 10, 20, 21, 30, 31
- **Full (including restart and token management):** 1, 2, 10, 20, 21, 30, 31

API tokens bypass the sudo check of the web UI; only the token's permission list counts. A missing permission returns HTTP 403 with code `AT003`, which the tools report together with a hint. Consider setting an expiry and an IP whitelist on the token.

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

Copy `.env.example` to `.env`, fill it in and keep it out of version control.

## Register with Claude Code

Keep the token in the `.env` file so it does not end up in the Claude Code config.

**Docker:**

```bash
claude mcp add cosmos --scope user -- \
  docker run -i --rm --env-file /path/to/CosmosCloudMCP/.env cosmos-cloud-mcp
```

`docker run --env-file` takes values literally, so write them without quotes in `.env`. If Cosmos runs on the same host and you use `COSMOS_URL=https://localhost`, add `--network host` (inside the container `localhost` is the container itself) and set `COSMOS_VERIFY_TLS=false` if the certificate is not issued for `localhost`.

**Python package:**

```bash
claude mcp add cosmos --scope user \
  -e COSMOS_ENV_FILE=/path/to/CosmosCloudMCP/.env \
  -- /path/to/CosmosCloudMCP/.venv/bin/cosmos-cloud-mcp
```

Alternatively pass the variables directly with `-e COSMOS_URL=... -e COSMOS_TOKEN=...`. Check the connection with `claude mcp list` or `/mcp` inside Claude Code.

Other MCP clients: run the Docker command or `.venv/bin/cosmos-cloud-mcp` as a stdio server with the same environment variables.

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
