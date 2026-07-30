import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.models.auth import User, UserRole
from app.models.backup import BackupRun, BackupTarget, RestoreRun
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, OperationStatus, PveTask, Workload
from app.proxmox.client import ProxmoxClient
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event

Sleep = Callable[[float], Awaitable[None]]
POLL_RETRYABLE_ERRORS = {"CLUSTER_UNREACHABLE", "PVE_UPSTREAM_ERROR", "PVE_TIMEOUT"}


class RestoreApi(Protocol):
    async def get_guests(self) -> list[dict[str, Any]]: ...

    async def get_nodes(self) -> list[dict[str, Any]]: ...

    async def submit_guest_restore(
        self, *, kind: str, node: str, archive: str, vmid: int, name: str
    ) -> str: ...

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]: ...


class RestoreOperationRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        client: RestoreApi | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._injected_client = client
        self._sleep = sleep

    async def run(self, operation_id: UUID) -> None:
        operation = await self._session.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if operation is None or operation.operation_type != "WORKLOAD_RESTORE":
            return
        if operation.status in {
            OperationStatus.SUCCEEDED.value,
            OperationStatus.FAILED.value,
            OperationStatus.TIMEOUT.value,
            OperationStatus.CANCELLED.value,
            OperationStatus.NEEDS_ATTENTION.value,
        }:
            return
        restore = await self._session.scalar(
            select(RestoreRun).where(RestoreRun.operation_id == operation.id)
        )
        backup = (
            await self._session.get(BackupRun, restore.backup_run_id)
            if restore is not None
            else None
        )
        target = (
            await self._session.get(BackupTarget, backup.backup_target_id)
            if backup is not None
            else None
        )
        workload = await self._session.get(Workload, operation.workload_id)
        cluster = await self._session.get(Cluster, operation.cluster_id)
        actor = await self._session.get(User, operation.requested_by_id)
        if (
            restore is None
            or backup is None
            or not backup.snapshot_volume_id
            or backup.status != OperationStatus.SUCCEEDED.value
            or target is None
            or not target.is_enabled
            or workload is None
            or workload.kind not in {"QEMU", "LXC"}
            or cluster is None
            or not cluster.is_active
        ):
            await self._finish(
                operation,
                restore,
                actor,
                status=OperationStatus.FAILED,
                error_code="RESTORE_SOURCE_UNAVAILABLE",
                retryable=False,
            )
            return
        if actor is None or not actor.is_active or actor.role != UserRole.SUPER_ADMIN.value:
            await self._finish(
                operation,
                restore,
                actor,
                status=OperationStatus.FAILED,
                error_code="PERMISSION_REVOKED",
                retryable=False,
            )
            return

        task = await self._session.scalar(
            select(PveTask).where(PveTask.operation_id == operation.id)
        )
        now = datetime.now(UTC)
        if operation.status == OperationStatus.RUNNING.value and task is None:
            lease_start = operation.heartbeat_at or operation.started_at
            if lease_start is not None and lease_start > now - timedelta(
                seconds=self._settings.operation_lease_seconds
            ):
                await self._session.rollback()
                return
            await self._finish(
                operation,
                restore,
                actor,
                status=OperationStatus.NEEDS_ATTENTION,
                error_code="RESTORE_SUBMISSION_STATE_UNKNOWN",
                retryable=False,
                restore_status=OperationStatus.FAILED,
            )
            return

        operation.status = OperationStatus.RUNNING.value
        operation.started_at = operation.started_at or now
        operation.heartbeat_at = now
        operation.attempt_count += 1
        operation.version += 1
        restore.status = OperationStatus.RUNNING.value
        restore.started_at = restore.started_at or now
        await self._session.commit()

        try:
            async with self._open_client(cluster) as client:
                if task is None:
                    if not await self._target_is_free(client, restore):
                        await self._finish(
                            operation,
                            restore,
                            actor,
                            status=OperationStatus.FAILED,
                            error_code="RESTORE_TARGET_CONFLICT",
                            retryable=False,
                        )
                        return
                    try:
                        upid = await client.submit_guest_restore(
                            kind=workload.kind,
                            node=restore.target_node,
                            archive=backup.snapshot_volume_id,
                            vmid=restore.target_vmid,
                            name=restore.target_name,
                        )
                    except AppError as exc:
                        await self._finish(
                            operation,
                            restore,
                            actor,
                            status=(
                                OperationStatus.NEEDS_ATTENTION
                                if exc.code == "PVE_TIMEOUT"
                                else OperationStatus.FAILED
                            ),
                            error_code=exc.code,
                            retryable=False,
                            restore_status=(
                                OperationStatus.TIMEOUT if exc.code == "PVE_TIMEOUT" else None
                            ),
                        )
                        return
                    task = PveTask(
                        id=uuid4(),
                        operation_id=operation.id,
                        cluster_id=cluster.id,
                        workload_id=workload.id,
                        step_name="restore",
                        upid=upid,
                        status="RUNNING",
                        pve_node=restore.target_node,
                        submitted_at=datetime.now(UTC),
                        poll_attempts=0,
                    )
                    self._session.add(task)
                    operation.heartbeat_at = datetime.now(UTC)
                    await self._session.commit()
                await self._poll(operation, restore, actor, task, client)
        except AppError as exc:
            await self._finish(
                operation,
                restore,
                actor,
                status=OperationStatus.FAILED,
                error_code=exc.code,
                retryable=False,
                task=task,
            )

    async def _target_is_free(self, client: RestoreApi, restore: RestoreRun) -> bool:
        nodes = await client.get_nodes()
        if not any(
            item.get("node") == restore.target_node
            and str(item.get("status", "")).lower() == "online"
            for item in nodes
        ):
            return False
        guests = await client.get_guests()
        return not any(
            item.get("vmid") in {restore.target_vmid, str(restore.target_vmid)} for item in guests
        )

    async def _poll(
        self,
        operation: Operation,
        restore: RestoreRun,
        actor: User,
        task: PveTask,
        client: RestoreApi,
    ) -> None:
        deadline = datetime.now(UTC) + timedelta(seconds=self._settings.pve_task_timeout_seconds)
        while (
            datetime.now(UTC) < deadline
            and task.poll_attempts < self._settings.pve_task_max_poll_attempts
        ):
            task.poll_attempts += 1
            task.last_polled_at = datetime.now(UTC)
            operation.heartbeat_at = task.last_polled_at
            try:
                task_status = await client.get_task_status(node=task.pve_node, upid=task.upid)
            except AppError as exc:
                if exc.code in POLL_RETRYABLE_ERRORS:
                    await self._session.commit()
                    await self._sleep(self._settings.pve_task_poll_interval_seconds)
                    continue
                task.status = "FAILED"
                task.error_code = exc.code
                await self._finish(
                    operation,
                    restore,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code=exc.code,
                    retryable=False,
                    task=task,
                )
                return
            if task_status.get("status") == "running":
                await self._session.commit()
                await self._sleep(self._settings.pve_task_poll_interval_seconds)
                continue
            exit_status = task_status.get("exitstatus")
            task.pve_exit_status = str(exit_status) if exit_status is not None else None
            task.completed_at = datetime.now(UTC)
            if task_status.get("status") != "stopped" or exit_status != "OK":
                task.status = "FAILED"
                await self._finish(
                    operation,
                    restore,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code="PVE_TASK_FAILED",
                    retryable=False,
                    task=task,
                )
                return
            task.status = "SUCCEEDED"
            operation.result = {**operation.result, "restored": True}
            await self._finish(
                operation,
                restore,
                actor,
                status=OperationStatus.SUCCEEDED,
                retryable=False,
                task=task,
            )
            return

        task.status = "TIMEOUT"
        task.error_code = "PVE_TASK_TIMEOUT"
        await self._finish(
            operation,
            restore,
            actor,
            status=OperationStatus.TIMEOUT,
            error_code="PVE_TASK_TIMEOUT",
            retryable=True,
            task=task,
        )

    async def _finish(
        self,
        operation: Operation,
        restore: RestoreRun | None,
        actor: User | None,
        *,
        status: OperationStatus,
        retryable: bool,
        error_code: str | None = None,
        task: PveTask | None = None,
        restore_status: OperationStatus | None = None,
    ) -> None:
        now = datetime.now(UTC)
        operation.status = status.value
        operation.finished_at = now
        operation.heartbeat_at = now
        operation.error_code = error_code
        operation.error_summary = (
            f"Restore operation failed ({error_code})." if error_code is not None else None
        )
        operation.retryable = retryable
        operation.version += 1
        if restore is not None:
            restore.status = (restore_status or status).value
            restore.finished_at = now
        add_audit_event(
            self._session,
            action="WORKLOAD_RESTORE",
            outcome="SUCCEEDED" if status is OperationStatus.SUCCEEDED else "FAILED",
            request_id=None,
            actor_user_id=actor.id if actor is not None else operation.requested_by_id,
            actor_role=UserRole(actor.role) if actor is not None else None,
            workload_id=operation.workload_id,
            operation_id=operation.id,
            source_ip=operation.source_ip,
            pve_upid=task.upid if task is not None else None,
            target_type="backup_run",
            target_id=restore.backup_run_id if restore is not None else None,
            details={
                "target_node": restore.target_node if restore is not None else "",
                "target_vmid": restore.target_vmid if restore is not None else 0,
                "status": status.value,
                "error_code": error_code or "",
            },
            error_code=error_code,
        )
        await self._session.commit()

    @asynccontextmanager
    async def _open_client(self, cluster: Cluster) -> AsyncIterator[RestoreApi]:
        if self._injected_client is not None:
            yield self._injected_client
            return
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == cluster.id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if credential is None:
            raise AppError(409, "CLUSTER_UNAVAILABLE", "The restore cluster is unavailable.")
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
        )
        async with client:
            yield client


async def run_restore_operation(operation_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            runner = RestoreOperationRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
            )
            await runner.run(operation_id)
    finally:
        await engine.dispose()
