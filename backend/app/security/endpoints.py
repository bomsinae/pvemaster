import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from urllib.parse import urlsplit

from app.core.errors import AppError

ResolvedAddress = IPv4Address | IPv6Address
EndpointResolver = Callable[[str, int], Awaitable[Sequence[str]]]


class ProxmoxEndpointPolicy:
    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str],
        allowed_networks: Sequence[str],
        resolver: EndpointResolver | None = None,
    ) -> None:
        self._allowed_hosts = {self._normalize_host(host) for host in allowed_hosts}
        try:
            self._allowed_networks = tuple(
                ip_network(value, strict=False) for value in allowed_networks
            )
        except ValueError as exc:
            raise ValueError("PVE_ALLOWED_NETWORKS contains an invalid CIDR") from exc
        self._resolver = resolver or self._resolve

    async def validate(self, endpoint: str) -> None:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        if parsed.scheme != "https" or host is None:
            raise self._not_allowed()
        normalized_host = self._normalize_host(host)
        port = parsed.port or 443
        addresses = await self._addresses(normalized_host, port)
        if not addresses or any(self._always_forbidden(address) for address in addresses):
            raise self._not_allowed()

        host_allowed = normalized_host in self._allowed_hosts
        addresses_allowed = all(
            any(address in network for network in self._allowed_networks) for address in addresses
        )
        if not host_allowed and not addresses_allowed:
            raise self._not_allowed()

    async def _addresses(self, host: str, port: int) -> tuple[ResolvedAddress, ...]:
        try:
            return (ip_address(host),)
        except ValueError:
            pass
        try:
            values = await self._resolver(host, port)
            return tuple({ip_address(value) for value in values})
        except (OSError, ValueError):
            raise AppError(
                status_code=422,
                code="PVE_ENDPOINT_UNRESOLVABLE",
                message="The Proxmox endpoint could not be resolved safely.",
            ) from None

    @staticmethod
    async def _resolve(host: str, port: int) -> Sequence[str]:
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return [str(record[4][0]) for record in records]

    @staticmethod
    def _always_forbidden(address: ResolvedAddress) -> bool:
        return any(
            (
                address.is_unspecified,
                address.is_loopback,
                address.is_link_local,
                address.is_multicast,
                address.is_reserved,
            )
        )

    @staticmethod
    def _normalize_host(host: str) -> str:
        return host.strip().lower().rstrip(".")

    @staticmethod
    def _not_allowed() -> AppError:
        return AppError(
            status_code=422,
            code="PVE_ENDPOINT_NOT_ALLOWED",
            message="The Proxmox endpoint is outside the configured management network policy.",
        )
