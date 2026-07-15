"""
services/sovereignty.py — Egress guard for local-only routing.

When a request's user context carries local_only_routing=1, every outbound
LLM call must target a private/allowlisted host. Violations raise
EgressDenied BEFORE any payload leaves the system — configuration mistakes
must fail loudly, not leak silently.

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


def assert_egress_allowed(url: str, user_ctx: dict | None) -> None:
    """Raise EgressDenied when local_only_routing is set and url is non-local."""
    if not user_ctx or user_ctx.get("local_only_routing") != "1":
        return
    host = urlparse(url).hostname or ""
    if not _host_is_local(host):
        logger.error("sovereignty: BLOCKED egress to %s (local_only key)", host)
        raise EgressDenied(
            f"local_only routing: endpoint '{host}' is not a local/allowlisted host"
        )
