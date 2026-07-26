import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.cluster import Cluster, ClusterCredential
from app.models.metrics import WorkloadMetric
from app.models.operation import Workload, WorkloadAssignment
from app.proxmox.client import ProxmoxClient
from app.security.credentials import CredentialCipher, EncryptedCredential


@dataclass(frozen=True)
class MetricTarget:
    id: UUID
    cluster_id: UUID
    organization_id: UUID
    assigned_at: datetime
    kind: str
    node: str
    vmid: int


MetricLoader = Callable[[MetricTarget], Awaitable[list[dict[str, Any]]]]
logger = logging.getLogger(__name__)


class WorkloadMetricService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        loader: MetricLoader | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._loader = loader or self._load

    async def collect(self) -> int:
        rows = (
            await self._session.execute(
                select(Workload, WorkloadAssignment)
                .join(
                    WorkloadAssignment,
                    (WorkloadAssignment.workload_id == Workload.id)
                    & (WorkloadAssignment.revoked_at.is_(None)),
                )
                .join(Cluster, Cluster.id == Workload.cluster_id)
                .where(
                    Workload.kind == "QEMU",
                    Workload.is_present.is_(True),
                    Workload.is_template.is_(False),
                    Workload.organization_id == WorkloadAssignment.organization_id,
                    Cluster.is_active.is_(True),
                )
            )
        ).all()
        targets = [
            MetricTarget(
                id=workload.id,
                cluster_id=workload.cluster_id,
                organization_id=assignment.organization_id,
                assigned_at=assignment.assigned_at,
                kind=workload.kind,
                node=workload.node,
                vmid=workload.vmid,
            )
            for workload, assignment in rows
        ]
        inserted = 0
        for target in targets:
            try:
                raw_points = await self._loader(target)
            except AppError as exc:
                logger.warning(
                    "Workload metric collection failed",
                    extra={"workload_id": str(target.id), "error_code": exc.code},
                )
                continue
            for raw in raw_points:
                values = self.normalize(raw)
                if values is None or values["bucket_at"] < target.assigned_at:
                    continue
                statement = (
                    insert(WorkloadMetric)
                    .values(
                        workload_id=target.id,
                        organization_id=target.organization_id,
                        resolution_seconds=60,
                        sample_count=1,
                        **values,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            WorkloadMetric.workload_id,
                            WorkloadMetric.organization_id,
                            WorkloadMetric.resolution_seconds,
                            WorkloadMetric.bucket_at,
                        ]
                    )
                    .returning(WorkloadMetric.id)
                )
                result = await self._session.execute(statement)
                if result.scalar_one_or_none() is not None:
                    inserted += 1
            await self._session.commit()
        return inserted

    async def downsample_and_retain(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        created = await self._rollup(
            source_resolution=60,
            target_resolution=300,
            before=current,
        )
        created += await self._rollup(
            source_resolution=300,
            target_resolution=3600,
            before=current,
        )
        await self._session.execute(
            delete(WorkloadMetric).where(
                (WorkloadMetric.resolution_seconds == 60)
                & (WorkloadMetric.bucket_at < current - timedelta(hours=24))
            )
        )
        await self._session.execute(
            delete(WorkloadMetric).where(
                (WorkloadMetric.resolution_seconds == 300)
                & (WorkloadMetric.bucket_at < current - timedelta(days=30))
            )
        )
        await self._session.execute(
            delete(WorkloadMetric).where(
                (WorkloadMetric.resolution_seconds == 3600)
                & (WorkloadMetric.bucket_at < current - timedelta(days=365))
            )
        )
        await self._session.commit()
        return created

    async def _rollup(
        self,
        *,
        source_resolution: int,
        target_resolution: int,
        before: datetime,
    ) -> int:
        rows = (
            await self._session.scalars(
                select(WorkloadMetric).where(
                    WorkloadMetric.resolution_seconds == source_resolution,
                    WorkloadMetric.bucket_at < before,
                )
            )
        ).all()
        groups: dict[tuple[UUID, UUID, datetime], list[WorkloadMetric]] = defaultdict(list)
        for row in rows:
            timestamp = int(row.bucket_at.timestamp())
            bucket = datetime.fromtimestamp(
                timestamp - timestamp % target_resolution,
                tz=UTC,
            )
            groups[(row.workload_id, row.organization_id, bucket)].append(row)
        created = 0
        for (workload_id, organization_id, bucket), samples in groups.items():
            values: dict[str, object] = {
                "workload_id": workload_id,
                "organization_id": organization_id,
                "resolution_seconds": target_resolution,
                "bucket_at": bucket,
                "sample_count": sum(item.sample_count for item in samples),
            }
            for name in (
                "cpu",
                "memory_used",
                "disk_read",
                "disk_write",
                "network_receive",
                "network_transmit",
            ):
                averages = [
                    (getattr(item, f"{name}_avg"), item.sample_count)
                    for item in samples
                    if getattr(item, f"{name}_avg") is not None
                ]
                maxima = [
                    getattr(item, f"{name}_max")
                    for item in samples
                    if getattr(item, f"{name}_max") is not None
                ]
                values[f"{name}_avg"] = (
                    sum(float(value) * count for value, count in averages)
                    / sum(count for _, count in averages)
                    if averages
                    else None
                )
                values[f"{name}_max"] = max(maxima) if maxima else None
            statement = (
                insert(WorkloadMetric)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        WorkloadMetric.workload_id,
                        WorkloadMetric.organization_id,
                        WorkloadMetric.resolution_seconds,
                        WorkloadMetric.bucket_at,
                    ],
                    set_={
                        key: value
                        for key, value in values.items()
                        if key
                        not in {
                            "workload_id",
                            "organization_id",
                            "resolution_seconds",
                            "bucket_at",
                        }
                    },
                )
            )
            await self._session.execute(statement)
            created += 1
        return created

    @staticmethod
    def normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
        timestamp = raw.get("time")
        if not isinstance(timestamp, (int, float)) or timestamp <= 0:
            return None
        bucket_at = datetime.fromtimestamp(int(timestamp) - int(timestamp) % 60, tz=UTC)

        def metric(name: str) -> float | None:
            value = raw.get(name)
            if not isinstance(value, (int, float)) or value < 0:
                return None
            return float(value)

        values: dict[str, Any] = {"bucket_at": bucket_at}
        for source, target in (
            ("cpu", "cpu"),
            ("mem", "memory_used"),
            ("diskread", "disk_read"),
            ("diskwrite", "disk_write"),
            ("netin", "network_receive"),
            ("netout", "network_transmit"),
        ):
            value = metric(source)
            values[f"{target}_avg"] = value
            values[f"{target}_max"] = value
        return values

    async def _load(self, target: MetricTarget) -> list[dict[str, Any]]:
        row = (
            await self._session.execute(
                select(Cluster, ClusterCredential)
                .join(
                    ClusterCredential,
                    (ClusterCredential.cluster_id == Cluster.id)
                    & ClusterCredential.is_active.is_(True),
                )
                .where(
                    Cluster.id == target.cluster_id,
                    Cluster.is_active.is_(True),
                )
            )
        ).one_or_none()
        if row is None:
            raise AppError(409, "CLUSTER_CREDENTIAL_MISSING", "Metrics are unavailable.")
        cluster, credential = row
        try:
            secret = self._cipher.decrypt(
                EncryptedCredential(
                    ciphertext=credential.secret_ciphertext,
                    nonce=credential.secret_nonce,
                    key_version=credential.key_version,
                ),
                cluster_id=cluster.id,
                credential_id=credential.id,
            )
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise AppError(500, "CREDENTIAL_DECRYPTION_FAILED", "Metrics are unavailable.") from exc
        api_base_url = cluster.api_base_url
        ca_bundle_pem = cluster.ca_bundle_pem
        token_identifier = credential.token_identifier
        kind = target.kind
        node = target.node
        vmid = target.vmid
        await self._session.rollback()
        async with self._client(
            api_base_url=api_base_url,
            ca_bundle_pem=ca_bundle_pem,
            token_identifier=token_identifier,
            token_secret=secret,
        ) as client:
            return await client.get_guest_rrd_data(
                kind=kind,
                node=node,
                vmid=vmid,
                timeframe="hour",
            )

    @asynccontextmanager
    async def _client(
        self,
        *,
        api_base_url: str,
        ca_bundle_pem: str | None,
        token_identifier: str,
        token_secret: str,
    ) -> AsyncIterator[ProxmoxClient]:
        async with ProxmoxClient(
            api_base_url=api_base_url,
            token_identifier=token_identifier,
            token_secret=token_secret,
            ca_bundle_pem=ca_bundle_pem,
            connect_timeout=self._settings.pve_connect_timeout_seconds,
            read_timeout=self._settings.pve_read_timeout_seconds,
            max_connections=self._settings.pve_max_connections,
            max_keepalive_connections=self._settings.pve_max_keepalive_connections,
            allowed_hosts=self._settings.pve_allowed_hosts,
            allowed_networks=self._settings.pve_allowed_networks,
        ) as client:
            yield client
