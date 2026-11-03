import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from .config import Settings


class UrlNotAllowed(Exception):
    pass


def check_url_syntax(url: str, settings: Settings) -> str:
    candidate = (url or "").strip()
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise UrlNotAllowed(f"Only https links are supported: {candidate[:120]}")
    if scheme == "http" and not settings.allow_http:
        raise UrlNotAllowed(f"Plain http links are not allowed, use https: {candidate[:120]}")
    host = (parts.hostname or "").lower()
    if not host:
        raise UrlNotAllowed(f"The link has no host name: {candidate[:120]}")
    if parts.username or parts.password:
        raise UrlNotAllowed("Links with embedded credentials are not allowed.")
    allowed = settings.allowed_download_hosts
    if allowed and not any(host == item.lower() or host.endswith("." + item.lower()) for item in allowed):
        raise UrlNotAllowed(f"Downloads from {host} are not allowed.")
    return candidate


async def check_url_target(url: str, settings: Settings) -> None:
    check_url_syntax(url, settings)
    if settings.allow_private_urls:
        return
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConnectionError(f"Cannot resolve {host}: {exc}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise UrlNotAllowed(f"{host} resolves to a private or reserved address, which is not allowed.")
