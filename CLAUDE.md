# CosmosCloudMCP

MCP server for **Cosmos Cloud** (azukaar/Cosmos-Server), the self-hosted server manager. Lets an MCP client read and change containers, routes, backups etc. through the Cosmos REST API.

Not related to Azure Cosmos DB, the Cosmos blockchain or cosmos.so. See `docs/research.md` for why no existing server is used. Host-specific notes, if any, are in the gitignored `CLAUDE.local.md`.

## Layout

- `src/cosmos_cloud_mcp/server.py`: MCPServer tools (`build_server(settings)`), entry point `main()`
- `client.py`: async httpx client, unwraps the `{"status","data"}` envelope, maps errors
- `safety.py`: levels and classification for the `cosmos_request` escape hatch
- `redact.py`: secret redaction in responses
- `config.py`: settings from environment / `COSMOS_ENV_FILE`
- `http_transport.py`: Streamable HTTP mode (stateless, JSON responses), bearer-token middleware, `/health`
- `healthcheck.py`: Docker HEALTHCHECK (only probes in http mode)
- Tests: `.venv/bin/pytest` (HTTP mocked with respx)

## Decisions

- **Language:** Python, official MCP Python SDK v2 (`MCPServer`), stdio transport. Built, tested and shipped as a Docker image; a local `.venv/` is optional.
- **Three tool levels:**
  1. Read tools: always available.
  2. Write tools (start/stop/restart/update container, create/edit route, run job, ...): run directly; the client's per-tool permission prompt is the approval.
  3. Destructive tools: require `confirm=true`, otherwise return a description of what would happen. Remove container, delete volume/route/network, restore backup, forget snapshot, restart Cosmos, create/delete API token.
- **Transports:** `stdio` (default) or `http` (`COSMOS_MCP_TRANSPORT=http`, requires `COSMOS_MCP_AUTH_TOKEN`, binds 127.0.0.1 by default).
- **Kill switch:** `COSMOS_READ_ONLY=true` does not register write tools.
- Target Cosmos version: **0.24.0**.

## Cosmos API facts (verified in v0.24.0 source and spec)

- Base path `/cosmos`, e.g. `GET https://<cosmos-host>/cosmos/api/status`.
- Auth: header `Authorization: Bearer cosmos_<token>`.
- API tokens skip the sudo check: only the token's permission list is checked (`CheckPermissions` in `src/utils/loggedIn.go`). Missing permission returns HTTP 403 with code `AT003`.
- Token permissions: 1 ADMIN_READ, 2 ADMIN, 10 USERS_READ, 11 USERS, 20 RESOURCES_READ, 21 RESOURCES, 30 CONFIGURATION_READ, 31 CONFIGURATION, 40 CREDENTIALS_READ, 100/101 LOGIN/LOGIN_WEAK.
- A token cannot be granted permissions its creator lacks (`AT016`).
- Web UI (Management > Configuration > API) has no permission picker: Read Only off = `DefaultAdminTokenPermissions` (all 9), on = 1, 10, 20, 30. Custom lists only via `POST /api/api-tokens`.
- `GET /api/config` needs only login; Cosmos masks credentials without permission 40.
- Restore body: `{snapshotId, target, include?}` (`RestoreBackupRoute` in `src/backups/API.go`), runs as a background job.
- `manage/remove` uses Docker `RemoveOptions{}`: no force, volumes kept.
- Full spec: `docs/api/openapi-v0.24.0.json`.

## Rules

- **Secrets:** the token lives only in `.env` (gitignored) or the MCP client config. Never commit it, never print it, never log request headers.
- Keep the tool count small (roughly 30 to 50). `cosmos_request` must apply the same read/write/confirm levels.
- No personal data (hostnames, IPs, domains, email addresses, host paths) in tracked files.
