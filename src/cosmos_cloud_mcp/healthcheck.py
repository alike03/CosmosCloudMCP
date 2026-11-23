"""Docker HEALTHCHECK: probe /health in http mode, always healthy in stdio mode."""

from __future__ import annotations

import os
import sys
import urllib.request

from .config import ConfigError, load_env_file


def main() -> int:
    env = dict(os.environ)
    if env.get("COSMOS_ENV_FILE"):
        # The server reads its settings from this file too; environment variables take precedence.
        try:
            load_env_file(env["COSMOS_ENV_FILE"], env)
        except (ConfigError, OSError):
            return 1
    transport = (env.get("COSMOS_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport not in {"http", "streamable-http"}:
        return 0
    host = (env.get("COSMOS_MCP_HOST") or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = (env.get("COSMOS_MCP_PORT") or "8000").strip()
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=3) as response:  # noqa: S310
            return 0 if response.status == 200 else 1
    except Exception:  # noqa: BLE001
        return 1


if __name__ == "__main__":
    sys.exit(main())
