"""Settings loaded from environment variables (and optionally an env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """Raised when required settings are missing or invalid."""


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def load_env_file(path: str | os.PathLike[str], environ: dict[str, str] | None = None) -> None:
    """Load KEY=VALUE lines from ``path`` into ``environ`` without overriding existing keys.

    Supports comments, blank lines, an optional ``export`` prefix and single or double quotes.
    Values are never printed.
    """
    target = os.environ if environ is None else environ
    file = Path(path).expanduser()
    if not file.is_file():
        raise ConfigError(f"COSMOS_ENV_FILE does not exist: {file}")
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in target:
            target[key] = value


@dataclass(frozen=True)
class Settings:
    url: str
    token: str
    read_only: bool = False
    verify_tls: bool = True
    redact_secrets: bool = True
    timeout: float = 30.0

    @property
    def api_base(self) -> str:
        """Base URL including the ``/cosmos`` prefix, without trailing slash."""
        base = self.url.rstrip("/")
        if not base.endswith("/cosmos"):
            base += "/cosmos"
        return base

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if environ is None else environ)
        env_file = env.get("COSMOS_ENV_FILE")
        if env_file:
            load_env_file(env_file, env)

        url = env.get("COSMOS_URL", "").strip()
        token = env.get("COSMOS_TOKEN", "").strip()
        if not url:
            raise ConfigError("COSMOS_URL is not set (e.g. https://cosmos.example.com)")
        if not url.startswith(("http://", "https://")):
            raise ConfigError("COSMOS_URL must start with http:// or https://")
        if not token:
            raise ConfigError("COSMOS_TOKEN is not set (create one in Cosmos: Settings > API Tokens)")
        if not token.startswith("cosmos_"):
            raise ConfigError("COSMOS_TOKEN does not look like a Cosmos API token (expected prefix 'cosmos_')")

        try:
            timeout = float(env.get("COSMOS_TIMEOUT", "30") or 30)
        except ValueError as exc:
            raise ConfigError("COSMOS_TIMEOUT must be a number of seconds") from exc

        return cls(
            url=url,
            token=token,
            read_only=_parse_bool("COSMOS_READ_ONLY", env.get("COSMOS_READ_ONLY"), False),
            verify_tls=_parse_bool("COSMOS_VERIFY_TLS", env.get("COSMOS_VERIFY_TLS"), True),
            redact_secrets=_parse_bool("COSMOS_REDACT_SECRETS", env.get("COSMOS_REDACT_SECRETS"), True),
            timeout=timeout,
        )

    def __repr__(self) -> str:  # never expose the token in logs or tracebacks
        return (
            f"Settings(url={self.url!r}, token='***', read_only={self.read_only}, "
            f"verify_tls={self.verify_tls}, redact_secrets={self.redact_secrets}, timeout={self.timeout})"
        )
