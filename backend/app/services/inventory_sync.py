import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.cluster import Cluster, ClusterCredential
from app.models.inventory import (
    FindingKind,
    FindingSeverity,
    FindingStatus,
    InventoryNode,
    InventoryStorage,
    ReconciliationFinding,
    WorkloadChangeEvent,
)
from app.models.operation import Operation, OperationStatus, Workload
from app.models.scheduling import RunStatus, SyncRun
from app.proxmox.client import ProxmoxClient
from app.schemas.cluster import GuestResponse, NodeResponse, StorageResponse
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event
from app.services.maintenance import acquire_lease, release_lease, require_current_lease


@dataclass
class InventorySnapshot:
    nodes: list[dict[str, object]] | None = None
    guests: list[dict[str, object]] | None = None
    storages: list[dict[str, object]] | None = None
    errors: dict[str, str] = field(default_factory=dict)


SnapshotLoader = Callable[[UUID], Awaitable[InventorySnapshot]]
GuestLoader = Callable[[UUID], Awaitable[list[dict[str, object]]]]


class _ValidatedSnapshot(BaseModel):
    nodes: list[NodeResponse] | None
    guests: list[GuestResponse] | None
    storages: list[StorageResponse] | None
    errors: dict[str, str]


class ScheduledInventorySyncRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        snapshot_loader: SnapshotLoader | None = None,
        guest_loader: GuestLoader | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        if snapshot_loader is not None:
            self._snapshot_loader = snapshot_loader
        elif guest_loader is not None:
            self._snapshot_loader = self._guest_snapshot_loader(guest_loader)
        else:
            self._snapshot_loader = self._load_snapshot

    async def run(self, run_or_cluster_id: UUID) -> SyncRun | None:
        run = await self._session.get(SyncRun, run_or_cluster_id)
        if run is None:
            run = await self._create_compatibility_run(run_or_cluster_id)
        run_id = run.id
        cluster_id = run.cluster_id
        owner_id = uuid4()
        grant = await acquire_lease(
            self._session,
            name=f"inventory:{cluster_id}",
            owner_id=owner_id,
            ttl_seconds=self._settings.scheduler_lease_seconds,
        )
        if grant is None:
            if run.status == RunStatus.QUEUED.value:
                run.status = RunStatus.SKIPPED.value
                run.error_code = "LEASE_HELD"
                run.finished_at = datetime.now(UTC)
                await self._session.commit()
            return None

        run = await self._session.get(SyncRun, run_id, with_for_update=True) or run
        run.status = RunStatus.RUNNING.value
        run.error_code = None
        await self._session.commit()
        try:
            snapshot = await self._snapshot_loader(cluster_id)
            await require_current_lease(self._session, grant)
            # The production snapshot loader rolls back its read transaction before
            # performing slow PVE HTTP calls. A rollback expires ORM state even when
            # expire_on_commit is disabled, so reload the run before applying data.
            run = await self._session.get(
                SyncRun,
                run_id,
                with_for_update=True,
                populate_existing=True,
            )
            if run is None:
                raise AppError(404, "SYNC_RUN_NOT_FOUND", "The inventory sync run was not found.")
            validated = self._validate_snapshot(snapshot)
            counts = await self._apply(run, validated)
            await require_current_lease(self._session, grant)
        except Exception as exc:
            await self._session.rollback()
            run = await self._session.get(SyncRun, run_id, with_for_update=True)
            if run is None:
                raise
            run.status = RunStatus.FAILED.value
            run.partial_failure = False
            run.error_code = self._error_code(exc)
            run.finished_at = datetime.now(UTC)
            cluster = await self._session.get(Cluster, cluster_id, with_for_update=True)
            if cluster is not None:
                cluster.last_connection_error_code = run.error_code
            await self._session.commit()
            raise
        else:
            finished_at = datetime.now(UTC)
            partial = bool(validated.errors)
            all_failed = (
                validated.nodes is None and validated.guests is None and validated.storages is None
            )
            run.status = (
                RunStatus.FAILED.value
                if all_failed
                else RunStatus.PARTIAL.value
                if partial
                else RunStatus.SUCCEEDED.value
            )
            run.partial_failure = partial
            run.resource_counts = counts
            run.finished_at = finished_at
            run.error_code = next(iter(validated.errors.values()), None)
            cluster = await self._session.get(Cluster, cluster_id, with_for_update=True)
            if cluster is not None:
                cluster.last_connection_error_code = run.error_code
                if run.status == RunStatus.SUCCEEDED.value and run.scope == "FULL":
                    cluster.last_connected_at = finished_at
                    cluster.last_sync_succeeded_at = finished_at
            add_audit_event(
                self._session,
                action="INVENTORY_SYNC_COMPLETED",
                outcome=run.status,
                request_id=None,
                actor_user_id=run.requested_by_id,
                actor_role=None,
                target_type="cluster",
                target_id=cluster_id,
                after={
                    "sync_run_id": str(run.id),
                    "scope": run.scope,
                    "generation": run.generation,
                    **counts,
                },
            )
            await self._session.commit()
            return run
        finally:
            await release_lease(self._session, grant)

    async def _create_compatibility_run(self, cluster_id: UUID) -> SyncRun:
        cluster = await self._session.scalar(
            select(Cluster)
            .where(Cluster.id == cluster_id, Cluster.is_active.is_(True))
            .with_for_update()
        )
        if cluster is None:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
        generation = (
            int(
                (
                    await self._session.scalar(
                        select(func.coalesce(func.max(SyncRun.generation), 0)).where(
                            SyncRun.cluster_id == cluster_id
                        )
                    )
                )
                or 0
            )
            + 1
        )
        run = SyncRun(
            cluster_id=cluster_id,
            generation=generation,
            status=RunStatus.QUEUED.value,
            scope="FULL",
            partial_failure=False,
            triggered_by="scheduler",
            started_at=datetime.now(UTC),
            resource_counts={},
        )
        self._session.add(run)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._session.scalar(
                select(SyncRun).where(
                    SyncRun.cluster_id == cluster_id,
                    SyncRun.scope == "FULL",
                    SyncRun.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]),
                )
            )
            if existing is None:
                raise
            return existing
        return run

    async def _apply(
        self,
        run: SyncRun,
        snapshot: _ValidatedSnapshot,
    ) -> dict[str, object]:
        cluster = await self._session.scalar(
            select(Cluster)
            .where(Cluster.id == run.cluster_id, Cluster.is_active.is_(True))
            .with_for_update()
        )
        if cluster is None:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
        observed_at = datetime.now(UTC)
        counts: dict[str, object] = {
            "nodes": len(snapshot.nodes or []),
            "storages": len(snapshot.storages or []),
            "discovered": len(snapshot.guests or []),
            "created": 0,
            "updated": 0,
            "missing": 0,
            "findings": 0,
            "partial_errors": sorted(snapshot.errors),
        }
        if snapshot.nodes is not None:
            await self._apply_nodes(run, snapshot.nodes, observed_at)
        if snapshot.guests is not None:
            workload_counts = await self._apply_workloads(run, snapshot.guests, observed_at)
            counts.update(workload_counts)
        if snapshot.storages is not None:
            await self._apply_storages(run, snapshot.storages, observed_at)
        await self._session.flush()
        return counts

    async def _apply_nodes(
        self,
        run: SyncRun,
        nodes: list[NodeResponse],
        observed_at: datetime,
    ) -> None:
        rows = await self._session.scalars(
            select(InventoryNode)
            .where(InventoryNode.cluster_id == run.cluster_id)
            .with_for_update()
        )
        existing = {item.pve_name: item for item in rows.all()}
        seen: set[str] = set()
        for node_data in nodes:
            name = node_data.node.strip()
            if not name or name in seen or len(name) > 255:
                raise self._invalid_response()
            seen.add(name)
            status = (node_data.status or "UNKNOWN").upper()[:32]
            node = existing.get(name)
            safe_facts: dict[str, object] = {}
            if node is None:
                self._session.add(
                    InventoryNode(
                        cluster_id=run.cluster_id,
                        pve_name=name,
                        status=status,
                        cpu_total=node_data.maxcpu,
                        cpu_usage=node_data.cpu,
                        memory_total_bytes=node_data.maxmem,
                        memory_used_bytes=node_data.mem,
                        uptime_seconds=node_data.uptime,
                        raw_facts=safe_facts,
                        observed_at=observed_at,
                        sync_generation=run.generation,
                        is_present=True,
                    )
                )
            else:
                node.status = status
                node.cpu_total = node_data.maxcpu
                node.cpu_usage = node_data.cpu
                node.memory_total_bytes = node_data.maxmem
                node.memory_used_bytes = node_data.mem
                node.uptime_seconds = node_data.uptime
                node.raw_facts = safe_facts
                node.observed_at = observed_at
                node.sync_generation = run.generation
                node.is_present = True
                node.missing_since = None
        if run.scope == "FULL":
            for name, node in existing.items():
                if name not in seen and node.is_present:
                    node.is_present = False
                    node.missing_since = observed_at

    async def _apply_workloads(
        self,
        run: SyncRun,
        guests: list[GuestResponse],
        observed_at: datetime,
    ) -> dict[str, int]:
        rows = await self._session.scalars(
            select(Workload).where(Workload.cluster_id == run.cluster_id).with_for_update()
        )
        existing = {item.vmid: item for item in rows.all()}
        target = (
            next(
                (item for item in existing.values() if item.id == run.target_workload_id),
                None,
            )
            if run.target_workload_id is not None
            else None
        )
        seen: set[int] = set()
        created = updated = missing = findings = 0
        for guest in guests:
            if run.scope == "TARGET" and target is not None and guest.vmid != target.vmid:
                continue
            self._validate_guest(guest, seen)
            seen.add(guest.vmid)
            workload = existing.get(guest.vmid)
            if workload is None:
                workload = Workload(
                    cluster_id=run.cluster_id,
                    vmid=guest.vmid,
                    node=guest.node or "",
                    kind=guest.type.upper(),
                    name=guest.name,
                    power_state=self._power_state(guest.status),
                    cpu_cores=guest.maxcpu,
                    memory_bytes=guest.maxmem,
                    disk_bytes=guest.maxdisk,
                    uptime_seconds=guest.uptime,
                    is_template=bool(guest.template),
                    is_present=True,
                    sync_generation=run.generation,
                    observed_at=observed_at,
                    version=1,
                )
                self._session.add(workload)
                await self._session.flush()
                existing[guest.vmid] = workload
                created += 1
            else:
                findings += await self._record_drift(run, workload, guest, observed_at)
                if not workload.is_present:
                    await self._resolve_finding(
                        workload,
                        FindingKind.EXTERNAL_DELETE,
                        observed_at,
                        "The workload reappeared in Proxmox inventory.",
                    )
                workload.node = guest.node or workload.node
                workload.kind = guest.type.upper()
                workload.name = guest.name
                workload.power_state = self._power_state(guest.status)
                workload.cpu_cores = guest.maxcpu
                workload.memory_bytes = guest.maxmem
                workload.disk_bytes = guest.maxdisk
                workload.uptime_seconds = guest.uptime
                workload.is_template = bool(guest.template)
                workload.is_present = True
                workload.missing_since = None
                workload.sync_generation = run.generation
                workload.observed_at = observed_at
                workload.version += 1
                updated += 1

        candidates = (
            [target]
            if run.scope == "TARGET" and target is not None
            else list(existing.values())
            if run.scope == "FULL"
            else []
        )
        for workload in candidates:
            if workload is None or workload.vmid in seen or not workload.is_present:
                continue
            workload.is_present = False
            workload.missing_since = observed_at
            workload.sync_generation = run.generation
            workload.version += 1
            severity = (
                FindingSeverity.CRITICAL
                if workload.organization_id is not None
                else FindingSeverity.WARNING
            )
            findings += await self._upsert_finding(
                run,
                workload,
                FindingKind.EXTERNAL_DELETE,
                severity,
                "Workload is absent from a complete Proxmox inventory response.",
                {
                    "vmid": workload.vmid,
                    "assignment_preserved": workload.organization_id is not None,
                },
                observed_at,
            )
            missing += 1
        return {
            "created": created,
            "updated": updated,
            "missing": missing,
            "findings": findings,
        }

    async def _apply_storages(
        self,
        run: SyncRun,
        storages: list[StorageResponse],
        observed_at: datetime,
    ) -> None:
        rows = await self._session.scalars(
            select(InventoryStorage)
            .where(InventoryStorage.cluster_id == run.cluster_id)
            .with_for_update()
        )
        existing = {item.natural_key: item for item in rows.all()}
        seen: set[str] = set()
        for storage_data in storages:
            storage_id = storage_data.storage.strip()
            node = storage_data.node.strip() if storage_data.node else None
            natural_key = f"{node or '*'}:{storage_id}"
            if (
                not storage_id
                or len(storage_id) > 255
                or (node is not None and len(node) > 255)
                or natural_key in seen
            ):
                raise self._invalid_response()
            seen.add(natural_key)
            storage = existing.get(natural_key)
            values = {
                "storage_id": storage_id,
                "node": node,
                "storage_type": storage_data.type,
                "status": (storage_data.status or "UNKNOWN").upper()[:32],
                "total_bytes": storage_data.total,
                "used_bytes": storage_data.used,
                "available_bytes": storage_data.avail,
                "shared": bool(storage_data.shared),
                "content": storage_data.content[:500] if storage_data.content else None,
            }
            if storage is None:
                self._session.add(
                    InventoryStorage(
                        cluster_id=run.cluster_id,
                        natural_key=natural_key,
                        observed_at=observed_at,
                        sync_generation=run.generation,
                        is_present=True,
                        **values,
                    )
                )
            else:
                for key, value in values.items():
                    setattr(storage, key, value)
                storage.observed_at = observed_at
                storage.sync_generation = run.generation
                storage.is_present = True
                storage.missing_since = None
        if run.scope == "FULL":
            for natural_key, storage in existing.items():
                if natural_key not in seen and storage.is_present:
                    storage.is_present = False
                    storage.missing_since = observed_at

    async def _record_drift(
        self,
        run: SyncRun,
        workload: Workload,
        guest: GuestResponse,
        observed_at: datetime,
    ) -> int:
        findings = 0
        new_node = guest.node or workload.node
        if workload.node != new_node:
            findings += await self._change(
                run,
                workload,
                FindingKind.NODE_MOVED,
                FindingSeverity.WARNING,
                {"node": workload.node},
                {"node": new_node},
                "Workload node changed outside the local inventory.",
                observed_at,
            )
        before_spec: dict[str, object] = {
            "name": workload.name,
            "kind": workload.kind,
            "cpu_cores": workload.cpu_cores,
            "memory_bytes": workload.memory_bytes,
            "disk_bytes": workload.disk_bytes,
            "is_template": workload.is_template,
        }
        after_spec: dict[str, object] = {
            "name": guest.name,
            "kind": guest.type.upper(),
            "cpu_cores": guest.maxcpu,
            "memory_bytes": guest.maxmem,
            "disk_bytes": guest.maxdisk,
            "is_template": bool(guest.template),
        }
        if before_spec != after_spec:
            findings += await self._change(
                run,
                workload,
                FindingKind.SPEC_DRIFT,
                FindingSeverity.WARNING,
                before_spec,
                after_spec,
                "Workload specification changed outside the local inventory.",
                observed_at,
            )
        new_power_state = self._power_state(guest.status)
        if workload.power_state != new_power_state and not await self._has_active_operation(
            workload.id
        ):
            findings += await self._change(
                run,
                workload,
                FindingKind.POWER_STATE_DRIFT,
                FindingSeverity.WARNING
                if workload.organization_id is not None
                else FindingSeverity.INFO,
                {"power_state": workload.power_state},
                {"power_state": new_power_state},
                "Workload power state changed without an active local operation.",
                observed_at,
            )
        return findings

    async def _change(
        self,
        run: SyncRun,
        workload: Workload,
        kind: FindingKind,
        severity: FindingSeverity,
        before: dict[str, object],
        after: dict[str, object],
        summary: str,
        observed_at: datetime,
    ) -> int:
        self._session.add(
            WorkloadChangeEvent(
                workload_id=workload.id,
                sync_run_id=run.id,
                kind=kind.value,
                before=before,
                after=after,
                observed_at=observed_at,
            )
        )
        return await self._upsert_finding(
            run,
            workload,
            kind,
            severity,
            summary,
            {"before": before, "after": after},
            observed_at,
        )

    async def _upsert_finding(
        self,
        run: SyncRun,
        workload: Workload,
        kind: FindingKind,
        severity: FindingSeverity,
        summary: str,
        details: dict[str, object],
        observed_at: datetime,
    ) -> int:
        fingerprint = f"workload:{workload.id}:{kind.value}"
        finding = await self._session.scalar(
            select(ReconciliationFinding)
            .where(ReconciliationFinding.fingerprint == fingerprint)
            .with_for_update()
        )
        created = finding is None
        if finding is None:
            finding = ReconciliationFinding(
                fingerprint=fingerprint,
                kind=kind.value,
                severity=severity.value,
                status=FindingStatus.OPEN.value,
                cluster_id=run.cluster_id,
                workload_id=workload.id,
                sync_run_id=run.id,
                target_type="workload",
                target_id=workload.id,
                summary=summary,
                details=details,
                first_observed_at=observed_at,
                last_observed_at=observed_at,
            )
            self._session.add(finding)
        else:
            finding.severity = severity.value
            finding.status = FindingStatus.OPEN.value
            finding.sync_run_id = run.id
            finding.summary = summary
            finding.details = details
            finding.last_observed_at = observed_at
            finding.resolved_by_id = None
            finding.resolved_at = None
            finding.resolution_note = None
        return int(created)

    async def _resolve_finding(
        self,
        workload: Workload,
        kind: FindingKind,
        observed_at: datetime,
        note: str,
    ) -> None:
        finding = await self._session.scalar(
            select(ReconciliationFinding)
            .where(
                ReconciliationFinding.fingerprint == f"workload:{workload.id}:{kind.value}",
                ReconciliationFinding.status != FindingStatus.RESOLVED.value,
            )
            .with_for_update()
        )
        if finding is not None:
            finding.status = FindingStatus.RESOLVED.value
            finding.last_observed_at = observed_at
            finding.resolved_at = observed_at
            finding.resolution_note = note

    async def _has_active_operation(self, workload_id: UUID) -> bool:
        return (
            await self._session.scalar(
                select(Operation.id).where(
                    Operation.workload_id == workload_id,
                    Operation.status.in_(
                        [OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]
                    ),
                )
            )
            is not None
        )

    async def _load_snapshot(self, cluster_id: UUID) -> InventorySnapshot:
        connection = await self._connection(cluster_id)
        await self._session.rollback()
        async with self._client(**connection) as client:
            results = await asyncio.gather(
                client.get_nodes(),
                client.get_guests(),
                client.get_storages(),
                return_exceptions=True,
            )
        snapshot = InventorySnapshot()
        for scope, result in zip(("nodes", "guests", "storages"), results, strict=True):
            if isinstance(result, BaseException):
                snapshot.errors[scope] = self._error_code(result)
            else:
                setattr(snapshot, scope, result)
        return snapshot

    async def _connection(self, cluster_id: UUID) -> dict[str, object]:
        cluster = await self._session.scalar(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.is_active.is_(True))
        )
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == cluster_id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if cluster is None or credential is None:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
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
        except InvalidTag as exc:
            raise AppError(
                500,
                "CREDENTIAL_DECRYPTION_FAILED",
                "The cluster credential could not be decrypted.",
            ) from exc
        return {
            "api_base_url": cluster.api_base_url,
            "ca_bundle_pem": cluster.ca_bundle_pem,
            "token_identifier": credential.token_identifier,
            "token_secret": secret,
        }

    @asynccontextmanager
    async def _client(
        self,
        *,
        api_base_url: object,
        ca_bundle_pem: object,
        token_identifier: object,
        token_secret: object,
    ) -> AsyncIterator[ProxmoxClient]:
        async with ProxmoxClient(
            api_base_url=str(api_base_url),
            token_identifier=str(token_identifier),
            token_secret=str(token_secret),
            ca_bundle_pem=str(ca_bundle_pem) if ca_bundle_pem is not None else None,
            connect_timeout=self._settings.pve_connect_timeout_seconds,
            read_timeout=self._settings.pve_read_timeout_seconds,
            max_connections=self._settings.pve_max_connections,
            max_keepalive_connections=self._settings.pve_max_keepalive_connections,
            allowed_hosts=self._settings.pve_allowed_hosts,
            allowed_networks=self._settings.pve_allowed_networks,
        ) as client:
            yield client

    @staticmethod
    def _guest_snapshot_loader(loader: GuestLoader) -> SnapshotLoader:
        async def load(cluster_id: UUID) -> InventorySnapshot:
            return InventorySnapshot(nodes=[], guests=await loader(cluster_id), storages=[])

        return load

    @staticmethod
    def _validate_snapshot(snapshot: InventorySnapshot) -> _ValidatedSnapshot:
        try:
            return _ValidatedSnapshot(
                nodes=(
                    [NodeResponse.model_validate(item) for item in snapshot.nodes]
                    if snapshot.nodes is not None
                    else None
                ),
                guests=(
                    [GuestResponse.model_validate(item) for item in snapshot.guests]
                    if snapshot.guests is not None
                    else None
                ),
                storages=(
                    [StorageResponse.model_validate(item) for item in snapshot.storages]
                    if snapshot.storages is not None
                    else None
                ),
                errors=snapshot.errors,
            )
        except ValidationError as exc:
            raise ScheduledInventorySyncRunner._invalid_response() from exc

    @staticmethod
    def _validate_guest(guest: GuestResponse, seen: set[int]) -> None:
        if (
            guest.vmid <= 0
            or guest.vmid in seen
            or guest.type.upper() not in {"QEMU", "LXC"}
            or guest.node is None
            or not guest.node.strip()
            or len(guest.node) > 255
            or (guest.name is not None and len(guest.name) > 255)
        ):
            raise ScheduledInventorySyncRunner._invalid_response()

    @staticmethod
    def _power_state(status: str | None) -> str:
        state = status.upper() if status else "UNKNOWN"
        return state if len(state) <= 20 else "UNKNOWN"

    @staticmethod
    def _invalid_response() -> AppError:
        return AppError(
            502,
            "PVE_INVALID_RESPONSE",
            "The Proxmox API returned invalid inventory data.",
        )

    @staticmethod
    def _error_code(exc: BaseException) -> str:
        code = getattr(exc, "code", None)
        return code[:64] if isinstance(code, str) and code else "INVENTORY_SYNC_FAILED"
