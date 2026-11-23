# Research: existing MCP servers for Cosmos Cloud (2026-11-23)

## Result

No usable MCP server exists. Build our own.

| Candidate | What it is | Verdict |
|---|---|---|
| Official (azukaar/Cosmos-Server) | None. Feature request [#629](https://github.com/azukaar/Cosmos-Server/issues/629), opened 2026-11-06, open, no comments | Nothing to use |
| [mrmos/cosmos-mcp](https://github.com/mrmos/cosmos-mcp) | MCP for **cosmos.so** (moodboard site), TypeScript, reverse-engineered GraphQL | Different product |
| [LobeHub: paolosturbini-cosmos-mcp](https://lobehub.com/mcp/paolosturbini-cosmos-mcp) | Python MCP for Cosmos Cloud, v0.1.0, 55 tools per README | Source repo `github.com/PaoloSturbini/cosmos-mcp` returns 404 (private or deleted), cannot be reviewed or installed |
| Others named "cosmos" on MCP directories | Azure Cosmos DB, Cosmos blockchain, Polarity Lab knowledge graph | Unrelated |

## Ideas taken from the PaoloSturbini listing (README only, code not seen)

- Env vars `COSMOS_URL`, `COSMOS_TOKEN`, `COSMOS_READ_ONLY`, `COSMOS_VERIFY_TLS`.
- Destructive tools need `confirm=True`.
- Tool areas: system, containers, Docker (volumes/networks/images), market, routes, metrics/events/alerts, backups, storage, Constellation VPN, cron/jobs, users, API tokens, OpenID, DNS.

## Proposed tool set (v0.24.0 endpoints)

Paths are relative to `/cosmos`. Level: R = read, W = write, D = destructive (needs `confirm=true`).

### System
| Tool | Endpoint | Level |
|---|---|---|
| server_status | GET /api/status | R |
| get_dashboard | GET /api/dashboard | R |
| whoami | GET /api/me | R |
| get_config | GET /api/config | R |
| restart_cosmos | GET /api/restart | D |

### Containers (servapps)
| Tool | Endpoint | Level |
|---|---|---|
| list_containers | GET /api/servapps | R |
| get_container | GET /api/servapps/{id}/ | R |
| get_container_logs | GET /api/servapps/{id}/logs (limit, search, errorOnly) | R |
| check_container_update | GET /api/servapps/{id}/check-update | R |
| export_container | GET /api/servapps/{id}/export | R |
| container_action | GET /api/servapps/{id}/manage/{action}: stop, start, restart, pause, unpause, recreate, update | W |
| container_kill / container_remove | manage/kill, manage/remove | D |
| set_auto_update | GET /api/servapps/{id}/auto-update/{status} | W |
| update_container | POST /api/servapps/{id}/update | W |
| create_service | POST /api/docker-service | W |

### Docker resources
| Tool | Endpoint | Level |
|---|---|---|
| list_volumes / list_networks | GET /api/volumes, /api/networks | R |
| inspect_image | GET /api/images | R |
| pull_image | GET /api/images/pull | W |
| delete_volume | DELETE /api/volume/{name} | D |
| delete_network | DELETE /api/network/{id} | D |

### Routes
| Tool | Endpoint | Level |
|---|---|---|
| list_routes / get_route | GET /api/routes, /api/routes/{name} | R |
| create_route / update_route | POST /api/routes, PUT /api/routes/{name} | W |
| delete_route | DELETE /api/routes/{name} | D |

### Monitoring
| Tool | Endpoint | Level |
|---|---|---|
| list_metrics / get_metrics | GET /api/list-metrics, /api/metrics | R |
| list_events | GET /api/events | R |
| list_alerts | GET /api/alerts | R |
| get_notifications | GET /api/notifications | R |

### Backups
| Tool | Endpoint | Level |
|---|---|---|
| list_backups | GET /api/backups-config | R |
| list_backup_snapshots | GET /api/backups/{name}/snapshots | R |
| list_snapshot_folders | GET /api/backups/{name}/{snapshot}/folders | R |
| restore_backup | POST /api/backups/{name}/restore | D |
| forget_snapshot | DELETE /api/backups/{name}/{snapshot}/forget | D |

### Cron / jobs
| Tool | Endpoint | Level |
|---|---|---|
| list_jobs / get_running_jobs | GET /api/jobs, /api/jobs/running | R |
| get_job | POST /api/jobs/get | R |
| run_job / stop_job | POST /api/jobs/run, /api/jobs/stop | W |

### Storage
| Tool | Endpoint | Level |
|---|---|---|
| list_disks / list_mounts | GET /api/disks, /api/mounts | R |
| list_directory | GET /api/list-dir | R |

### API tokens
| Tool | Endpoint | Level |
|---|---|---|
| list_api_tokens | GET /api/api-tokens | R |
| create_api_token | POST /api/api-tokens (name, permissions, readOnly, expiryDays, ipWhitelist) | D |
| delete_api_token | DELETE /api/api-tokens (body: name) | D |

### Users, DNS
| Tool | Endpoint | Level |
|---|---|---|
| list_users | GET /api/users | R |
| check_dns | GET /api/dns-check | R |

Not planned: disk format, mount/unmount, MergerFS, SnapRAID actions, host restart (`/api/restart-server`), Constellation create/reset/force-reform, user create/delete, rclone config dump (contains credentials), metrics reset.

## Notes on specific endpoints

- `POST /api/backups/{name}/restore` body is untyped in the spec (`object`). Read the handler in the source before implementing.
- `POST /api/api-tokens` returns the raw token once. The tool must return it to the user but never log it.
- `container_action` with `update`/`recreate` changes the container; keep at level W but describe the effect in the tool docstring.
