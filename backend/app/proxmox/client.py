import ssl
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Origin, Subprotocol

from app.core.errors import AppError
from app.security.endpoints import EndpointResolver, ProxmoxEndpointPolicy


@dataclass(frozen=True)
class ConsoleProxyTicket:
    kind: str
    node: str
    vmid: int
    port: int
    ticket: str
    user: str | None = None


class ProxmoxClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        token_identifier: str,
        token_secret: str,
        connect_timeout: float,
        read_timeout: float,
        max_connections: int,
        max_keepalive_connections: int,
        allowed_hosts: list[str] | None = None,
        allowed_networks: list[str] | None = None,
        ca_bundle_pem: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        endpoint_resolver: EndpointResolver | None = None,
    ) -> None:
        ssl_context = ssl.create_default_context()
        if ca_bundle_pem is not None:
            try:
                ssl_context.load_verify_locations(cadata=ca_bundle_pem)
            except ssl.SSLError as exc:
                raise AppError(
                    status_code=422,
                    code="INVALID_CA_BUNDLE",
                    message="The custom CA bundle is not a valid PEM certificate.",
                ) from exc

        normalized_base_url = api_base_url.rstrip("/")
        self._endpoint_policy = ProxmoxEndpointPolicy(
            allowed_hosts=allowed_hosts or [],
            allowed_networks=allowed_networks or [],
            resolver=endpoint_resolver,
        )
        self._api_base_url = normalized_base_url
        self._ssl_context = ssl_context
        self._authorization = f"PVEAPIToken={token_identifier}={token_secret}"
        self._client = httpx.AsyncClient(
            base_url=normalized_base_url,
            headers={
                "Authorization": self._authorization,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
            ),
            verify=ssl_context,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def __aenter__(self) -> "ProxmoxClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def test_connection(self) -> dict[str, Any]:
        version = cast(
            dict[str, Any],
            await self._request_data("/api2/json/version", expected_type=dict),
        )
        await self._request_data("/api2/json/nodes", expected_type=list)
        await self._request_data(
            "/api2/json/cluster/resources", params={"type": "vm"}, expected_type=list
        )
        await self._request_data(
            "/api2/json/cluster/resources", params={"type": "storage"}, expected_type=list
        )
        return {
            "version": version.get("version"),
            "release": version.get("release"),
            "capabilities": {"nodes": True, "guests": True, "storages": True},
        }

    async def get_nodes(self) -> list[dict[str, Any]]:
        data = await self._request_data("/api2/json/nodes", expected_type=list)
        return self._require_object_list(data)

    async def get_node_status(self, *, node: str) -> dict[str, Any]:
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/status",
            expected_type=dict,
        )
        return cast(dict[str, Any], data)

    async def get_node_rrd_data(
        self,
        *,
        node: str,
        timeframe: str,
    ) -> list[dict[str, Any]]:
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/rrddata",
            params={"timeframe": timeframe, "cf": "AVERAGE"},
            expected_type=list,
        )
        return self._require_object_list(data)

    async def get_guest_rrd_data(
        self,
        *,
        kind: str,
        node: str,
        vmid: int,
        timeframe: str,
    ) -> list[dict[str, Any]]:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/rrddata",
            params={"timeframe": timeframe, "cf": "AVERAGE"},
            expected_type=list,
        )
        return self._require_object_list(data)

    async def get_guests(self) -> list[dict[str, Any]]:
        data = await self._request_data(
            "/api2/json/cluster/resources",
            params={"type": "vm"},
            expected_type=list,
        )
        return self._require_object_list(data)

    async def get_storages(self) -> list[dict[str, Any]]:
        data = await self._request_data(
            "/api2/json/cluster/resources",
            params={"type": "storage"},
            expected_type=list,
        )
        return self._require_object_list(data)

    async def get_storage_configurations(self) -> list[dict[str, Any]]:
        data = await self._request_data("/api2/json/storage", expected_type=list)
        return self._require_object_list(data)

    async def get_node_storages(self, *, node: str) -> list[dict[str, Any]]:
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/storage",
            expected_type=list,
        )
        return self._require_object_list(data)

    async def get_vm_status(self, *, node: str, vmid: int) -> dict[str, Any]:
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/qemu/{vmid}/status/current",
            expected_type=dict,
        )
        return cast(dict[str, Any], data)

    async def get_guest_status(self, *, kind: str, node: str, vmid: int) -> dict[str, Any]:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/status/current",
            expected_type=dict,
        )
        return cast(dict[str, Any], data)

    async def get_guest_config(self, *, kind: str, node: str, vmid: int) -> dict[str, Any]:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/config",
            expected_type=dict,
        )
        return cast(dict[str, Any], data)

    async def create_console_proxy(self, *, kind: str, node: str, vmid: int) -> ConsoleProxyTicket:
        guest_type = self._guest_type(kind)
        proxy_endpoint = "vncproxy" if guest_type == "qemu" else "termproxy"
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/{proxy_endpoint}",
            expected_type=dict,
            method="POST",
            data={"websocket": "1"} if guest_type == "qemu" else None,
        )
        if not isinstance(data, dict):
            raise self._invalid_response()
        ticket = data.get("ticket")
        user_value = data.get("user")
        port_value = data.get("port")
        if not isinstance(port_value, (int, str)):
            raise self._invalid_response()
        try:
            port = int(port_value)
        except (TypeError, ValueError) as exc:
            raise self._invalid_response() from exc
        if not isinstance(ticket, str) or not ticket or len(ticket) > 4096:
            raise self._invalid_response()
        if guest_type == "lxc" and (
            not isinstance(user_value, str) or not user_value or len(user_value) > 255
        ):
            raise self._invalid_response()
        if not 1 <= port <= 65535:
            raise self._invalid_response()
        return ConsoleProxyTicket(
            kind=guest_type,
            node=node,
            vmid=vmid,
            port=port,
            ticket=ticket,
            user=user_value if isinstance(user_value, str) else None,
        )

    async def open_console_websocket(
        self,
        proxy: ConsoleProxyTicket,
        *,
        open_timeout: float,
    ) -> ClientConnection:
        await self._endpoint_policy.validate(self._api_base_url)
        base = urlsplit(self._api_base_url)
        base_path = base.path.rstrip("/")
        websocket_path = (
            f"{base_path}/api2/json/nodes/{quote(proxy.node, safe='')}"
            f"/{proxy.kind}/{proxy.vmid}/vncwebsocket"
        )
        uri = urlunsplit(
            (
                "wss" if base.scheme == "https" else "ws",
                base.netloc,
                websocket_path,
                urlencode({"port": str(proxy.port), "vncticket": proxy.ticket}),
                "",
            )
        )
        origin = urlunsplit((base.scheme, base.netloc, "", "", ""))
        return await connect(
            uri,
            additional_headers={"Authorization": self._authorization},
            origin=cast(Origin, origin),
            subprotocols=[cast(Subprotocol, "binary")],
            ssl=self._ssl_context if base.scheme == "https" else None,
            open_timeout=open_timeout,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
            proxy=None,
        )

    async def configure_guest(
        self, *, kind: str, node: str, vmid: int, cores: int, memory_mib: int
    ) -> None:
        guest_type = self._guest_type(kind)
        await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/config",
            expected_type=str,
            method="PUT",
            data={"cores": str(cores), "memory": str(memory_mib)},
            allow_null_data=True,
        )

    async def resize_guest_disk(
        self, *, kind: str, node: str, vmid: int, disk: str, size_bytes: int
    ) -> None:
        guest_type = self._guest_type(kind)
        await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/resize",
            expected_type=str,
            method="PUT",
            data={"disk": disk, "size": str(size_bytes)},
            allow_null_data=True,
        )

    async def delete_guest(self, *, kind: str, node: str, vmid: int) -> str:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}",
            expected_type=str,
            method="DELETE",
        )
        return self._require_upid(data)

    @staticmethod
    def _guest_type(kind: str) -> str:
        if kind == "QEMU":
            return "qemu"
        if kind == "LXC":
            return "lxc"
        raise ValueError("unsupported guest kind")

    async def submit_vm_power_action(
        self,
        *,
        node: str,
        vmid: int,
        action: str,
    ) -> str:
        return await self.submit_guest_power_action(
            kind="QEMU", node=node, vmid=vmid, action=action
        )

    async def submit_guest_power_action(
        self,
        *,
        kind: str,
        node: str,
        vmid: int,
        action: str,
    ) -> str:
        allowed_actions = {"start", "shutdown", "stop", "reboot"}
        if kind == "QEMU":
            allowed_actions.add("reset")
        if action not in allowed_actions:
            raise ValueError("unsupported guest power action")
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/status/{action}",
            expected_type=str,
            method="POST",
        )
        if not isinstance(data, str) or not data.startswith("UPID:") or len(data) > 2048:
            raise self._invalid_response()
        return data

    async def get_guest_snapshots(self, *, kind: str, node: str, vmid: int) -> list[dict[str, Any]]:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/snapshot",
            expected_type=list,
        )
        return self._require_object_list(data)

    async def submit_guest_snapshot(
        self,
        *,
        kind: str,
        node: str,
        vmid: int,
        snapshot_name: str,
        include_memory: bool,
    ) -> str:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/snapshot",
            expected_type=str,
            method="POST",
            data={
                "snapname": snapshot_name,
                "vmstate": "1" if include_memory else "0",
            },
        )
        return self._require_upid(data)

    async def delete_guest_snapshot(
        self, *, kind: str, node: str, vmid: int, snapshot_name: str
    ) -> str:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            (
                f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}"
                f"/snapshot/{quote(snapshot_name, safe='')}"
            ),
            expected_type=str,
            method="DELETE",
        )
        return self._require_upid(data)

    async def rollback_guest_snapshot(
        self, *, kind: str, node: str, vmid: int, snapshot_name: str
    ) -> str:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            (
                f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}"
                f"/snapshot/{quote(snapshot_name, safe='')}/rollback"
            ),
            expected_type=str,
            method="POST",
        )
        return self._require_upid(data)

    async def migrate_guest(
        self,
        *,
        kind: str,
        node: str,
        vmid: int,
        target_node: str,
        online: bool,
        target_storage: str | None,
        target_network: str | None,
    ) -> str:
        guest_type = self._guest_type(kind)
        payload = {
            "target": target_node,
            "online": "1" if online else "0",
            "with-local-disks": "1",
        }
        if target_storage:
            payload["targetstorage"] = target_storage
        if target_network:
            payload["migration_network"] = target_network
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/migrate",
            expected_type=str,
            method="POST",
            data=payload,
        )
        return self._require_upid(data)

    async def get_ha_resources(self) -> list[dict[str, Any]]:
        data = await self._request_data(
            "/api2/json/cluster/ha/resources",
            expected_type=list,
        )
        return self._require_object_list(data)

    async def get_ha_groups(self) -> list[dict[str, Any]]:
        data = await self._request_data(
            "/api2/json/cluster/ha/groups",
            expected_type=list,
        )
        return self._require_object_list(data)

    async def update_ha_resource(self, *, resource_id: str, state: str, group: str | None) -> None:
        payload = {"state": state}
        if group:
            payload["group"] = group
        await self._request_data(
            f"/api2/json/cluster/ha/resources/{quote(resource_id, safe='')}",
            expected_type=str,
            method="PUT",
            data=payload,
            allow_null_data=True,
        )

    async def configure_guest_advanced(
        self, *, kind: str, node: str, vmid: int, values: dict[str, str]
    ) -> None:
        allowed = {"cores", "memory", "net0", "boot", "ciuser", "nameserver", "ipconfig0"}
        if not values or not set(values).issubset(allowed):
            raise ValueError("unsupported advanced guest configuration")
        guest_type = self._guest_type(kind)
        await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/config",
            expected_type=str,
            method="PUT",
            data=values,
            allow_null_data=True,
        )

    async def get_guest_firewall_rules(
        self, *, kind: str, node: str, vmid: int
    ) -> list[dict[str, Any]]:
        guest_type = self._guest_type(kind)
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}/{vmid}/firewall/rules",
            expected_type=list,
        )
        return self._require_object_list(data)

    async def get_sdn_resources(self) -> dict[str, list[dict[str, Any]]]:
        zones = await self._request_data(
            "/api2/json/cluster/sdn/zones",
            expected_type=list,
        )
        vnets = await self._request_data(
            "/api2/json/cluster/sdn/vnets",
            expected_type=list,
        )
        return {
            "zones": self._require_object_list(zones),
            "vnets": self._require_object_list(vnets),
        }

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]:
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/tasks/{quote(upid, safe='')}/status",
            expected_type=dict,
        )
        return cast(dict[str, Any], data)

    async def submit_guest_backup(
        self,
        *,
        node: str,
        vmid: int,
        storage: str,
        mode: str = "snapshot",
        compression: str = "zstd",
    ) -> str:
        if mode != "snapshot" or compression != "zstd":
            raise ValueError("unsupported backup option")
        if vmid <= 0 or not storage or len(storage) > 255:
            raise ValueError("invalid backup target")
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/vzdump",
            expected_type=str,
            method="POST",
            data={
                "vmid": str(vmid),
                "storage": storage,
                "mode": mode,
                "compress": compression,
            },
        )
        return self._require_upid(data)

    async def get_backup_content(
        self,
        *,
        node: str,
        storage: str,
        vmid: int,
    ) -> list[dict[str, Any]]:
        if vmid <= 0 or not storage or len(storage) > 255:
            raise ValueError("invalid backup target")
        data = await self._request_data(
            (f"/api2/json/nodes/{quote(node, safe='')}/storage/{quote(storage, safe='')}/content"),
            params={"content": "backup", "vmid": str(vmid)},
            expected_type=list,
        )
        return self._require_object_list(data)

    async def submit_guest_restore(
        self,
        *,
        kind: str,
        node: str,
        archive: str,
        vmid: int,
        name: str,
    ) -> str:
        guest_type = self._guest_type(kind)
        if vmid < 100 or not archive or len(archive) > 1024 or not name or len(name) > 63:
            raise ValueError("invalid restore target")
        payload = {
            "vmid": str(vmid),
            "start": "0",
        }
        if guest_type == "qemu":
            payload.update({"archive": archive, "name": name, "unique": "1"})
        else:
            payload.update({"ostemplate": archive, "hostname": name, "restore": "1"})
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/{guest_type}",
            expected_type=str,
            method="POST",
            data=payload,
        )
        return self._require_upid(data)

    async def get_task_log(self, *, node: str, upid: str) -> list[dict[str, Any]]:
        if not upid or len(upid) > 2048:
            raise ValueError("invalid PVE task identifier")
        data = await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/tasks/{quote(upid, safe='')}/log",
            params={"start": "0", "limit": "500"},
            expected_type=list,
        )
        return self._require_object_list(data)

    async def clone_qemu_template(
        self,
        *,
        source_node: str,
        source_vmid: int,
        target_node: str,
        target_vmid: int,
        name: str,
        storage: str,
    ) -> str:
        data = await self._request_data(
            f"/api2/json/nodes/{quote(source_node, safe='')}/qemu/{source_vmid}/clone",
            expected_type=str,
            method="POST",
            data={
                "newid": str(target_vmid),
                "node": target_node,
                "name": name,
                "storage": storage,
                "full": "1",
            },
        )
        return self._require_upid(data)

    async def configure_qemu(self, *, node: str, vmid: int, values: dict[str, str]) -> None:
        allowed = {"cores", "memory", "net0", "ipconfig0", "nameserver", "ciuser", "sshkeys"}
        if not values or not set(values).issubset(allowed):
            raise ValueError("unsupported QEMU configuration parameter")
        payload = dict(values)
        # Proxmox validates sshkeys as a URL-encoded string after decoding the
        # HTTP form body, so it must be encoded once before httpx form-encodes it.
        if "sshkeys" in payload:
            payload["sshkeys"] = quote(payload["sshkeys"], safe="")
        await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/qemu/{vmid}/config",
            expected_type=str,
            method="PUT",
            data=payload,
            allow_null_data=True,
        )

    async def resize_qemu_disk(self, *, node: str, vmid: int, disk: str, size_bytes: int) -> None:
        await self._request_data(
            f"/api2/json/nodes/{quote(node, safe='')}/qemu/{vmid}/resize",
            expected_type=str,
            method="PUT",
            data={"disk": disk, "size": str(size_bytes)},
            allow_null_data=True,
        )

    async def _request_data(
        self,
        path: str,
        *,
        expected_type: type[dict[str, Any]] | type[list[Any]] | type[str],
        params: dict[str, str] | None = None,
        method: str = "GET",
        data: dict[str, str] | None = None,
        allow_null_data: bool = False,
    ) -> dict[str, Any] | list[Any] | str:
        await self._endpoint_policy.validate(self._api_base_url)
        try:
            response = await self._client.request(method, path, params=params, data=data)
        except httpx.TimeoutException as exc:
            raise AppError(
                status_code=504,
                code="PVE_TIMEOUT",
                message="The Proxmox API did not respond within the configured timeout.",
            ) from exc
        except httpx.ConnectError as exc:
            if self._is_tls_error(exc):
                raise AppError(
                    status_code=502,
                    code="PVE_TLS_ERROR",
                    message="The Proxmox TLS certificate could not be verified.",
                ) from exc
            raise AppError(
                status_code=503,
                code="CLUSTER_UNREACHABLE",
                message="The Proxmox cluster is unreachable.",
            ) from exc
        except httpx.NetworkError as exc:
            raise AppError(
                status_code=503,
                code="CLUSTER_UNREACHABLE",
                message="The Proxmox cluster is unreachable.",
            ) from exc

        if response.status_code == 401:
            raise AppError(
                status_code=401,
                code="PVE_AUTH_FAILED",
                message="The Proxmox API token was rejected.",
            )
        if response.status_code == 403:
            raise AppError(
                status_code=403,
                code="PVE_PERMISSION_DENIED",
                message="The Proxmox API token lacks a required permission.",
            )
        if response.status_code == 429:
            raise AppError(
                status_code=503,
                code="PVE_RATE_LIMITED",
                message="The Proxmox API rate limit was reached.",
            )
        if response.status_code >= 500:
            raise AppError(
                status_code=502,
                code="PVE_UPSTREAM_ERROR",
                message="The Proxmox API returned a server error.",
            )
        if response.status_code >= 400:
            raise AppError(
                status_code=502,
                code="PVE_INVALID_RESPONSE",
                message="The Proxmox API returned an unexpected response.",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise self._invalid_response() from exc
        if not isinstance(payload, dict) or "data" not in payload:
            raise self._invalid_response()
        response_data = payload["data"]
        if allow_null_data and response_data is None:
            return ""
        if not isinstance(response_data, expected_type):
            raise self._invalid_response()
        return response_data

    @staticmethod
    def _require_upid(data: object) -> str:
        if not isinstance(data, str) or not data.startswith("UPID:") or len(data) > 2048:
            raise ProxmoxClient._invalid_response()
        return data

    @staticmethod
    def _require_object_list(data: dict[str, Any] | list[Any] | str) -> list[dict[str, Any]]:
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ProxmoxClient._invalid_response()
        return data

    @staticmethod
    def _invalid_response() -> AppError:
        return AppError(
            status_code=502,
            code="PVE_INVALID_RESPONSE",
            message="The Proxmox API returned an invalid response body.",
        )

    @staticmethod
    def _is_tls_error(error: BaseException) -> bool:
        current: BaseException | None = error
        while current is not None:
            if isinstance(current, ssl.SSLError):
                return True
            current = current.__cause__ or current.__context__
        return False
