"""Redaction of secrets (passwords, tokens, env var values) in API responses."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***redacted***"

# Keys whose scalar string values are replaced. Matched case-insensitively against the key.
_SECRET_KEY = re.compile(
    r"(pass(word|wd|phrase)?$|password|secret|token|api[_-]?key|private[_-]?key|privkey|"
    r"credential|jwt|auth[_-]?key|encryption[_-]?key|mongodb|smtp[_-]?pass|salt$|"
    r"access[_-]?key|session[_-]?key)",
    re.IGNORECASE,
)
# Keys that match the pattern above but are safe to show.
_SAFE_KEYS = {"tokensuffix", "tokenname", "tokens", "apitokens"}
# Keys that hold environment variables, as ["KEY=VALUE", ...] or {"KEY": "VALUE"}.
_ENV_KEYS = {"env", "envvars", "environment"}


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered not in _SAFE_KEYS and bool(_SECRET_KEY.search(key))


def _redact_env(value: Any) -> Any:
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and "=" in item:
                name = item.split("=", 1)[0]
                out.append(f"{name}={REDACTED}")
            else:
                out.append(item)
        return out
    if isinstance(value, dict):
        return {k: (REDACTED if isinstance(v, str) and v else v) for k, v in value.items()}
    return value


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with secret-looking fields replaced.

    Only non-empty string values are replaced, so nested objects (e.g. a dict of tokens keyed by
    name) and booleans like ``authEnabled`` stay intact. Environment variables keep their names
    but lose their values.
    """
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            skey = str(key)
            if skey.lower() in _ENV_KEYS:
                out[key] = _redact_env(redact(item) if isinstance(item, dict) else item)
            elif isinstance(item, str) and item and _is_secret_key(skey):
                out[key] = REDACTED
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
