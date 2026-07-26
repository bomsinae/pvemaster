import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from app.core.errors import AppError

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


class WebhookEndpointPolicy:
    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str],
        allowed_networks: Sequence[str],
        resolver: Resolver | None = None,
    ) -> None:
        self._hosts = {item.strip().lower().rstrip(".") for item in allowed_hosts}
        self._networks = tuple(ip_network(item, strict=False) for item in allowed_networks)
        self._resolver = resolver or self._resolve

    async def validate(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise self._denied()
        host = parsed.hostname.lower().rstrip(".")
        try:
            addresses = [ip_address(host)]
        except ValueError:
            try:
                addresses = [
                    ip_address(item) for item in await self._resolver(host, parsed.port or 443)
                ]
            except (OSError, ValueError):
                raise self._denied() from None
        if not addresses or any(
            item.is_private
            or item.is_loopback
            or item.is_link_local
            or item.is_multicast
            or item.is_reserved
            or item.is_unspecified
            for item in addresses
        ):
            raise self._denied()
        if host not in self._hosts and not all(
            any(address in network for network in self._networks) for address in addresses
        ):
            raise self._denied()

    @staticmethod
    async def _resolve(host: str, port: int) -> Sequence[str]:
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return [str(item[4][0]) for item in records]

    @staticmethod
    def _denied() -> AppError:
        return AppError(
            422,
            "WEBHOOK_ENDPOINT_NOT_ALLOWED",
            "The webhook endpoint is outside the notification allowlist.",
        )
