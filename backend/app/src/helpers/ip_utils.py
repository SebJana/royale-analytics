import ipaddress

from fastapi import Request


async def rate_limit_key_func(request: Request) -> str:
    """Key limits by the trusted client's IP prefix and the matched endpoint.

    The prefix (see client_ip_prefix) and not the full address: one IPv6
    connection holds a whole /64, and a limit per address would hand it a
    fresh budget for every address it rotates through.
    """
    route = request.scope["route"]
    return f"{client_ip_prefix(request)}:{request.method}:{route.path}"


def client_ip_prefix(request: Request) -> str:
    """The part of the client address that one connection controls.

    One home connection usually gets a whole IPv6 /64 (often more), so keyed
    by the full address a single client could rotate through 2^64 identities
    for free. An IPv4-mapped IPv6 address counts as its IPv4 address.

    Args:
        request (Request): The request whose trusted client address is used
            (nginx and uvicorn resolve it, see frontend/nginx.conf).

    Returns:
        str: The IPv4 address, the IPv6 /64 network, or the raw host if it is
        not an IP address (e.g. "testclient").
    """

    host = request.client.host if request.client else "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, 64), strict=False))
    return str(address)


def client_network(request: Request) -> str:
    """The network the client's address belongs to, as one operator holds it.

    An IPv4 /24 is the smallest range commonly rented out, and an IPv6 /48
    the usual assignment of one site. Many addresses of one range point at
    one operator rather than many visitors.

    Args:
        request (Request): The request whose trusted client address is used.

    Returns:
        str: The IPv4 /24 or IPv6 /48 network, or the raw host if it is not
        an IP address.
    """

    prefix = client_ip_prefix(request)
    try:
        address = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return prefix
    bits = 24 if address.version == 4 else 48
    return str(address.supernet(new_prefix=bits))
