"""Thin async HTTP client for the Cosmos Cloud REST API."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings


class CosmosAPIError(Exception):
    """An error response from Cosmos, or a transport failure."""

    def __init__(self, status: int | None, message: str, code: str | None = None):
        self.status = status
        self.code = code
        self.message = message
        parts = ["Cosmos API error"]
        if status is not None:
            parts.append(str(status))
        if code:
            parts.append(code)
        super().__init__(f"{' '.join(parts)}: {message}")


_HINTS = {
    "AT003": "the API token lacks a permission for this endpoint; a Read Only token cannot write, create a token with the needed permissions (Management > Configuration > API, or the create_api_token tool)",
}


class CosmosClient:
    """Sends requests to ``<COSMOS_URL>/cosmos<path>`` with the API token.

    Request headers are never logged or included in error messages.
    """

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.api_base,
                headers={
                    "Authorization": f"Bearer {self._settings.token}",
                    "Accept": "application/json",
                    "User-Agent": "cosmos-cloud-mcp",
                },
                verify=self._settings.verify_tls,
                timeout=self._settings.timeout,
                transport=self._transport,
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Send a request and return the ``data`` field of the Cosmos envelope.

        Responses without a ``data`` field are returned as-is (JSON) or as text.
        """
        if not path.startswith("/"):
            path = "/" + path
        clean_params = {k: _param(v) for k, v in (params or {}).items() if v is not None}
        try:
            response = await self._http().request(
                method.upper(),
                path,
                params=clean_params or None,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise CosmosAPIError(None, f"request to Cosmos failed: {type(exc).__name__}: {exc}") from None

        body = _decode(response)

        if response.is_redirect:
            raise CosmosAPIError(
                response.status_code,
                "Cosmos answered with a redirect; check COSMOS_URL (scheme, host and port)",
            )
        if response.status_code >= 400:
            message, code = _error_details(body, response)
            hint = _HINTS.get(code or "")
            if hint:
                message = f"{message} ({hint})"
            raise CosmosAPIError(response.status_code, message, code)

        if isinstance(body, dict):
            if body.get("status") not in (None, "OK") and "data" not in body:
                raise CosmosAPIError(response.status_code, str(body.get("message") or body), body.get("code"))
            if "data" in body:
                return body["data"]
        return body


def _param(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _decode(response: httpx.Response) -> Any:
    text = response.text
    if not text:
        return None
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        # Some Cosmos endpoints stream several JSON objects or plain text.
        return text


def _error_details(body: Any, response: httpx.Response) -> tuple[str, str | None]:
    if isinstance(body, dict):
        return str(body.get("message") or body.get("status") or response.reason_phrase), body.get("code")
    if isinstance(body, str) and body.strip():
        return body.strip()[:500], None
    return response.reason_phrase or "error", None
