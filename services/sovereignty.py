"""
services/sovereignty.py — Egress guard for local-only routing.

When a request is flagged local_only, every outbound LLM call must target a
private/allowlisted host. Violations raise EgressDenied BEFORE any payload
leaves the system — configuration mistakes must fail loudly, not leak
silently.

Call assert_egress_allowed() at the point a URL is about to be dispatched
(not only at candidate-selection time): candidate lists can be misconfigured
or bypassed by a new code path, but a check immediately before the network
call cannot be.

Allowlist extension: MOE_LOCAL_ALLOW_HOSTS="host1,host2" (exact hostnames).
"""

import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

logger = logging.getLogger("MOE-SOVEREIGN")


class EgressDenied(Exception):
    pass


_PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128",
)]


def _host_is_local(host: str) -> bool:
    allow = {h.strip() for h in os.getenv("MOE_LOCAL_ALLOW_HOSTS", "").split(",") if h.strip()}
    if host in allow:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not any(ip in net for net in _PRIVATE_NETS):
            return False
    return bool(infos)


def assert_egress_allowed(url: str, local_only: bool) -> None:
    """Raise EgressDenied when local_only is set and url is not private/allowlisted."""
    if not local_only:
        return
    host = urlparse(url).hostname or ""
    if not _host_is_local(host):
        logger.error("sovereignty: BLOCKED egress to %s (local_only key)", host)
        raise EgressDenied(
            f"local_only routing: endpoint '{host}' is not a local/allowlisted host"
        )


def resolve_local_only(user_perms: dict | None, user_ctx: dict | None) -> bool:
    """Resolve the single local_only decision for a request.

    Precedence: explicit permission flag > per-key flag > global compliance
    env var. Single source of truth so every API entry point (chat, Messages,
    streaming) freezes the same value onto AgentState once per request instead
    of re-deriving it ad hoc.
    """
    moe_modes = set((user_perms or {}).get("moe_mode", []))
    if "moe-auto:local-only" in moe_modes:
        return True
    if (user_ctx or {}).get("local_only_routing") == "1":
        return True
    return os.getenv("LOCAL_ONLY_COMPLIANCE", "false").lower() in ("1", "true", "yes")
