"""Safety levels and classification of raw requests for the ``cosmos_request`` escape hatch."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class Level(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# Paths (relative to /cosmos) that are destructive with any HTTP method.
_DESTRUCTIVE_PATHS = [
    r"^/api/restart$",
    r"^/api/restart-server$",
    r"^/api/reset-metrics$",
    r"^/api/rclone-restart$",
    r"^/api/logout$",
    r"^/api/servapps/[^/]+/manage/(kill|remove)$",
    r"^/api/backups/[^/]+/restore$",
    r"^/api/backups/[^/]+/[^/]+/forget$",
    r"^/api/constellation/(reset|restart|create|force-reform).*$",
    r"^/api/snapraid/[^/]+/[^/]+$",
    r"^/api/disks/.*$",
    r"^/api/(mount|unmount|merge)$",
    r"^/api/users(/.*)?/(reset-2fa|reset-password)$",
    # Recreates the Cosmos container (a restart) or reprovisions/rejoins the server.
    r"^/api/migrate-host$",
    r"^/api/setup(-join)?$",
    r"^/api/constellation/connect$",
    # User management outside /api/users and minting of registry deploy tokens.
    r"^/api/(register|invite|password-reset)$",
    r"^/api/constellation/registries/[^/]+/tokens(/.*)?$",
]

# GET endpoints that change state even though they use GET.
_MUTATING_GET_PATHS = [
    r"^/api/servapps/[^/]+/manage/[^/]+$",
    r"^/api/servapps/[^/]+/(auto-update|lazy|secure)/[^/]+$",
    r"^/api/images/pull(-if-missing)?$",
    r"^/api/force-server-update$",
    r"^/api/upgrade.*$",
    r"^/api/newInstall$",
    r"^/api/notifications/read$",
    r"^/api/mfa$",
]

# POST endpoints that only read.
_READ_ONLY_POSTS = [
    r"^/api/jobs/get$",
]

# Non-GET methods on these paths are destructive (token and user management, and
# the config endpoint, whose PUT replaces the whole Cosmos configuration).
_DESTRUCTIVE_WRITES = [
    r"^/api/config$",
    r"^/api/api-tokens(/.*)?$",
    r"^/api/users(/.*)?$",
]

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _matches(patterns: list[str], path: str) -> bool:
    return any(re.match(p, path) for p in patterns)


def normalize_path(path: str) -> str:
    """Validate and normalise a path relative to ``/cosmos``.

    Accepts ``/api/...``, ``api/...`` or ``/cosmos/api/...``. Rejects absolute URLs, query strings
    and path traversal so the request can only reach the configured Cosmos API.
    """
    p = path.strip()
    if "://" in p or p.startswith("//"):
        raise ValueError("path must be relative to the Cosmos API, not a full URL")
    if "?" in p or "#" in p:
        raise ValueError("pass query parameters via 'query', not in the path")
    if not p.startswith("/"):
        p = "/" + p
    if p.startswith("/cosmos/"):
        p = p[len("/cosmos") :]
    segments = p.split("/")
    lowered = p.lower()
    if any(s in {".", ".."} for s in segments) or "%2e" in lowered or "\\" in p:
        raise ValueError("path traversal is not allowed")
    if "%2f" in lowered or "%5c" in lowered:
        # Cosmos decodes these into path separators, which would bypass the classification.
        raise ValueError("encoded slashes are not allowed in the path")
    if not p.startswith("/api/"):
        raise ValueError("path must start with /api/")
    return p.rstrip("/") if p != "/api/" else p


def classify(method: str, path: str) -> Level:
    """Return the safety level of a raw request (``path`` already normalised)."""
    m = method.upper()
    if m not in _ALLOWED_METHODS:
        raise ValueError(f"unsupported HTTP method {method!r}")
    if m == "DELETE" or _matches(_DESTRUCTIVE_PATHS, path):
        return Level.DESTRUCTIVE
    if m != "GET" and _matches(_DESTRUCTIVE_WRITES, path):
        return Level.DESTRUCTIVE
    if m == "GET":
        return Level.WRITE if _matches(_MUTATING_GET_PATHS, path) else Level.READ
    if m == "POST" and _matches(_READ_ONLY_POSTS, path):
        return Level.READ
    return Level.WRITE


def preview(action: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Result of a destructive tool called without ``confirm=true``: nothing is executed."""
    return {
        "executed": False,
        "confirm_required": True,
        "would": action,
        "details": details or {},
        "next_step": "Show this to the user. Only after they agree, call the same tool again with confirm=true.",
    }
