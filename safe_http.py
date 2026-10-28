"""
safe_http.py — outbound HTTP to user-supplied URLs (webhooks) without SSRF.

Validating a URL when it is saved is not enough: DNS can change between
save and send (rebinding), and a public endpoint can answer with a redirect
to an internal address. So every delivery:

  1. parses the URL: https only, no userinfo, port in ALLOWED_PORTS;
  2. resolves the hostname NOW and rejects it if ANY address is not a
     public unicast address (loopback, RFC1918, link-local / cloud
     metadata 169.254.0.0/16, CGNAT 100.64/10, ULA, multicast, reserved,
     IPv4-mapped forms of those…);
  3. connects to the exact IP it just validated (TLS SNI + certificate
     verification still use the hostname), so a second DNS answer can't
     swap the destination;
  4. never follows redirects (a 3xx is a failed delivery);
  5. bounds request size, response read size and time.

If the deployment routes egress through an HTTP proxy, the proxy resolves
DNS itself and step 3 no longer pins the IP — the proxy must then enforce
an equivalent destination policy. Documented in DEPLOY.md.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
from typing import Callable, Optional
from urllib.parse import urlsplit

import urllib3

ALLOWED_PORTS = {443, 8443}
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
TIMEOUT_S = 8.0


class UnsafeDestination(Exception):
    """The URL or the address it resolves to is not allowed."""


class DeliveryError(Exception):
    """The request was made but the destination did not accept it.
    `retryable` distinguishes transient (5xx/429/network) from permanent."""

    def __init__(self, msg: str, retryable: bool, status: Optional[int] = None):
        super().__init__(msg)
        self.retryable = retryable
        self.status = status


def is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None or ip.teredo is not None:
            return False
    return bool(ip.is_global) and not ip.is_multicast


def check_url(url: str, *, allow_http: bool = False) -> tuple[str, str, int, str]:
    """Static checks. Returns (scheme, host, port, path_with_query)."""
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeDestination("URL missing or too long")
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise UnsafeDestination("malformed URL") from None
    scheme = (parts.scheme or "").lower()
    if scheme != "https" and not (allow_http and scheme == "http"):
        raise UnsafeDestination("webhook must be an https:// URL")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise UnsafeDestination("credentials in URL are not allowed")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeDestination("URL has no host")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise UnsafeDestination("invalid port") from None
    if scheme == "https" and port not in ALLOWED_PORTS:
        raise UnsafeDestination(f"port {port} not allowed")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, port, path


def resolve_checked(host: str, port: int,
                    policy: Callable[[ipaddress._BaseAddress], bool] = is_public_ip) -> str:
    """Resolve `host` now; every returned address must pass `policy`.
    Returns the first address to connect to."""
    try:
        ip_literal = ipaddress.ip_address(host)
        if not policy(ip_literal):
            raise UnsafeDestination("destination address is not public")
        return str(ip_literal)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeDestination("host does not resolve") from None
    addrs = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not policy(ip):
            raise UnsafeDestination("host resolves to a non-public address")
        addrs.append(str(ip))
    if not addrs:
        raise UnsafeDestination("host does not resolve")
    return addrs[0]


def validate_destination(url: str) -> Optional[str]:
    """Save-time check. Returns an error message or None."""
    try:
        _, host, port, _ = check_url(url)
        resolve_checked(host, port)
    except UnsafeDestination as e:
        return str(e)
    return None


def post_json(url: str, payload: dict, *, timeout: float = TIMEOUT_S,
              policy: Callable[[ipaddress._BaseAddress], bool] = is_public_ip,
              allow_http: bool = False) -> int:
    """POST JSON to a user-supplied URL with connect-time SSRF checks.
    Returns the HTTP status on 2xx; raises UnsafeDestination or
    DeliveryError otherwise. `policy`/`allow_http` exist for tests."""
    scheme, host, port, path = check_url(url, allow_http=allow_http)
    body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise UnsafeDestination(f"payload {len(body)} bytes exceeds {MAX_REQUEST_BYTES}")
    ip = resolve_checked(host, port, policy)

    t = urllib3.Timeout(connect=timeout, read=timeout)
    host_header = host if port in (80, 443) else f"{host}:{port}"
    if scheme == "https":
        ctx = ssl.create_default_context()
        pool = urllib3.HTTPSConnectionPool(
            ip, port=port, timeout=t, retries=False, maxsize=1,
            server_hostname=host, assert_hostname=host, ssl_context=ctx)
    else:
        pool = urllib3.HTTPConnectionPool(ip, port=port, timeout=t,
                                          retries=False, maxsize=1)
    try:
        resp = pool.urlopen(
            "POST", path, body=body, redirect=False, retries=False,
            preload_content=False,
            headers={"Host": host_header, "Content-Type": "application/json",
                     "User-Agent": "CarSniper-Webhook/1"})
        try:
            resp.read(MAX_RESPONSE_BYTES)   # bounded; content ignored
        finally:
            resp.release_conn()
    except urllib3.exceptions.HTTPError as e:
        raise DeliveryError(f"network error: {type(e).__name__}", retryable=True) from None
    finally:
        pool.close()

    status = resp.status
    if 200 <= status < 300:
        return status
    if 300 <= status < 400:
        raise DeliveryError(f"redirect {status} not followed", retryable=False,
                            status=status)
    retryable = status == 429 or status >= 500
    raise DeliveryError(f"HTTP {status}", retryable=retryable, status=status)
