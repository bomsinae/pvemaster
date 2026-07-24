import hmac
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import httpx
from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import Organization, UserRole
from app.models.backup import BackupRun, BackupTarget, RestoreRun
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, OperationStatus, PveTask, Workload
from app.proxmox.client import ProxmoxClient
from app.schemas.backup import (
    BackupRequest,
    BackupRunResponse,
    BackupStorageCandidate,
    BackupTargetCreate,
    BackupTargetResponse,
    BackupTargetUpdate,
    RestoreRequest,
    RestoreRunResponse,
)
from app.security.access import Principal, require_service_role
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event
from app.services.outbox import (
    BACKUP_EVENT,
    RESTORE_EVENT,
    add_operation_event,
    record_publish_failure,
    record_publish_success,
)

BackupPublisher = Callable[[UUID, str], None]
RestorePublisher = Callable[[UUID, str], None]
logger = logging.getLogger(__name__)


class BackupService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        principal: Principal,
        publisher: BackupPublisher,
        restore_publisher: RestorePublisher,
        request_id: str,
        source_ip: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._principal = principal
        self._publisher = publisher
        self._restore_publisher = restore_publisher
        self._request_id = request_id
        self._source_ip = source_ip
        self._transport = transport

    async def discover_storages(self, cluster_id: UUID) -> list[BackupStorageCandidate]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        cluster = await self._active_cluster(cluster_id)
        async with self._open_client(cluster) as client:
            configurations = await client.get_storage_configurations()
            live_storages = await client.get_storages()

        registered = {
            target.storage_id: target.id
            for target in (
                await self._session.scalars(
                    select(BackupTarget).where(BackupTarget.cluster_id == cluster.id)
                )
            ).all()
        }
        live_by_storage: dict[str, list[dict[str, object]]] = {}
        for item in live_storages:
            storage = item.get("storage")
            if isinstance(storage, str):
                live_by_storage.setdefault(storage, []).append(item)

        result: list[BackupStorageCandidate] = []
        now = datetime.now(UTC)
        for item in configurations:
            storage = item.get("storage")
            if item.get("type") != "pbs" or not isinstance(storage, str):
                continue
            content = str(item.get("content", ""))
            if "backup" not in {part.strip() for part in content.split(",")}:
                continue
            disabled = self._as_bool(item.get("disable"))
            available = not disabled and any(
                str(live.get("status", "")).lower() == "available"
                for live in live_by_storage.get(storage, [])
            )
            result.append(
                BackupStorageCandidate(
                    cluster_id=cluster.id,
                    cluster_name=cluster.name,
                    storage_id=storage,
                    datastore=self._optional_text(item.get("datastore")),
                    namespace=self._optional_text(item.get("namespace")),
                    available=available,
                    enabled_in_pve=not disabled,
                    registered_target_id=registered.get(storage),
                )
            )
        result.sort(key=lambda item: item.storage_id)
        await self._refresh_observed_targets(cluster.id, result, now)
        return result

    async def list_targets(self) -> list[BackupTargetResponse]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        rows = (
            await self._session.execute(
                select(BackupTarget, Cluster)
                .join(Cluster, Cluster.id == BackupTarget.cluster_id)
                .where(Cluster.is_active.is_(True))
                .order_by(Cluster.name, BackupTarget.storage_id)
            )
        ).all()
        return [self._target_response(target, cluster) for target, cluster in rows]

    async def create_target(self, payload: BackupTargetCreate) -> BackupTargetResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        candidates = await self.discover_storages(payload.cluster_id)
        candidate = next(
            (item for item in candidates if item.storage_id == payload.storage_id), None
        )
        if candidate is None:
            raise AppError(404, "BACKUP_STORAGE_NOT_FOUND", "The PBS storage was not found.")
        if not candidate.enabled_in_pve:
            raise AppError(409, "BACKUP_STORAGE_DISABLED", "The PBS storage is disabled in PVE.")
        if candidate.registered_target_id is not None:
            raise AppError(409, "BACKUP_TARGET_EXISTS", "The backup target is already registered.")

        now = datetime.now(UTC)
        target = BackupTarget(
            id=uuid4(),
            cluster_id=payload.cluster_id,
            storage_id=payload.storage_id,
            datastore=candidate.datastore,
            namespace=candidate.namespace,
            is_enabled=True,
            last_observed_available=candidate.available,
            last_checked_at=now,
            created_by_id=self._principal.user_id,
            version=1,
        )
        self._session.add(target)
        add_audit_event(
            self._session,
            action="BACKUP_TARGET_CREATED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="backup_target",
            target_id=target.id,
            after={"storage_id": target.storage_id, "enabled": True},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409, "BACKUP_TARGET_EXISTS", "The backup target already exists."
            ) from exc
        await self._session.refresh(target)
        cluster = await self._session.get(Cluster, payload.cluster_id)
        assert cluster is not None
        return self._target_response(target, cluster)

    async def update_target(
        self, target_id: UUID, payload: BackupTargetUpdate
    ) -> BackupTargetResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        target = await self._session.get(BackupTarget, target_id)
        if target is None:
            raise AppError(404, "BACKUP_TARGET_NOT_FOUND", "The backup target was not found.")
        if target.version != payload.version:
            raise AppError(409, "BACKUP_TARGET_VERSION_CONFLICT", "The backup target changed.")
        target.is_enabled = payload.is_enabled
        target.version += 1
        add_audit_event(
            self._session,
            action="BACKUP_TARGET_UPDATED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="backup_target",
            target_id=target.id,
            after={"enabled": target.is_enabled},
        )
        await self._session.commit()
        await self._session.refresh(target)
        cluster = await self._session.get(Cluster, target.cluster_id)
        assert cluster is not None
        return self._target_response(target, cluster)

    async def request_backup(
        self,
        workload_id: UUID,
        payload: BackupRequest,
        idempotency_key: str,
    ) -> tuple[BackupRunResponse, bool]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        workload = await self._session.scalar(
            select(Workload).where(
                Workload.id == workload_id,
                Workload.is_present.is_(True),
                Workload.is_template.is_(False),
                Workload.kind.in_(["QEMU", "LXC"]),
            )
        )
        if workload is None:
            raise AppError(404, "VM_NOT_FOUND", "The workload was not found.")
        target = await self._session.get(BackupTarget, payload.backup_target_id)
        if target is None or not target.is_enabled:
            raise AppError(404, "BACKUP_TARGET_NOT_FOUND", "The backup target was not found.")
        if target.cluster_id != workload.cluster_id:
            raise AppError(
                409,
                "BACKUP_TARGET_CLUSTER_MISMATCH",
                "The backup target belongs to another cluster.",
            )

        key_hash = self._key_hash(idempotency_key)
        fingerprint = sha256(
            json.dumps(
                {
                    "workload_id": str(workload.id),
                    "backup_target_id": str(target.id),
                    "mode": payload.mode,
                    "compression": payload.compression,
                },
                sort_keys=True,
            ).encode()
        ).digest()
        existing = await self._session.scalar(
            select(Operation).where(
                Operation.requested_by_id == self._principal.user_id,
                Operation.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The idempotency key was reused.")
            run = await self._session.scalar(
                select(BackupRun).where(BackupRun.operation_id == existing.id)
            )
            if run is None:
                raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The idempotency key was reused.")
            return await self._run_response(run.id), False

        conflict = await self._session.scalar(
            select(Operation.id).where(
                Operation.workload_id == workload.id,
                Operation.status.in_([OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]),
            )
        )
        if conflict is not None:
            raise AppError(409, "OPERATION_CONFLICT", "Another workload operation is running.")

        operation = Operation(
            id=uuid4(),
            operation_type="WORKLOAD_BACKUP",
            action="backup",
            status=OperationStatus.QUEUED.value,
            requested_by_id=self._principal.user_id,
            source_ip=self._source_ip,
            organization_id=workload.organization_id,
            cluster_id=workload.cluster_id,
            workload_id=workload.id,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            celery_task_id=str(uuid4()),
            result={
                "backup_target_id": str(target.id),
                "storage_id": target.storage_id,
                "mode": payload.mode,
                "compression": payload.compression,
                "workload_kind": workload.kind,
            },
            attempt_count=0,
            version=1,
        )
        run = BackupRun(
            id=uuid4(),
            operation_id=operation.id,
            backup_target_id=target.id,
            workload_id=workload.id,
            organization_id=workload.organization_id,
            mode=payload.mode,
            compression=payload.compression,
            status=OperationStatus.QUEUED.value,
        )
        workload_id_for_log = workload.id
        target_id_for_log = target.id
        self._session.add(operation)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(409, "OPERATION_CONFLICT", "A conflicting operation exists.") from exc
        outbox = add_operation_event(self._session, operation, BACKUP_EVENT)
        self._session.add(run)
        add_audit_event(
            self._session,
            action="WORKLOAD_BACKUP",
            outcome="ATTEMPTED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=workload.organization_id,
            workload_id=workload.id,
            operation_id=operation.id,
            source_ip=self._source_ip,
            target_type="workload",
            target_id=workload.id,
            details={"storage_id": target.storage_id, "mode": payload.mode},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            logger.exception(
                "Could not persist backup operation",
                extra={
                    "workload_id": str(workload_id_for_log),
                    "backup_target_id": str(target_id_for_log),
                },
            )
            raise AppError(409, "OPERATION_CONFLICT", "A conflicting operation exists.") from exc
        try:
            self._publisher(operation.id, operation.celery_task_id)
        except Exception:
            await record_publish_failure(self._session, outbox, self._settings)
            logger.exception(
                "Backup operation enqueue failed; worker recovery will retry",
                extra={"operation_id": str(operation.id)},
            )
        else:
            await record_publish_success(self._session, outbox)
        return await self._run_response(run.id), True

    async def list_runs(
        self,
        *,
        cluster_id: UUID | None = None,
        workload_id: UUID | None = None,
        status: OperationStatus | None = None,
        limit: int = 100,
    ) -> list[BackupRunResponse]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        query = select(BackupRun.id).join(Operation, Operation.id == BackupRun.operation_id)
        if cluster_id is not None:
            query = query.where(Operation.cluster_id == cluster_id)
        if workload_id is not None:
            query = query.where(BackupRun.workload_id == workload_id)
        if status is not None:
            query = query.where(Operation.status == status.value)
        ids = (
            await self._session.scalars(query.order_by(BackupRun.created_at.desc()).limit(limit))
        ).all()
        return [await self._run_response(run_id) for run_id in ids]

    async def get_run(self, run_id: UUID) -> BackupRunResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        return await self._run_response(run_id)

    async def request_restore(
        self,
        run_id: UUID,
        payload: RestoreRequest,
        idempotency_key: str,
    ) -> tuple[RestoreRunResponse, bool]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        row = (
            await self._session.execute(
                select(BackupRun, Operation, BackupTarget, Cluster, Workload)
                .join(Operation, Operation.id == BackupRun.operation_id)
                .join(BackupTarget, BackupTarget.id == BackupRun.backup_target_id)
                .join(Cluster, Cluster.id == BackupTarget.cluster_id)
                .join(Workload, Workload.id == BackupRun.workload_id)
                .where(BackupRun.id == run_id, Cluster.is_active.is_(True))
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "BACKUP_NOT_FOUND", "The backup run was not found.")
        backup, backup_operation, target, cluster, workload = row
        if (
            backup_operation.status != OperationStatus.SUCCEEDED.value
            or not backup.snapshot_volume_id
        ):
            raise AppError(
                409,
                "BACKUP_NOT_RESTORABLE",
                "Only a completed backup with a snapshot can be restored.",
            )
        if not target.is_enabled:
            raise AppError(409, "BACKUP_TARGET_DISABLED", "The backup target is disabled.")

        key_hash = self._key_hash(idempotency_key)
        fingerprint = sha256(
            json.dumps(
                {
                    "backup_run_id": str(backup.id),
                    "target_node": payload.target_node,
                    "target_vmid": payload.target_vmid,
                    "target_name": payload.target_name,
                },
                sort_keys=True,
            ).encode()
        ).digest()
        existing = await self._session.scalar(
            select(Operation).where(
                Operation.requested_by_id == self._principal.user_id,
                Operation.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The idempotency key was reused.")
            restore = await self._session.scalar(
                select(RestoreRun).where(RestoreRun.operation_id == existing.id)
            )
            if restore is None:
                raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "The idempotency key was reused.")
            return await self._restore_response(restore.id), False

        local_collision = await self._session.scalar(
            select(Workload.id).where(
                Workload.cluster_id == cluster.id,
                Workload.vmid == payload.target_vmid,
                Workload.is_present.is_(True),
            )
        )
        if local_collision is not None:
            raise AppError(409, "RESTORE_VMID_IN_USE", "The target VMID is already in use.")
        async with self._open_client(cluster) as client:
            nodes = await client.get_nodes()
            if not any(
                item.get("node") == payload.target_node
                and str(item.get("status", "")).lower() == "online"
                for item in nodes
            ):
                raise AppError(409, "RESTORE_NODE_UNAVAILABLE", "The target node is unavailable.")
            guests = await client.get_guests()
        if any(
            item.get("vmid") in {payload.target_vmid, str(payload.target_vmid)} for item in guests
        ):
            raise AppError(409, "RESTORE_VMID_IN_USE", "The target VMID is already in use.")

        operation = Operation(
            id=uuid4(),
            operation_type="WORKLOAD_RESTORE",
            action="restore",
            status=OperationStatus.QUEUED.value,
            requested_by_id=self._principal.user_id,
            source_ip=self._source_ip,
            organization_id=None,
            cluster_id=cluster.id,
            workload_id=workload.id,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            celery_task_id=str(uuid4()),
            result={
                "backup_run_id": str(backup.id),
                "snapshot_volume_id": backup.snapshot_volume_id,
                "storage_id": target.storage_id,
                "workload_kind": workload.kind,
                "target_node": payload.target_node,
                "target_vmid": payload.target_vmid,
                "target_name": payload.target_name,
            },
            attempt_count=0,
            version=1,
        )
        restore = RestoreRun(
            id=uuid4(),
            operation_id=operation.id,
            backup_run_id=backup.id,
            cluster_id=cluster.id,
            source_workload_id=workload.id,
            target_node=payload.target_node,
            target_vmid=payload.target_vmid,
            target_name=payload.target_name,
            status=OperationStatus.QUEUED.value,
        )
        self._session.add(operation)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "RESTORE_SOURCE_CONFLICT",
                "Another operation is running for the source workload.",
            ) from exc
        outbox = add_operation_event(self._session, operation, RESTORE_EVENT)
        self._session.add(restore)
        add_audit_event(
            self._session,
            action="WORKLOAD_RESTORE",
            outcome="ATTEMPTED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            workload_id=workload.id,
            operation_id=operation.id,
            source_ip=self._source_ip,
            target_type="backup_run",
            target_id=backup.id,
            details={
                "target_node": payload.target_node,
                "target_vmid": payload.target_vmid,
                "target_name": payload.target_name,
            },
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(409, "RESTORE_CONFLICT", "A conflicting restore is running.") from exc
        try:
            self._restore_publisher(operation.id, operation.celery_task_id)
        except Exception:
            await record_publish_failure(self._session, outbox, self._settings)
            logger.exception(
                "Restore operation enqueue failed; worker recovery will retry",
                extra={"operation_id": str(operation.id)},
            )
        else:
            await record_publish_success(self._session, outbox)
        return await self._restore_response(restore.id), True

    async def get_restore(self, restore_id: UUID) -> RestoreRunResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        return await self._restore_response(restore_id)

    async def _run_response(self, run_id: UUID) -> BackupRunResponse:
        row = (
            await self._session.execute(
                select(BackupRun, Operation, BackupTarget, Cluster, Workload, Organization)
                .join(Operation, Operation.id == BackupRun.operation_id)
                .join(BackupTarget, BackupTarget.id == BackupRun.backup_target_id)
                .join(Cluster, Cluster.id == BackupTarget.cluster_id)
                .join(Workload, Workload.id == BackupRun.workload_id)
                .outerjoin(Organization, Organization.id == BackupRun.organization_id)
                .where(BackupRun.id == run_id)
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "BACKUP_NOT_FOUND", "The backup run was not found.")
        run, operation, target, cluster, workload, organization = row
        task = await self._session.scalar(
            select(PveTask)
            .where(PveTask.operation_id == operation.id)
            .order_by(PveTask.submitted_at.desc())
        )
        return BackupRunResponse(
            id=run.id,
            operation_id=operation.id,
            backup_target_id=target.id,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            storage_id=target.storage_id,
            workload_id=workload.id,
            workload_name=workload.name,
            vmid=workload.vmid,
            kind=workload.kind,
            source_node=workload.node,
            organization_id=run.organization_id,
            organization_name=organization.name if organization is not None else None,
            mode=run.mode,
            compression=run.compression,
            status=OperationStatus(operation.status),
            snapshot_volume_id=run.snapshot_volume_id,
            snapshot_time=run.snapshot_time,
            size_bytes=run.size_bytes,
            transferred_bytes=run.transferred_bytes,
            error_code=operation.error_code,
            error_summary=operation.error_summary,
            retryable=operation.retryable,
            pve_exit_status=task.pve_exit_status if task is not None else None,
            requested_at=operation.requested_at,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
        )

    async def _restore_response(self, restore_id: UUID) -> RestoreRunResponse:
        row = (
            await self._session.execute(
                select(RestoreRun, Operation, BackupRun, Cluster, Workload)
                .join(Operation, Operation.id == RestoreRun.operation_id)
                .join(BackupRun, BackupRun.id == RestoreRun.backup_run_id)
                .join(Cluster, Cluster.id == RestoreRun.cluster_id)
                .join(Workload, Workload.id == RestoreRun.source_workload_id)
                .where(RestoreRun.id == restore_id)
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "RESTORE_NOT_FOUND", "The restore run was not found.")
        restore, operation, backup, cluster, workload = row
        task = await self._session.scalar(
            select(PveTask)
            .where(PveTask.operation_id == operation.id)
            .order_by(PveTask.submitted_at.desc())
        )
        assert backup.snapshot_volume_id is not None
        return RestoreRunResponse(
            id=restore.id,
            operation_id=operation.id,
            backup_run_id=backup.id,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            source_workload_id=workload.id,
            source_workload_name=workload.name,
            kind=workload.kind,
            snapshot_volume_id=backup.snapshot_volume_id,
            target_node=restore.target_node,
            target_vmid=restore.target_vmid,
            target_name=restore.target_name,
            status=OperationStatus(operation.status),
            error_code=operation.error_code,
            error_summary=operation.error_summary,
            retryable=operation.retryable,
            pve_exit_status=task.pve_exit_status if task is not None else None,
            requested_at=operation.requested_at,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
        )

    async def _active_cluster(self, cluster_id: UUID) -> Cluster:
        cluster = await self._session.scalar(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.is_active.is_(True))
        )
        if cluster is None:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
        return cluster

    @asynccontextmanager
    async def _open_client(self, cluster: Cluster) -> AsyncIterator[ProxmoxClient]:
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == cluster.id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if credential is None:
            raise AppError(409, "CLUSTER_CREDENTIAL_MISSING", "The cluster has no credential.")
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
            raise AppError(
                500,
                "CREDENTIAL_DECRYPTION_FAILED",
                "The stored Proxmox credential could not be decrypted.",
            ) from exc
        client = ProxmoxClient(
            api_base_url=cluster.api_base_url,
            token_identifier=credential.token_identifier,
            token_secret=secret,
            ca_bundle_pem=cluster.ca_bundle_pem,
            connect_timeout=self._settings.pve_connect_timeout_seconds,
            read_timeout=self._settings.pve_read_timeout_seconds,
            max_connections=self._settings.pve_max_connections,
            max_keepalive_connections=self._settings.pve_max_keepalive_connections,
            allowed_hosts=self._settings.pve_allowed_hosts,
            allowed_networks=self._settings.pve_allowed_networks,
            transport=self._transport,
        )
        async with client:
            yield client

    async def _refresh_observed_targets(
        self,
        cluster_id: UUID,
        candidates: list[BackupStorageCandidate],
        checked_at: datetime,
    ) -> None:
        by_storage = {item.storage_id: item for item in candidates}
        targets = (
            await self._session.scalars(
                select(BackupTarget).where(BackupTarget.cluster_id == cluster_id)
            )
        ).all()
        changed = False
        for target in targets:
            candidate = by_storage.get(target.storage_id)
            target.last_observed_available = candidate.available if candidate is not None else False
            target.last_checked_at = checked_at
            if candidate is not None:
                target.datastore = candidate.datastore
                target.namespace = candidate.namespace
            changed = True
        if changed:
            await self._session.commit()

    @staticmethod
    def _target_response(target: BackupTarget, cluster: Cluster) -> BackupTargetResponse:
        return BackupTargetResponse(
            id=target.id,
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            storage_id=target.storage_id,
            datastore=target.datastore,
            namespace=target.namespace,
            is_enabled=target.is_enabled,
            available=target.last_observed_available,
            last_checked_at=target.last_checked_at,
            created_at=target.created_at,
            updated_at=target.updated_at,
            version=target.version,
        )

    def _key_hash(self, key: str) -> bytes:
        secret = self._settings.app_secret_key.get_secret_value().encode()
        return hmac.new(secret, key.encode(), sha256).digest()

    @staticmethod
    def _as_bool(value: object) -> bool:
        return value is True or value == 1 or str(value).lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
