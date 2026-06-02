"""Allowlisted HTTP for optional local connector URLs only — blocks arbitrary internet egress."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from loguru import logger

_DEFAULT_HOSTS = "127.0.0.1,localhost,::1"
_DEFAULT_SCHEMES = "http,https"


def _parse_hosts_env(raw: str) -> frozenset[str]:
    out = []
    for part in (raw or "").split(","):
        p = part.strip().lower()
        if p:
            out.append(p)
    return frozenset(out)


def _parse_cidr_env(raw: str) -> tuple:
    nets = []
    for part in (raw or "").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            nets.append(ipaddress.ip_network(p, strict=False))
        except ValueError:
            logger.warning(f"[local_tool_executor] Ignoring bad CIDR: {p!r}")
    return tuple(nets)


def tool_http_allow_hosts() -> frozenset[str]:
    return _parse_hosts_env(os.environ.get("TOOL_HTTP_ALLOW_HOSTS", _DEFAULT_HOSTS))


def tool_http_allow_schemes() -> frozenset[str]:
    return _parse_hosts_env(os.environ.get("TOOL_HTTP_ALLOW_SCHEMES", _DEFAULT_SCHEMES))


def tool_http_allow_private_lan() -> bool:
    return os.environ.get("TOOL_HTTP_ALLOW_PRIVATE_LAN", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def tool_http_extra_cidrs() -> tuple:
    return _parse_cidr_env(os.environ.get("TOOL_HTTP_ALLOW_CIDRS", ""))


def _hostname_allowed(host: str) -> bool:
    h = host.strip().lower().strip("[]")
    if h in tool_http_allow_hosts():
        return True
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_loopback:
            return True
        if tool_http_allow_private_lan():
            if ip.is_private or ip.is_link_local:
                return True
        for net in tool_http_extra_cidrs():
            if ip in net:
                return True
    except ValueError:
        pass
    return False


def _ip_allowed(ip: ipaddress._BaseAddress) -> bool:
    if ip.is_loopback:
        return True
    if tool_http_allow_private_lan() and (ip.is_private or ip.is_link_local):
        return True
    for net in tool_http_extra_cidrs():
        if ip in net:
            return True
    return False


def _resolve_check(hostname: str) -> bool:
    """Reject hostnames that resolve to global (internet) addresses unless LAN mode allows."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as e:
        logger.warning(f"[local_tool_executor] DNS resolve failed for {hostname!r}: {e}")
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not _ip_allowed(ip) and ip.is_global:
            logger.warning(f"[local_tool_executor] Rejecting resolved global IP {addr} for {hostname!r}")
            return False
    return True


def assert_url_allowed(url: str, *, context: str = "") -> None:
    """Raise ValueError if URL is not permitted for tool HTTP."""
    p = urlparse(url)
    if p.scheme.lower() not in {s for s in tool_http_allow_schemes()}:
        raise ValueError(f"Scheme not allowed: {p.scheme!r} ({context})")
    host = p.hostname
    if not host:
        raise ValueError(f"Missing host ({context})")
    if not _hostname_allowed(host):
        raise ValueError(f"Host not allowlisted: {host!r} ({context})")
    if not _resolve_check(host):
        raise ValueError(f"Host resolves to disallowed addresses: {host!r} ({context})")


async def http_request_allowed(
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    timeout: float = 60.0,
    context: str = "",
) -> httpx.Response:
    assert_url_allowed(url, context=context)
    m = method.upper()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        if m == "GET":
            return await client.get(url)
        if m == "POST":
            return await client.post(url, json=json_body)
        if m == "PUT":
            return await client.put(url, json=json_body)
        raise ValueError(f"Method not allowed: {method}")


def validate_user_connector_url(url: str | None, *, label: str) -> str | None:
    """Return trimmed URL or None; raises ValueError if set but not allowed."""
    if not url or not str(url).strip():
        return None
    u = str(url).strip()
    assert_url_allowed(u, context=label)
    return u
