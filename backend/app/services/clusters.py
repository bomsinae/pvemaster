import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.ipam import IpPool
from app.models.operation import Operation, OperationStatus, Workload
from app.models.provisioning import (
    ProvisioningNode,
    ProvisioningRequest,
    ProvisioningStatus,
    Template,
)
from app.proxmox.client import ProxmoxClient
from app.schemas.cluster import (
    ClusterCreate,
    ClusterRemovalBlock,
    ClusterRemovalCheckResponse,
    ClusterResourceOverview,
    ClusterResponse,
    ClusterUpdate,
    ConnectionTestResponse,
    CredentialSummary,
    GuestResponse,
    NodeMetricPoint,
    NodeMetricRange,
    NodeMetricSeriesResponse,
    NodeResourceOverview,
    NodeResponse,
    StorageResponse,
)
from app.schemas.workload import WorkloadImportResponse
from app.security.access import Principal, require_service_role
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event


@dataclass(frozen=True)
class ClusterConnection:
    cluster_id: UUID
    credential_id: UUID
    api_base_url: str
    ca_bundle_pem: str | None
    token_identifier: str
    token_secret: str


class ClusterService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        principal: Principal,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._transport = transport
        self._principal = principal
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)

    async def create(self, request: ClusterCreate) -> ClusterResponse:
        cluster_id = uuid4()
        credential_id = uuid4()
        connection = ClusterConnection(
            cluster_id=cluster_id,
            credential_id=credential_id,
            api_base_url=self._normalize_url(request.api_base_url),
            ca_bundle_pem=request.ca_bundle_pem,
            token_identifier=request.token_identifier,
            token_secret=request.token_secret.get_secret_value(),
        )
        await self._probe(connection)
        encrypted = self._cipher.encrypt(
            connection.token_secret,
            cluster_id=cluster_id,
            credential_id=credential_id,
        )
        now = datetime.now(UTC)
        cluster = Cluster(
            id=cluster_id,
            name=request.name.strip(),
            api_base_url=connection.api_base_url,
            ca_bundle_pem=request.ca_bundle_pem,
            is_active=True,
            last_connected_at=now,
            version=1,
        )
        credential = ClusterCredential(
            id=credential_id,
            cluster_id=cluster_id,
            token_identifier=request.token_identifier,
            secret_ciphertext=encrypted.ciphertext,
            secret_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            is_active=True,
            last_used_at=now,
        )
        cluster.credentials.append(credential)
        self._session.add(cluster)
        add_audit_event(
            self._session,
            action="CLUSTER_CREATED",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="cluster",
            target_id=cluster.id,
            after={
                "name": cluster.name,
                "api_base_url": cluster.api_base_url,
                "ca_configured": cluster.ca_bundle_pem is not None,
                "token_identifier": credential.token_identifier,
            },
        )
        await self._commit_or_conflict()
        await self._session.refresh(cluster)
        return self._to_response(cluster, credential)

    async def list_clusters(self) -> list[ClusterResponse]:
        result = await self._session.scalars(
            select(Cluster).where(Cluster.is_active.is_(True)).order_by(Cluster.created_at.desc())
        )
        clusters = result.unique().all()
        return [
            self._to_response(cluster, self._display_credential(cluster)) for cluster in clusters
        ]

    async def get(self, cluster_id: UUID) -> ClusterResponse:
        cluster = await self._get_cluster(cluster_id)
        return self._to_response(cluster, self._display_credential(cluster))

    async def resource_overview(self) -> list[ClusterResourceOverview]:
        result = await self._session.scalars(
            select(Cluster).where(Cluster.is_active.is_(True)).order_by(Cluster.name)
        )
        clusters = result.unique().all()
        observed_at = datetime.now(UTC)
        snapshots: list[tuple[UUID, str, ClusterConnection]] = []
        cluster_order: list[tuple[UUID, str]] = []
        overview_by_id: dict[UUID, ClusterResourceOverview] = {}

        for cluster in clusters:
            cluster_order.append((cluster.id, cluster.name))
            try:
                credential = self._active_credential(cluster)
                snapshots.append(
                    (
                        cluster.id,
                        cluster.name,
                        ClusterConnection(
                            cluster_id=cluster.id,
                            credential_id=credential.id,
                            api_base_url=cluster.api_base_url,
                            ca_bundle_pem=cluster.ca_bundle_pem,
                            token_identifier=credential.token_identifier,
                            token_secret=self._decrypt(cluster.id, credential),
                        ),
                    )
                )
            except AppError as exc:
                overview_by_id[cluster.id] = self._failed_resource_overview(
                    cluster_id=cluster.id,
                    name=cluster.name,
                    observed_at=observed_at,
                    error_code=exc.code,
                )

        await self._session.rollback()
        fetched = await asyncio.gather(
            *(
                self._fetch_resource_overview(cluster_id, name, connection)
                for cluster_id, name, connection in snapshots
            ),
            return_exceptions=True,
        )
        for (cluster_id, name, _connection), item in zip(snapshots, fetched, strict=True):
            if isinstance(item, AppError):
                overview_by_id[cluster_id] = self._failed_resource_overview(
                    cluster_id=cluster_id,
                    name=name,
                    observed_at=observed_at,
                    error_code=item.code,
                )
            elif isinstance(item, BaseException):
                raise item
            else:
                overview_by_id[cluster_id] = item

        for cluster_id, _name in cluster_order:
            item = overview_by_id[cluster_id]
            await self._record_connection_result(
                cluster_id,
                error_code=None if item.connected else item.error_code,
            )
        return [overview_by_id[cluster_id] for cluster_id, _name in cluster_order]

    async def _fetch_resource_overview(
        self,
        cluster_id: UUID,
        name: str,
        connection: ClusterConnection,
    ) -> ClusterResourceOverview:
        async with self._client(connection) as client:
            raw_nodes, raw_guests, raw_storages = await asyncio.gather(
                client.get_nodes(),
                client.get_guests(),
                client.get_storages(),
            )
            nodes = self._validate_items(NodeResponse, raw_nodes)
            guests = self._validate_items(GuestResponse, raw_guests)
            storages = self._validate_items(StorageResponse, raw_storages)
            node_statuses = await asyncio.gather(
                *(client.get_node_status(node=node.node) for node in nodes),
                return_exceptions=True,
            )

        node_overviews = [
            self._node_resource_overview(
                node,
                status if isinstance(status, dict) else {},
            )
            for node, status in zip(nodes, node_statuses, strict=True)
        ]
        unique_storages: dict[tuple[str, str | None], StorageResponse] = {}
        for storage in storages:
            key = (storage.storage, None if bool(storage.shared) else storage.node)
            unique_storages[key] = storage
        storage_used = sum(item.used or 0 for item in unique_storages.values())
        storage_total = sum(item.total or 0 for item in unique_storages.values())
        return ClusterResourceOverview(
            cluster_id=cluster_id,
            name=name,
            connected=True,
            observed_at=datetime.now(UTC),
            node_count=len(nodes),
            guest_count=len(guests),
            running_guest_count=sum(
                1 for guest in guests if (guest.status or "").lower() == "running"
            ),
            qemu_count=sum(1 for guest in guests if guest.type.lower() == "qemu"),
            lxc_count=sum(1 for guest in guests if guest.type.lower() == "lxc"),
            storage_count=len(unique_storages),
            storage_used_bytes=storage_used,
            storage_total_bytes=storage_total,
            nodes=node_overviews,
        )

    @classmethod
    def _node_resource_overview(
        cls,
        node: NodeResponse,
        status: dict[str, Any],
    ) -> NodeResourceOverview:
        memory_value = status.get("memory")
        rootfs_value = status.get("rootfs")
        memory: dict[str, Any] = memory_value if isinstance(memory_value, dict) else {}
        rootfs: dict[str, Any] = rootfs_value if isinstance(rootfs_value, dict) else {}
        cpu = cls._number(status.get("cpu"))
        load_average = status.get("loadavg")
        parsed_load = (
            [value for item in load_average[:3] if (value := cls._number(item)) is not None]
            if isinstance(load_average, list)
            else []
        )
        return NodeResourceOverview(
            node=node.node,
            status=node.status,
            cpu=cpu if cpu is not None else node.cpu,
            maxcpu=node.maxcpu,
            memory_used_bytes=cls._integer(memory.get("used"), fallback=node.mem),
            memory_total_bytes=cls._integer(memory.get("total"), fallback=node.maxmem),
            disk_used_bytes=cls._integer(rootfs.get("used"), fallback=node.disk),
            disk_total_bytes=cls._integer(rootfs.get("total"), fallback=node.maxdisk),
            load_average=parsed_load,
            uptime_seconds=cls._integer(status.get("uptime"), fallback=node.uptime),
        )

    @staticmethod
    def _number(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float | str):
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            return max(0.0, parsed) if math.isfinite(parsed) else None
        return None

    @classmethod
    def _node_metric_point(cls, item: dict[str, Any]) -> NodeMetricPoint | None:
        timestamp = cls._integer(item.get("time"), fallback=None)
        if timestamp is None or timestamp <= 0:
            return None

        memory_used = item.get("memused", item.get("mem"))
        memory_total = item.get("memtotal", item.get("maxmem"))
        return NodeMetricPoint(
            time=timestamp,
            cpu_usage=cls._number(item.get("cpu")),
            server_load=cls._number(item.get("loadavg")),
            memory_used_bytes=cls._integer(memory_used, fallback=None),
            memory_total_bytes=cls._integer(memory_total, fallback=None),
            network_receive_bps=cls._number(item.get("netin")),
            network_transmit_bps=cls._number(item.get("netout")),
            cpu_pressure_some=cls._number(item.get("pressurecpusome")),
            io_pressure_some=cls._number(item.get("pressureiosome")),
            io_pressure_full=cls._number(item.get("pressureiofull")),
            memory_pressure_some=cls._number(item.get("pressurememorysome")),
            memory_pressure_full=cls._number(item.get("pressurememoryfull")),
        )

    @classmethod
    def _integer(cls, value: object, *, fallback: int | None) -> int | None:
        parsed = cls._number(value)
        return int(parsed) if parsed is not None else fallback

    @staticmethod
    def _failed_resource_overview(
        *,
        cluster_id: UUID,
        name: str,
        observed_at: datetime,
        error_code: str,
    ) -> ClusterResourceOverview:
        return ClusterResourceOverview(
            cluster_id=cluster_id,
            name=name,
            connected=False,
            observed_at=observed_at,
            error_code=error_code,
            node_count=0,
            guest_count=0,
            running_guest_count=0,
            qemu_count=0,
            lxc_count=0,
            storage_count=0,
            storage_used_bytes=0,
            storage_total_bytes=0,
            nodes=[],
        )

    async def update(self, cluster_id: UUID, request: ClusterUpdate) -> ClusterResponse:
        cluster = await self._get_cluster(cluster_id)
        credential = self._active_credential(cluster)
        before = {
            "name": cluster.name,
            "api_base_url": cluster.api_base_url,
            "ca_configured": cluster.ca_bundle_pem is not None,
            "token_identifier": credential.token_identifier,
            "is_active": cluster.is_active,
        }
        if request.version is not None and request.version != cluster.version:
            raise AppError(
                status_code=409,
                code="CLUSTER_VERSION_CONFLICT",
                message="The cluster was modified by another request.",
            )

        api_base_url = (
            self._normalize_url(request.api_base_url)
            if request.api_base_url is not None
            else cluster.api_base_url
        )
        ca_bundle_pem = (
            None
            if request.clear_ca_bundle
            else request.ca_bundle_pem
            if request.ca_bundle_pem is not None
            else cluster.ca_bundle_pem
        )
        token_identifier = request.token_identifier or credential.token_identifier
        connection_identity_changed = any(
            (
                api_base_url != cluster.api_base_url,
                ca_bundle_pem != cluster.ca_bundle_pem,
                token_identifier != credential.token_identifier,
            )
        )
        if connection_identity_changed and request.token_secret is None:
            raise AppError(
                status_code=422,
                code="PVE_CREDENTIAL_REENTRY_REQUIRED",
                message=(
                    "The Proxmox token secret must be re-entered when connection details change."
                ),
            )
        token_secret = (
            request.token_secret.get_secret_value()
            if request.token_secret is not None
            else self._decrypt(cluster.id, credential)
        )
        connection_changed = any(
            (
                request.api_base_url is not None,
                request.ca_bundle_pem is not None,
                request.clear_ca_bundle,
                request.token_identifier is not None,
                request.token_secret is not None,
            )
        )
        if connection_changed:
            probe_cluster_id = cluster.id
            probe_credential_id = credential.id
            await self._session.rollback()
            await self._probe(
                ClusterConnection(
                    cluster_id=probe_cluster_id,
                    credential_id=probe_credential_id,
                    api_base_url=api_base_url,
                    ca_bundle_pem=ca_bundle_pem,
                    token_identifier=token_identifier,
                    token_secret=token_secret,
                )
            )
            cluster = await self._get_cluster(cluster_id)
            credential = self._active_credential(cluster)

        if request.name is not None:
            cluster.name = request.name.strip()
        cluster.api_base_url = api_base_url
        cluster.ca_bundle_pem = ca_bundle_pem
        cluster.version += 1
        if connection_changed:
            now = datetime.now(UTC)
            cluster.last_connected_at = now
            cluster.last_connection_error_code = None
            credential.last_used_at = now
            if request.token_identifier is not None or request.token_secret is not None:
                credential.is_active = False
                credential.retired_at = now
                await self._session.flush()
                replacement_id = uuid4()
                encrypted = self._cipher.encrypt(
                    token_secret,
                    cluster_id=cluster.id,
                    credential_id=replacement_id,
                )
                credential = ClusterCredential(
                    id=replacement_id,
                    cluster_id=cluster.id,
                    token_identifier=token_identifier,
                    secret_ciphertext=encrypted.ciphertext,
                    secret_nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                    is_active=True,
                    last_used_at=now,
                )
                self._session.add(credential)
        after = {
            "name": cluster.name,
            "api_base_url": cluster.api_base_url,
            "ca_configured": cluster.ca_bundle_pem is not None,
            "token_identifier": credential.token_identifier,
            "is_active": cluster.is_active,
        }
        add_audit_event(
            self._session,
            action="CLUSTER_API_TOKEN_CHANGED"
            if request.token_identifier is not None or request.token_secret is not None
            else "CLUSTER_UPDATED",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="cluster",
            target_id=cluster.id,
            before=before,
            after=after,
        )
        await self._commit_or_conflict()
        await self._session.refresh(cluster)
        return self._to_response(cluster, credential)

    async def delete(self, cluster_id: UUID) -> None:
        cluster = await self._get_cluster(cluster_id)
        if not cluster.is_active:
            return
        blocks = await self._removal_blocks(cluster_id)
        if blocks:
            raise AppError(
                status_code=409,
                code="CLUSTER_REMOVAL_BLOCKED",
                message="The cluster still has dependent resources.",
                details={"blocks": [block.model_dump() for block in blocks]},
            )
        now = datetime.now(UTC)
        cluster.is_active = False
        cluster.disabled_at = now
        cluster.version += 1
        credential = self._active_credential(cluster)
        credential.is_active = False
        credential.retired_at = now
        await self._session.execute(
            update(Workload)
            .where(
                Workload.cluster_id == cluster_id,
                Workload.is_present.is_(True),
            )
            .values(
                is_present=False,
                observed_at=now,
                version=Workload.version + 1,
            )
        )
        add_audit_event(
            self._session,
            action="CLUSTER_DISABLED",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="cluster",
            target_id=cluster.id,
            before={"is_active": True},
            after={"is_active": False},
        )
        await self._session.commit()

    async def removal_check(self, cluster_id: UUID) -> ClusterRemovalCheckResponse:
        await self._get_cluster(cluster_id)
        blocks = await self._removal_blocks(cluster_id)
        return ClusterRemovalCheckResponse(
            cluster_id=cluster_id,
            can_remove=not blocks,
            blocks=blocks,
        )

    async def _removal_blocks(self, cluster_id: UUID) -> list[ClusterRemovalBlock]:
        async def count(statement: Executable) -> int:
            value = await self._session.scalar(statement)
            return int(value or 0)

        counts = {
            "ASSIGNED_WORKLOADS": await count(
                select(func.count())
                .select_from(Workload)
                .where(
                    Workload.cluster_id == cluster_id,
                    Workload.organization_id.is_not(None),
                )
            ),
            "ACTIVE_OPERATIONS": await count(
                select(func.count())
                .select_from(Operation)
                .where(
                    Operation.cluster_id == cluster_id,
                    Operation.status.in_(
                        [OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]
                    ),
                )
            ),
            "ACTIVE_PROVISIONING_REQUESTS": await count(
                select(func.count())
                .select_from(ProvisioningRequest)
                .where(
                    ProvisioningRequest.target_cluster_id == cluster_id,
                    ProvisioningRequest.status.in_(
                        [
                            ProvisioningStatus.QUEUED.value,
                            ProvisioningStatus.RUNNING.value,
                            ProvisioningStatus.MANUAL_REVIEW.value,
                        ]
                    ),
                )
            ),
            "PROVISIONING_NODES": await count(
                select(func.count())
                .select_from(ProvisioningNode)
                .where(ProvisioningNode.cluster_id == cluster_id)
            ),
            "TEMPLATES": await count(
                select(func.count())
                .select_from(Template)
                .join(Workload, Template.source_workload_id == Workload.id)
                .where(Workload.cluster_id == cluster_id)
            ),
            "CLUSTER_IP_POOLS": await count(
                select(func.count()).select_from(IpPool).where(IpPool.cluster_id == cluster_id)
            ),
        }
        return [
            ClusterRemovalBlock(code=code, count=value)
            for code, value in counts.items()
            if value > 0
        ]

    async def test_connection(self, cluster_id: UUID) -> ConnectionTestResponse:
        connection = await self._connection_snapshot(cluster_id)
        try:
            result = await self._probe(connection)
        except AppError as exc:
            await self._record_connection_result(cluster_id, error_code=exc.code)
            add_audit_event(
                self._session,
                action="CLUSTER_CONNECTION_TEST",
                outcome="FAILED",
                request_id=None,
                actor_user_id=self._principal.user_id,
                actor_role=self._principal.role,
                target_type="cluster",
                target_id=cluster_id,
                error_code=exc.code,
            )
            await self._session.commit()
            raise
        await self._record_connection_result(cluster_id, error_code=None)
        add_audit_event(
            self._session,
            action="CLUSTER_CONNECTION_TEST",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="cluster",
            target_id=cluster_id,
        )
        await self._session.commit()
        return ConnectionTestResponse(
            reachable=True,
            tls_valid=True,
            authenticated=True,
            version=self._optional_string(result.get("version")),
            release=self._optional_string(result.get("release")),
            capabilities=self._capabilities(result),
        )

    async def nodes(self, cluster_id: UUID) -> list[NodeResponse]:
        raw = await self._call_resource(cluster_id, "get_nodes")
        return self._validate_items(NodeResponse, raw)

    async def node_metrics(
        self,
        cluster_id: UUID,
        *,
        node: str,
        metric_range: NodeMetricRange,
    ) -> NodeMetricSeriesResponse:
        timeframe, duration = {
            "hour": ("hour", timedelta(hours=1)),
            "six_hours": ("day", timedelta(hours=6)),
            "day": ("day", timedelta(days=1)),
            "week": ("week", timedelta(days=7)),
        }[metric_range]
        connection = await self._connection_snapshot(cluster_id)
        async with self._client(connection) as client:
            raw = await client.get_node_rrd_data(node=node, timeframe=timeframe)

        cutoff = int((datetime.now(UTC) - duration).timestamp())
        points = [point for item in raw if (point := self._node_metric_point(item))]
        points = sorted(
            (point for point in points if point.time >= cutoff),
            key=lambda point: point.time,
        )
        return NodeMetricSeriesResponse(
            cluster_id=cluster_id,
            node=node,
            range=metric_range,
            observed_at=datetime.now(UTC),
            items=points,
        )

    async def guests(self, cluster_id: UUID) -> list[GuestResponse]:
        raw = await self._call_resource(cluster_id, "get_guests")
        return self._validate_items(GuestResponse, raw)

    async def storages(self, cluster_id: UUID) -> list[StorageResponse]:
        raw = await self._call_resource(cluster_id, "get_storages")
        return self._validate_items(StorageResponse, raw)

    async def import_workloads(self, cluster_id: UUID) -> WorkloadImportResponse:
        raw = await self._call_resource(cluster_id, "get_guests")
        guests = self._validate_items(GuestResponse, raw)
        cluster = await self._session.scalar(
            select(Cluster).where(Cluster.id == cluster_id).with_for_update()
        )
        if cluster is None:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The Proxmox cluster was not found.")
        existing_rows = await self._session.scalars(
            select(Workload).where(Workload.cluster_id == cluster_id)
        )
        existing = {item.vmid: item for item in existing_rows.all()}
        now = datetime.now(UTC)
        created = 0
        updated = 0
        seen_vmids: set[int] = set()
        for guest in guests:
            kind = guest.type.upper()
            if (
                guest.vmid <= 0
                or guest.vmid in seen_vmids
                or kind not in {"QEMU", "LXC"}
                or guest.node is None
                or not guest.node.strip()
                or len(guest.node) > 255
                or (guest.name is not None and len(guest.name) > 255)
            ):
                raise AppError(
                    502,
                    "PVE_INVALID_RESPONSE",
                    "The Proxmox API returned an invalid workload item.",
                )
            seen_vmids.add(guest.vmid)
            power_state = guest.status.upper() if guest.status else "UNKNOWN"
            if len(power_state) > 20:
                power_state = "UNKNOWN"
            workload = existing.get(guest.vmid)
            if workload is None:
                workload = Workload(
                    cluster_id=cluster_id,
                    vmid=guest.vmid,
                    node=guest.node,
                    kind=kind,
                    name=guest.name,
                    power_state=power_state,
                    cpu_cores=guest.maxcpu,
                    memory_bytes=guest.maxmem,
                    disk_bytes=guest.maxdisk,
                    is_template=bool(guest.template),
                    is_present=True,
                    organization_id=None,
                    observed_at=now,
                    version=1,
                )
                self._session.add(workload)
                existing[guest.vmid] = workload
                created += 1
            else:
                workload.node = guest.node
                workload.kind = kind
                workload.name = guest.name
                workload.power_state = power_state
                workload.cpu_cores = guest.maxcpu
                workload.memory_bytes = guest.maxmem
                workload.disk_bytes = guest.maxdisk
                workload.is_template = bool(guest.template)
                workload.is_present = True
                workload.observed_at = now
                workload.version += 1
                updated += 1
        add_audit_event(
            self._session,
            action="PVE_WORKLOADS_IMPORTED",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="cluster",
            target_id=cluster_id,
            after={"discovered": len(guests), "created": created, "updated": updated},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "WORKLOAD_IMPORT_CONFLICT",
                "The workload inventory changed during import; retry the request.",
            ) from exc
        return WorkloadImportResponse(
            cluster_id=cluster_id,
            discovered=len(guests),
            created=created,
            updated=updated,
        )

    async def _call_resource(self, cluster_id: UUID, method_name: str) -> list[dict[str, Any]]:
        connection = await self._connection_snapshot(cluster_id)
        try:
            async with self._client(connection) as client:
                method = getattr(client, method_name)
                result: list[dict[str, Any]] = await method()
        except AppError as exc:
            await self._record_connection_result(cluster_id, error_code=exc.code)
            raise
        await self._record_connection_result(cluster_id, error_code=None)
        return result

    async def _probe(self, connection: ClusterConnection) -> dict[str, Any]:
        async with self._client(connection) as client:
            return await client.test_connection()

    def _client(self, connection: ClusterConnection) -> ProxmoxClient:
        return ProxmoxClient(
            api_base_url=connection.api_base_url,
            token_identifier=connection.token_identifier,
            token_secret=connection.token_secret,
            ca_bundle_pem=connection.ca_bundle_pem,
            connect_timeout=self._settings.pve_connect_timeout_seconds,
            read_timeout=self._settings.pve_read_timeout_seconds,
            max_connections=self._settings.pve_max_connections,
            max_keepalive_connections=self._settings.pve_max_keepalive_connections,
            allowed_hosts=self._settings.pve_allowed_hosts,
            allowed_networks=self._settings.pve_allowed_networks,
            transport=self._transport,
        )

    async def _connection_snapshot(self, cluster_id: UUID) -> ClusterConnection:
        cluster = await self._get_cluster(cluster_id)
        if not cluster.is_active:
            raise AppError(
                status_code=409,
                code="CLUSTER_DISABLED",
                message="The Proxmox cluster is disabled.",
            )
        credential = self._active_credential(cluster)
        connection = ClusterConnection(
            cluster_id=cluster.id,
            credential_id=credential.id,
            api_base_url=cluster.api_base_url,
            ca_bundle_pem=cluster.ca_bundle_pem,
            token_identifier=credential.token_identifier,
            token_secret=self._decrypt(cluster.id, credential),
        )
        await self._session.rollback()
        return connection

    async def _record_connection_result(self, cluster_id: UUID, error_code: str | None) -> None:
        cluster = await self._get_cluster(cluster_id)
        cluster.last_connection_error_code = error_code
        if error_code is None:
            cluster.last_connected_at = datetime.now(UTC)
            self._active_credential(cluster).last_used_at = datetime.now(UTC)
        await self._session.commit()

    async def _get_cluster(self, cluster_id: UUID) -> Cluster:
        cluster = await self._session.scalar(select(Cluster).where(Cluster.id == cluster_id))
        if cluster is None:
            raise AppError(
                status_code=404,
                code="CLUSTER_NOT_FOUND",
                message="The Proxmox cluster was not found.",
            )
        return cluster

    def _decrypt(self, cluster_id: UUID, credential: ClusterCredential) -> str:
        try:
            return self._cipher.decrypt(
                EncryptedCredential(
                    ciphertext=credential.secret_ciphertext,
                    nonce=credential.secret_nonce,
                    key_version=credential.key_version,
                ),
                cluster_id=cluster_id,
                credential_id=credential.id,
            )
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise AppError(
                status_code=500,
                code="CREDENTIAL_DECRYPTION_FAILED",
                message="The stored Proxmox credential could not be decrypted.",
            ) from exc

    async def _commit_or_conflict(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="CLUSTER_CONFLICT",
                message="A cluster with the same name or endpoint already exists.",
            ) from exc

    @staticmethod
    def _active_credential(cluster: Cluster) -> ClusterCredential:
        credential = next((item for item in cluster.credentials if item.is_active), None)
        if credential is None:
            raise AppError(
                status_code=409,
                code="CLUSTER_CREDENTIAL_MISSING",
                message="The Proxmox cluster has no active credential.",
            )
        return credential

    @staticmethod
    def _display_credential(cluster: Cluster) -> ClusterCredential:
        active = next((item for item in cluster.credentials if item.is_active), None)
        if active is not None:
            return active
        if cluster.credentials:
            return max(cluster.credentials, key=lambda item: item.created_at)
        raise AppError(
            status_code=409,
            code="CLUSTER_CREDENTIAL_MISSING",
            message="The Proxmox cluster has no credential metadata.",
        )

    @staticmethod
    def _to_response(cluster: Cluster, credential: ClusterCredential) -> ClusterResponse:
        return ClusterResponse(
            id=cluster.id,
            name=cluster.name,
            api_base_url=cluster.api_base_url,
            is_active=cluster.is_active,
            ca_configured=cluster.ca_bundle_pem is not None,
            last_connection_error_code=cluster.last_connection_error_code,
            last_connected_at=cluster.last_connected_at,
            credential=CredentialSummary(
                token_identifier=credential.token_identifier,
                last_used_at=credential.last_used_at,
            ),
            created_at=cluster.created_at,
            updated_at=cluster.updated_at,
            version=cluster.version,
        )

    @staticmethod
    def _normalize_url(url: object) -> str:
        return str(url).rstrip("/")

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _capabilities(result: dict[str, Any]) -> dict[str, bool]:
        value = result.get("capabilities")
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(enabled, bool) for key, enabled in value.items()
        ):
            raise AppError(
                status_code=502,
                code="PVE_INVALID_RESPONSE",
                message="The Proxmox API returned invalid capability information.",
            )
        return value

    @staticmethod
    def _validate_items[T](model: type[T], items: list[dict[str, Any]]) -> list[T]:
        try:
            return [model.model_validate(item) for item in items]  # type: ignore[attr-defined]
        except ValidationError as exc:
            raise AppError(
                status_code=502,
                code="PVE_INVALID_RESPONSE",
                message="The Proxmox API returned an invalid resource item.",
            ) from exc
