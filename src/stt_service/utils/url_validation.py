"""URL validation to prevent SSRF attacks."""

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("10.0.0.0/8"),         # Private class A
    ipaddress.ip_network("172.16.0.0/12"),      # Private class B
    ipaddress.ip_network("192.168.0.0/16"),     # Private class C
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),          # "This" network
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


def validate_external_url(url: str) -> str:
    """Validate that a URL is safe to make requests to.

    Checks:
    - Scheme is http or https
    - Hostname resolves to a public (non-private) IP

    Args:
        url: The URL to validate.

    Returns:
        The validated URL (unchanged).

    Raises:
        ValueError: If the URL is unsafe.
    """
    parsed = urlparse(url)

    # Scheme check
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL scheme must be http or https, got '{parsed.scheme}'"
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must include a hostname")

    # Resolve hostname to IP and check against blocked ranges
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {hostname}")

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise ValueError(
                    f"URL resolves to blocked address: {ip}"
                )

    return url
