"""Async HTTP client for MoE Libris hub communication."""

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


class LibrisClient:
    """Client for communicating with a MoE Libris federation hub."""

    def __init__(self, hub_url: str, api_key: str, node_id: str,
                 timeout: float = _DEFAULT_TIMEOUT):
        self.hub_url = hub_url.rstrip("/")
        self.api_key = api_key
        self.node_id = node_id
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def push(self, bundle: dict) -> dict:
        """Push a knowledge bundle to the Libris hub.

        Args:
            bundle: JSON-LD knowledge bundle from export_knowledge_bundle()

        Returns:
            Hub response with accepted/rejected/queued counts.

        Raises:
            LibrisError on HTTP or connection errors.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    f"{self.hub_url}/v1/federation/push",
                    json={"bundle": bundle},
                    headers=self._headers(),
                )
                if resp.status_code == 429:
                    raise LibrisError("Rate limited by hub — try again later", code=429)
                if resp.status_code == 403:
                    raise LibrisError("Node blocked by hub", code=403)
                if resp.status_code != 200:
                    raise LibrisError(
                        f"Push failed: HTTP {resp.status_code} — {resp.text[:200]}",
                        code=resp.status_code,
                    )
                return resp.json()
            except httpx.ConnectError as e:
                raise LibrisError(f"Cannot reach hub at {self.hub_url}: {e}") from e
            except httpx.TimeoutException as e:
                raise LibrisError(f"Hub request timed out after {self.timeout}s") from e

    async def pull(self, last_sync: Optional[str] = None,
                   domains: Optional[list[str]] = None,
                   limit: int = 1000) -> dict:
        """Pull approved knowledge from the Libris hub.

        Args:
            last_sync: ISO timestamp — only return triples approved after this
            domains: Domain filter (e.g. ["general", "code_reviewer"])
            limit: Max entities to return

        Returns:
            JSON-LD bundle with entities and relations.
        """
        params = {"limit": limit}
        if last_sync:
            params["last_sync"] = last_sync
        if domains:
            params["domains"] = ",".join(domains)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(
                    f"{self.hub_url}/v1/federation/pull",
                    params=params,
                    headers=self._headers(),
                )
                if resp.status_code != 200:
                    raise LibrisError(
                        f"Pull failed: HTTP {resp.status_code} — {resp.text[:200]}",
                        code=resp.status_code,
                    )
                return resp.json()
            except httpx.ConnectError as e:
                raise LibrisError(f"Cannot reach hub at {self.hub_url}: {e}") from e
            except httpx.TimeoutException as e:
                raise LibrisError(f"Hub request timed out after {self.timeout}s") from e

    async def handshake(self, name: str, url: str, domains: list[str]) -> dict:
        """Initiate a handshake with the hub.

        Args:
            name: Human-readable name for this node
            url: Public URL of this MoE Sovereign instance
            domains: Domains this node wants to share

        Returns:
            Hub response with handshake status.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.hub_url}/v1/federation/handshake",
                json={
                    "node_id": self.node_id,
                    "name": name,
                    "url": url,
                    "domains": domains,
                },
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                raise LibrisError(
                    f"Handshake failed: HTTP {resp.status_code} — {resp.text[:200]}",
                    code=resp.status_code,
                )
            return resp.json()

    async def health(self) -> dict:
        """Check hub health and verify connectivity."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(f"{self.hub_url}/health")
                if resp.status_code == 200:
                    return {"status": "ok", **resp.json()}
                return {"status": "error", "code": resp.status_code}
            except Exception as e:
                return {"status": "unreachable", "error": str(e)}


class LibrisError(Exception):
    """Error communicating with a MoE Libris hub."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code
