from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

from kb_feed.errors import UrlRejected


Resolver = Callable[[str, int | None, int, int, int, int], list[tuple]]


def assert_public_hostname(hostname: str, resolver: Resolver = socket.getaddrinfo) -> None:
    if not hostname:
        raise UrlRejected("URL must include a hostname.")

    try:
        results = resolver(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, 0)
    except OSError as exc:
        raise UrlRejected(f"Could not resolve hostname '{hostname}'.") from exc

    addresses = {
        result[4][0]
        for result in results
        if len(result) >= 5 and result[4]
    }
    if not addresses:
        raise UrlRejected(f"Could not resolve hostname '{hostname}'.")

    blocked = [address for address in addresses if not is_public_address(address)]
    if blocked:
        raise UrlRejected(f"URL resolves to a private or reserved address: {blocked[0]}")


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    carrier_nat = ipaddress.ip_network("100.64.0.0/10")
    cloud_metadata = ipaddress.ip_network("169.254.169.254/32")
    if ip in carrier_nat or ip in cloud_metadata:
        return False
    return ip.is_global
