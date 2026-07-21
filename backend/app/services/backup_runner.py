import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.models.auth import User, UserRole
from app.models.backup import BackupRun, BackupTarget
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, OperationStatus, PveTask, Workload
from app.proxmox.client import ProxmoxClient
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event

Sleep = Callable[[float], Awaitable[None]]
POLL_RETRYABLE_ERRORS = {"CLUSTER_UNREACHABLE", "PVE_UPSTREAM_ERROR", "PVE_TIMEOUT"}
_SIZE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTPE]?i?B)\b", re.I)
_PERCENT_PATTERN = re.compile(r"\((?P<value>\d+(?:\.\d+)?)%\)")
_SIZE_MULTIPLIERS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
    "EB": 1000**6,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
    "PIB": 1024**5,
    "EIB": 1024**6,
}


def backup_transfer_bytes(log_entries: list[dict[str, Any]]) -> int | None:
    """Return bytes newly sent to PBS, excluding data reported as reused."""
    transferred: int | None = None
    reused: int | None = None
    reused_percent: Decimal | None = None
    for entry in log_entries:
        text = entry.get("t")
        if not isinstance(text, str):
            continue
        lowered = text.casefold()
        if "transferred" in lowered:
            parsed = _first_size(text[lowered.index("transferred") + len("transferred") :])
            if parsed is not None:
                transferred = parsed
        if "reused" in lowered:
            parsed = _first_size(text[lowered.index("reused") + len("reused") :])
            if parsed is not None:
                reused = parsed
            percent_match = _PERCENT_PATTERN.search(text)
            if percent_match is not None:
                try:
                    reused_percent = Decimal(percent_match.group("value"))
                except InvalidOperation:
                    reused_percent = None
    if transferred is None:
        return None
    if reused is None:
        return transferred
    difference = transferred - reused
    if difference <= 0:
        return 0 if reused_percent == Decimal(100) else None
    return difference


def _first_size(value: str) -> int | None:
    match = _SIZE_PATTERN.search(value)
    if match is None:
        return None
    try:
        amount = Decimal(match.group("value"))
    except InvalidOperation:
        return None
    multiplier = _SIZE_MULTIPLIERS.get(match.group("unit").upper())
    return int(amount * multiplier) if multiplier is not None else None


class BackupApi(Protocol):
    async def submit_guest_backup(
        self,
        *,
        node: str,
        vmid: int,
        storage: str,
        mode: str,
        compression: str,
    ) -> str: ...

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]: ...

    async def get_backup_content(
        self, *, node: str, storage: str, vmid: int
    ) -> list[dict[str, Any]]: ...

    async def get_task_log(self, *, node: str, upid: str) -> list[dict[str, Any]]: ...


class BackupOperationRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        client: BackupApi | None = None,
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
        if operation is None or operation.operation_type != "WORKLOAD_BACKUP":
            return
        if operation.status in {
            OperationStatus.SUCCEEDED.value,
            OperationStatus.FAILED.value,
            OperationStatus.TIMEOUT.value,
        }:
            return
        run = await self._session.scalar(
            select(BackupRun).where(BackupRun.operation_id == operation.id)
        )
        workload = await self._session.get(Workload, operation.workload_id)
        target = (
            await self._session.get(BackupTarget, run.backup_target_id) if run is not None else None
        )
        actor = await self._session.get(User, operation.requested_by_id)
        if (
            run is None
            or workload is None
            or not workload.is_present
            or workload.is_template
            or workload.kind not in {"QEMU", "LXC"}
            or target is None
            or not target.is_enabled
            or target.cluster_id != workload.cluster_id
        ):
            await self._finish(
                operation,
                run,
                actor,
                status=OperationStatus.FAILED,
                error_code="BACKUP_TARGET_OR_WORKLOAD_UNAVAILABLE",
                retryable=False,
            )
            return
        if (
            actor is None
            or not actor.is_active
            or actor.role
            not in {
                UserRole.SUPER_ADMIN.value,
                UserRole.OPERATOR.value,
            }
        ):
            await self._finish(
                operation,
                run,
                actor,
                status=OperationStatus.FAILED,
                error_code="PERMISSION_REVOKED",
                retryable=False,
            )
            return

        pve_task = await self._session.scalar(
            select(PveTask).where(PveTask.operation_id == operation.id)
        )
        now = datetime.now(UTC)
        if operation.status == OperationStatus.RUNNING.value and pve_task is None:
            lease_start = operation.heartbeat_at or operation.started_at
            if lease_start is not None and lease_start > now - timedelta(
                seconds=self._settings.operation_lease_seconds
            ):
                await self._session.rollback()
                return
            await self._finish(
                operation,
                run,
                actor,
                status=OperationStatus.FAILED,
                error_code="BACKUP_SUBMISSION_STATE_UNKNOWN",
                retryable=False,
            )
            return

        operation.status = OperationStatus.RUNNING.value
        operation.started_at = operation.started_at or now
        operation.heartbeat_at = now
        operation.attempt_count += 1
        operation.version += 1
        run.status = OperationStatus.RUNNING.value
        run.started_at = run.started_at or now
        await self._session.commit()

        try:
            async with self._open_client(workload) as client:
                if pve_task is None:
                    try:
                        upid = await client.submit_guest_backup(
                            node=workload.node,
                            vmid=workload.vmid,
                            storage=target.storage_id,
                            mode=run.mode,
                            compression=run.compression,
                        )
                    except AppError as exc:
                        await self._finish(
                            operation,
                            run,
                            actor,
                            status=(
                                OperationStatus.TIMEOUT
                                if exc.code == "PVE_TIMEOUT"
                                else OperationStatus.FAILED
                            ),
                            error_code=exc.code,
                            retryable=False,
                        )
                        return
                    pve_task = PveTask(
                        id=uuid4(),
                        operation_id=operation.id,
                        cluster_id=workload.cluster_id,
                        workload_id=workload.id,
                        step_name="backup",
                        upid=upid,
                        status="RUNNING",
                        pve_node=workload.node,
                        submitted_at=datetime.now(UTC),
                        poll_attempts=0,
                    )
                    self._session.add(pve_task)
                    operation.heartbeat_at = datetime.now(UTC)
                    await self._session.commit()
                await self._poll(operation, run, actor, workload, target, pve_task, client)
        except AppError as exc:
            await self._finish(
                operation,
                run,
                actor,
                status=OperationStatus.FAILED,
                error_code=exc.code,
                retryable=False,
                pve_task=pve_task,
            )

    async def _poll(
        self,
        operation: Operation,
        run: BackupRun,
        actor: User,
        workload: Workload,
        target: BackupTarget,
        task: PveTask,
        client: BackupApi,
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
                    run,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code=exc.code,
                    retryable=False,
                    pve_task=task,
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
                    run,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code="PVE_TASK_FAILED",
                    retryable=False,
                    pve_task=task,
                )
                return
            task.status = "SUCCEEDED"
            metadata_pending = False
            transfer_measurement_pending = False
            try:
                content = await client.get_backup_content(
                    node=workload.node,
                    storage=target.storage_id,
                    vmid=workload.vmid,
                )
                snapshot = self._latest_snapshot(content, workload.vmid)
                if snapshot is not None:
                    run.snapshot_volume_id = str(snapshot["volid"])
                    ctime = snapshot.get("ctime")
                    if isinstance(ctime, (int, float)):
                        run.snapshot_time = datetime.fromtimestamp(ctime, tz=UTC)
                    size = snapshot.get("size")
                    if isinstance(size, int) and size >= 0:
                        run.size_bytes = size
                else:
                    metadata_pending = True
            except AppError:
                metadata_pending = True
            try:
                run.transferred_bytes = backup_transfer_bytes(
                    await client.get_task_log(node=task.pve_node, upid=task.upid)
                )
                transfer_measurement_pending = run.transferred_bytes is None
            except AppError:
                transfer_measurement_pending = True
            operation.result = {
                **operation.result,
                "snapshot_volume_id": run.snapshot_volume_id,
                "metadata_pending": metadata_pending,
                "transfer_measurement_pending": transfer_measurement_pending,
            }
            await self._finish(
                operation,
                run,
                actor,
                status=OperationStatus.SUCCEEDED,
                retryable=False,
                pve_task=task,
            )
            return

        task.status = "TIMEOUT"
        task.error_code = "PVE_TASK_TIMEOUT"
        await self._finish(
            operation,
            run,
            actor,
            status=OperationStatus.TIMEOUT,
            error_code="PVE_TASK_TIMEOUT",
            retryable=True,
            pve_task=task,
        )

    async def _finish(
        self,
        operation: Operation,
        run: BackupRun | None,
        actor: User | None,
        *,
        status: OperationStatus,
        retryable: bool,
        error_code: str | None = None,
        pve_task: PveTask | None = None,
    ) -> None:
        now = datetime.now(UTC)
        operation.status = status.value
        operation.finished_at = now
        operation.heartbeat_at = now
        operation.error_code = error_code
        operation.error_summary = (
            f"Backup operation failed ({error_code})." if error_code is not None else None
        )
        operation.retryable = retryable
        operation.version += 1
        if run is not None:
            run.status = status.value
            run.finished_at = now
        add_audit_event(
            self._session,
            action="WORKLOAD_BACKUP",
            outcome="SUCCEEDED" if status is OperationStatus.SUCCEEDED else "FAILED",
            request_id=None,
            actor_user_id=actor.id if actor is not None else operation.requested_by_id,
            actor_role=UserRole(actor.role) if actor is not None else None,
            organization_id=operation.organization_id,
            workload_id=operation.workload_id,
            operation_id=operation.id,
            source_ip=operation.source_ip,
            pve_upid=pve_task.upid if pve_task is not None else None,
            target_type="workload",
            target_id=operation.workload_id,
            details={
                "storage_id": operation.result.get("storage_id", ""),
                "status": status.value,
                "error_code": error_code or "",
            },
            error_code=error_code,
        )
        await self._session.commit()

    @staticmethod
    def _latest_snapshot(content: list[dict[str, Any]], vmid: int) -> dict[str, Any] | None:
        candidates = [
            item
            for item in content
            if isinstance(item.get("volid"), str)
            and item.get("content", "backup") == "backup"
            and (item.get("vmid") in {None, vmid, str(vmid)})
        ]

        def timestamp(item: dict[str, Any]) -> float:
            value = item.get("ctime")
            return float(value) if isinstance(value, (int, float)) else 0.0

        return max(candidates, key=timestamp, default=None)

    @asynccontextmanager
    async def _open_client(self, workload: Workload) -> AsyncIterator[BackupApi]:
        if self._injected_client is not None:
            yield self._injected_client
            return
        cluster = await self._session.get(Cluster, workload.cluster_id)
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == workload.cluster_id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if cluster is None or credential is None or not cluster.is_active:
            raise AppError(409, "CLUSTER_UNAVAILABLE", "The workload cluster is unavailable.")
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


async def run_backup_operation(operation_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            runner = BackupOperationRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
            )
            await runner.run(operation_id)
    finally:
        await engine.dispose()
