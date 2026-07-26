import asyncio
import re
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
from app.models.auth import Organization, OrganizationMember, User, UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import (
    AdminVmAction,
    Operation,
    OperationStatus,
    PowerAction,
    PveTask,
    Workload,
)
from app.proxmox.client import ProxmoxClient
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event
from app.services.customer_notifications import queue_customer_notification

Sleep = Callable[[float], Awaitable[None]]
RETRYABLE_ERROR_CODES = {"CLUSTER_UNREACHABLE", "PVE_UPSTREAM_ERROR", "PVE_TIMEOUT"}


class PowerApi(Protocol):
    async def submit_guest_power_action(
        self, *, kind: str, node: str, vmid: int, action: str
    ) -> str: ...

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]: ...

    async def get_guest_status(self, *, kind: str, node: str, vmid: int) -> dict[str, Any]: ...
    async def get_guest_config(self, *, kind: str, node: str, vmid: int) -> dict[str, Any]: ...
    async def configure_guest(
        self, *, kind: str, node: str, vmid: int, cores: int, memory_mib: int
    ) -> None: ...
    async def resize_guest_disk(
        self, *, kind: str, node: str, vmid: int, disk: str, size_bytes: int
    ) -> None: ...
    async def delete_guest(self, *, kind: str, node: str, vmid: int) -> str: ...


class UnavailablePowerApi:
    def __init__(self, error: AppError) -> None:
        self._error = error

    async def submit_guest_power_action(
        self, *, kind: str, node: str, vmid: int, action: str
    ) -> str:
        del kind, node, vmid, action
        raise self._error

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]:
        del node, upid
        raise self._error

    async def get_guest_status(self, *, kind: str, node: str, vmid: int) -> dict[str, Any]:
        del kind, node, vmid
        raise self._error

    async def get_guest_config(self, *, kind: str, node: str, vmid: int) -> dict[str, Any]:
        del kind, node, vmid
        raise self._error

    async def configure_guest(
        self, *, kind: str, node: str, vmid: int, cores: int, memory_mib: int
    ) -> None:
        del kind, node, vmid, cores, memory_mib
        raise self._error

    async def resize_guest_disk(
        self, *, kind: str, node: str, vmid: int, disk: str, size_bytes: int
    ) -> None:
        del kind, node, vmid, disk, size_bytes
        raise self._error

    async def delete_guest(self, *, kind: str, node: str, vmid: int) -> str:
        del kind, node, vmid
        raise self._error


class PowerOperationRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        client: PowerApi | None = None,
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
        if operation is None or operation.status in {
            OperationStatus.SUCCEEDED.value,
            OperationStatus.FAILED.value,
            OperationStatus.TIMEOUT.value,
            OperationStatus.CANCELLED.value,
            OperationStatus.NEEDS_ATTENTION.value,
        }:
            return
        if operation.action in {item.value for item in AdminVmAction}:
            await self._run_admin_action(operation)
            return
        workload = await self._session.get(Workload, operation.workload_id)
        actor = await self._session.get(User, operation.requested_by_id)
        if (
            workload is None
            or not workload.is_present
            or workload.is_template
            or workload.kind not in {"QEMU", "LXC"}
            or (workload.kind == "LXC" and operation.action == PowerAction.RESET.value)
        ):
            await self._finish(
                operation,
                actor,
                status=OperationStatus.FAILED,
                error_code="VM_NOT_FOUND",
                retryable=False,
            )
            return
        actor_authorized = actor is not None and actor.is_active
        if actor_authorized and actor is not None and actor.role == UserRole.CUSTOMER.value:
            membership = await self._session.scalar(
                select(OrganizationMember.id)
                .join(
                    Organization,
                    Organization.id == OrganizationMember.organization_id,
                )
                .where(
                    OrganizationMember.user_id == actor.id,
                    OrganizationMember.organization_id == operation.organization_id,
                    Organization.is_active.is_(True),
                )
            )
            actor_authorized = (
                membership is not None
                and workload.organization_id == operation.organization_id
                and operation.action
                in {
                    PowerAction.START.value,
                    PowerAction.SHUTDOWN.value,
                    PowerAction.STOP.value,
                    PowerAction.REBOOT.value,
                }
            )
        elif actor_authorized and actor is not None:
            actor_authorized = actor.role in {
                UserRole.SUPER_ADMIN.value,
                UserRole.OPERATOR.value,
            }
        if not actor_authorized:
            await self._finish(
                operation,
                actor,
                status=OperationStatus.FAILED,
                error_code="PERMISSION_REVOKED",
                retryable=False,
            )
            return
        assert actor is not None

        pve_task = await self._session.scalar(
            select(PveTask).where(PveTask.operation_id == operation.id)
        )
        now = datetime.now(UTC)
        recovering_without_upid = operation.status == OperationStatus.RUNNING.value
        if recovering_without_upid and pve_task is None:
            lease_start = operation.heartbeat_at or operation.started_at
            if lease_start is not None and lease_start > now - timedelta(
                seconds=self._settings.operation_lease_seconds
            ):
                await self._session.rollback()
                return
        operation.status = OperationStatus.RUNNING.value
        operation.started_at = operation.started_at or now
        operation.heartbeat_at = now
        operation.attempt_count += 1
        operation.version += 1
        await self._session.commit()

        action = PowerAction(operation.action)
        async with self._open_client(workload) as client:
            if pve_task is None:
                current = await self._safe_guest_status(operation, actor, workload, client)
                if current is None:
                    return
                try:
                    no_op = self._no_op_result(action, current)
                except AppError as exc:
                    await self._finish_from_error(operation, actor, exc, submission=False)
                    return
                if no_op is not None:
                    workload.power_state = str(current.get("status", "unknown")).upper()
                    workload.observed_at = datetime.now(UTC)
                    operation.result = {**operation.result, **no_op}
                    await self._finish(
                        operation,
                        actor,
                        status=OperationStatus.SUCCEEDED,
                        retryable=False,
                    )
                    return
                if recovering_without_upid and action in {PowerAction.REBOOT, PowerAction.RESET}:
                    await self._finish(
                        operation,
                        actor,
                        status=OperationStatus.NEEDS_ATTENTION,
                        error_code="OPERATION_STATE_UNKNOWN",
                        retryable=False,
                    )
                    return
                upid = await self._submit(operation, actor, workload, client)
                if upid is None:
                    return
                pve_task = PveTask(
                    id=uuid4(),
                    operation_id=operation.id,
                    cluster_id=workload.cluster_id,
                    workload_id=workload.id,
                    step_name=f"power_{action.value}",
                    upid=upid,
                    status="RUNNING",
                    pve_node=workload.node,
                    submitted_at=datetime.now(UTC),
                    poll_attempts=0,
                )
                self._session.add(pve_task)
                operation.heartbeat_at = datetime.now(UTC)
                await self._session.commit()
            await self._poll(operation, actor, workload, pve_task, client)

    async def _run_admin_action(self, operation: Operation) -> None:
        workload = await self._session.get(Workload, operation.workload_id)
        actor = await self._session.get(User, operation.requested_by_id)
        if workload is None or not workload.is_present or workload.is_template:
            await self._finish(
                operation,
                actor,
                status=OperationStatus.FAILED,
                error_code="VM_NOT_FOUND",
                retryable=False,
            )
            return
        if actor is None or not actor.is_active or actor.role != UserRole.SUPER_ADMIN.value:
            await self._finish(
                operation,
                actor,
                status=OperationStatus.FAILED,
                error_code="PERMISSION_REVOKED",
                retryable=False,
            )
            return
        operation.status = OperationStatus.RUNNING.value
        operation.started_at = operation.started_at or datetime.now(UTC)
        operation.heartbeat_at = datetime.now(UTC)
        operation.attempt_count += 1
        operation.version += 1
        await self._session.commit()

        async with self._open_client(workload) as client:
            try:
                if operation.action == AdminVmAction.UPDATE_SPEC.value:
                    await self._apply_spec(operation, workload, client)
                    await self._finish(
                        operation, actor, status=OperationStatus.SUCCEEDED, retryable=False
                    )
                    return
                current = await client.get_guest_status(
                    kind=workload.kind, node=workload.node, vmid=workload.vmid
                )
                if str(current.get("status", "unknown")).lower() != "stopped":
                    raise AppError(409, "VM_NOT_STOPPED", "Stop the VM before deleting it.")
                upid = await client.delete_guest(
                    kind=workload.kind, node=workload.node, vmid=workload.vmid
                )
                task = PveTask(
                    operation_id=operation.id,
                    cluster_id=workload.cluster_id,
                    workload_id=workload.id,
                    step_name="delete",
                    upid=upid,
                    status="RUNNING",
                    pve_node=workload.node,
                    submitted_at=datetime.now(UTC),
                    poll_attempts=0,
                )
                self._session.add(task)
                await self._session.commit()
                await self._poll_delete(operation, actor, workload, task, client)
            except AppError as exc:
                await self._finish_from_error(operation, actor, exc, submission=False)

    async def _apply_spec(self, operation: Operation, workload: Workload, client: PowerApi) -> None:
        cores_value = operation.result.get("cpu_cores")
        memory_value = operation.result.get("memory_bytes")
        if not isinstance(cores_value, int) or not isinstance(memory_value, int):
            raise AppError(500, "OPERATION_PAYLOAD_INVALID", "The VM operation is invalid.")
        cores = cores_value
        memory_bytes = memory_value
        disk_bytes_value = operation.result.get("disk_bytes")
        await client.configure_guest(
            kind=workload.kind,
            node=workload.node,
            vmid=workload.vmid,
            cores=cores,
            memory_mib=memory_bytes // 1024**2,
        )
        if isinstance(disk_bytes_value, int) and disk_bytes_value > (workload.disk_bytes or 0):
            config = await client.get_guest_config(
                kind=workload.kind, node=workload.node, vmid=workload.vmid
            )
            disk = self._resizable_disk(workload.kind, config)
            await client.resize_guest_disk(
                kind=workload.kind,
                node=workload.node,
                vmid=workload.vmid,
                disk=disk,
                size_bytes=disk_bytes_value,
            )
            workload.disk_bytes = disk_bytes_value
        workload.cpu_cores = cores
        workload.memory_bytes = memory_bytes
        workload.observed_at = datetime.now(UTC)
        workload.version += 1
        operation.result = {**operation.result, "applied": True}

    @staticmethod
    def _resizable_disk(kind: str, config: dict[str, Any]) -> str:
        if kind == "LXC":
            if "rootfs" not in config:
                raise AppError(409, "VM_DISK_LAYOUT_UNSUPPORTED", "The root disk was not found.")
            return "rootfs"
        disks = [
            key
            for key, value in config.items()
            if re.fullmatch(r"(?:scsi|virtio|sata|ide)\d+", key)
            and "media=cdrom" not in str(value)
            and "cloudinit" not in str(value)
        ]
        if len(disks) != 1:
            raise AppError(409, "VM_DISK_LAYOUT_UNSUPPORTED", "Exactly one VM disk is required.")
        return disks[0]

    async def _poll_delete(
        self,
        operation: Operation,
        actor: User,
        workload: Workload,
        task: PveTask,
        client: PowerApi,
    ) -> None:
        for _ in range(self._settings.pve_task_max_poll_attempts):
            task.poll_attempts += 1
            task.last_polled_at = datetime.now(UTC)
            status = await client.get_task_status(node=task.pve_node, upid=task.upid)
            if status.get("status") == "running":
                await self._session.commit()
                await self._sleep(self._settings.pve_task_poll_interval_seconds)
                continue
            task.pve_exit_status = str(status.get("exitstatus", ""))
            task.completed_at = datetime.now(UTC)
            if status.get("status") != "stopped" or status.get("exitstatus") != "OK":
                task.status = "FAILED"
                await self._finish(
                    operation,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code="PVE_TASK_FAILED",
                    retryable=False,
                    pve_task=task,
                )
                return
            task.status = "SUCCEEDED"
            workload.is_present = False
            workload.observed_at = datetime.now(UTC)
            workload.version += 1
            operation.result = {**operation.result, "deleted": True}
            await self._finish(
                operation, actor, status=OperationStatus.SUCCEEDED, retryable=False, pve_task=task
            )
            return
        task.status = "TIMEOUT"
        await self._finish(
            operation,
            actor,
            status=OperationStatus.TIMEOUT,
            error_code="PVE_TASK_TIMEOUT",
            retryable=True,
            pve_task=task,
        )

    async def _safe_guest_status(
        self,
        operation: Operation,
        actor: User,
        workload: Workload,
        client: PowerApi,
    ) -> dict[str, Any] | None:
        try:
            return await client.get_guest_status(
                kind=workload.kind, node=workload.node, vmid=workload.vmid
            )
        except AppError as exc:
            await self._finish_from_error(operation, actor, exc, submission=False)
            return None

    async def _submit(
        self,
        operation: Operation,
        actor: User,
        workload: Workload,
        client: PowerApi,
    ) -> str | None:
        for attempt in range(1, self._settings.pve_action_max_attempts + 1):
            try:
                return await client.submit_guest_power_action(
                    kind=workload.kind,
                    node=workload.node,
                    vmid=workload.vmid,
                    action=operation.action,
                )
            except AppError as exc:
                retryable = exc.code in RETRYABLE_ERROR_CODES
                ambiguous_timeout = exc.code == "PVE_TIMEOUT"
                if (
                    retryable
                    and not ambiguous_timeout
                    and attempt < self._settings.pve_action_max_attempts
                ):
                    await self._sleep(min(float(2 ** (attempt - 1)), 5.0))
                    continue
                await self._finish_from_error(
                    operation,
                    actor,
                    exc,
                    submission=True,
                )
                return None
        return None

    async def _poll(
        self,
        operation: Operation,
        actor: User,
        workload: Workload,
        pve_task: PveTask,
        client: PowerApi,
    ) -> None:
        deadline = datetime.now(UTC) + timedelta(seconds=self._settings.pve_task_timeout_seconds)
        while (
            datetime.now(UTC) < deadline
            and pve_task.poll_attempts < self._settings.pve_task_max_poll_attempts
        ):
            pve_task.poll_attempts += 1
            pve_task.last_polled_at = datetime.now(UTC)
            operation.heartbeat_at = pve_task.last_polled_at
            try:
                status = await client.get_task_status(node=pve_task.pve_node, upid=pve_task.upid)
            except AppError as exc:
                if exc.code in RETRYABLE_ERROR_CODES:
                    await self._session.commit()
                    await self._sleep(self._settings.pve_task_poll_interval_seconds)
                    continue
                pve_task.status = "FAILED"
                pve_task.error_code = exc.code
                await self._finish_from_error(
                    operation, actor, exc, submission=False, pve_task=pve_task
                )
                return
            task_status = status.get("status")
            if task_status == "running":
                await self._session.commit()
                await self._sleep(self._settings.pve_task_poll_interval_seconds)
                continue
            if task_status != "stopped":
                pve_task.status = "FAILED"
                pve_task.error_code = "PVE_INVALID_RESPONSE"
                await self._finish(
                    operation,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code="PVE_INVALID_RESPONSE",
                    retryable=False,
                    pve_task=pve_task,
                )
                return
            exit_status = status.get("exitstatus")
            pve_task.pve_exit_status = str(exit_status) if exit_status is not None else None
            pve_task.completed_at = datetime.now(UTC)
            if exit_status != "OK":
                pve_task.status = "FAILED"
                await self._finish(
                    operation,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code="PVE_TASK_FAILED",
                    retryable=False,
                    pve_task=pve_task,
                )
                return
            pve_task.status = "SUCCEEDED"
            final_state = await self._safe_guest_status(operation, actor, workload, client)
            if final_state is None:
                return
            workload.power_state = str(final_state.get("status", "unknown")).upper()
            workload.observed_at = datetime.now(UTC)
            expected_state = (
                "STOPPED"
                if PowerAction(operation.action) in {PowerAction.SHUTDOWN, PowerAction.STOP}
                else "RUNNING"
            )
            if workload.power_state != expected_state:
                await self._finish(
                    operation,
                    actor,
                    status=OperationStatus.FAILED,
                    error_code="PVE_STATE_MISMATCH",
                    retryable=True,
                    pve_task=pve_task,
                )
                return
            operation.result = {
                **operation.result,
                "no_op": False,
                "final_power_state": workload.power_state,
            }
            await self._finish(
                operation,
                actor,
                status=OperationStatus.SUCCEEDED,
                retryable=False,
                pve_task=pve_task,
            )
            return
        pve_task.status = "TIMEOUT"
        pve_task.error_code = "PVE_TASK_TIMEOUT"
        await self._finish(
            operation,
            actor,
            status=OperationStatus.TIMEOUT,
            error_code="PVE_TASK_TIMEOUT",
            retryable=True,
            pve_task=pve_task,
        )

    async def _finish_from_error(
        self,
        operation: Operation,
        actor: User,
        error: AppError,
        *,
        submission: bool,
        pve_task: PveTask | None = None,
    ) -> None:
        is_timeout = error.code == "PVE_TIMEOUT"
        status = OperationStatus.TIMEOUT if is_timeout else OperationStatus.FAILED
        if submission and is_timeout:
            status = OperationStatus.NEEDS_ATTENTION
        await self._finish(
            operation,
            actor,
            status=status,
            error_code=error.code,
            retryable=error.code in RETRYABLE_ERROR_CODES and not (submission and is_timeout),
            pve_task=pve_task,
        )

    async def _finish(
        self,
        operation: Operation,
        actor: User | None,
        *,
        status: OperationStatus,
        retryable: bool,
        error_code: str | None = None,
        pve_task: PveTask | None = None,
    ) -> None:
        operation.status = status.value
        operation.finished_at = datetime.now(UTC)
        operation.heartbeat_at = operation.finished_at
        operation.error_code = error_code
        operation.error_summary = self._safe_error_summary(error_code)
        operation.retryable = retryable
        operation.version += 1
        add_audit_event(
            self._session,
            action=operation.operation_type,
            outcome="SUCCEEDED" if status is OperationStatus.SUCCEEDED else "FAILED",
            request_id=None,
            actor_user_id=actor.id if actor is not None else operation.requested_by_id,
            actor_role=UserRole(actor.role) if actor is not None else None,
            organization_id=operation.organization_id,
            workload_id=operation.workload_id,
            operation_id=operation.id,
            source_ip=operation.source_ip,
            pve_upid=pve_task.upid if pve_task is not None else None,
            target_type=(
                "vm" if operation.action in {item.value for item in AdminVmAction} else "workload"
            ),
            target_id=operation.workload_id,
            details={
                "action_mode": operation.result.get("action_mode", "STANDARD"),
                "workload_kind": operation.result.get("workload_kind", ""),
                "status": status.value,
                "error_code": error_code or "",
            },
        )
        if (
            actor is not None
            and actor.role == UserRole.CUSTOMER.value
            and operation.organization_id is not None
        ):
            outcome = "완료" if status is OperationStatus.SUCCEEDED else "실패"
            await queue_customer_notification(
                self._session,
                organization_id=operation.organization_id,
                recipient_user_id=actor.id,
                event_type="OPERATION_COMPLETED",
                event_key=f"power-operation:{operation.id}:{status.value}",
                subject=f"가상 머신 작업 {outcome}",
                message=f"요청한 전원 작업이 {status.value} 상태로 종료되었습니다.",
            )
        await self._session.commit()

    @staticmethod
    def _no_op_result(action: PowerAction, status: dict[str, Any]) -> dict[str, object] | None:
        state = str(status.get("status", "unknown")).lower()
        if action is PowerAction.START and state == "running":
            return {
                "no_op": True,
                "message": "The workload is already running.",
                "final_power_state": "RUNNING",
            }
        if action in {PowerAction.SHUTDOWN, PowerAction.STOP} and state == "stopped":
            return {
                "no_op": True,
                "message": "The workload is already stopped.",
                "final_power_state": "STOPPED",
            }
        if action in {PowerAction.REBOOT, PowerAction.RESET} and state != "running":
            raise AppError(
                status_code=409,
                code="VM_NOT_RUNNING",
                message="The workload is not running.",
            )
        return None

    @staticmethod
    def _safe_error_summary(error_code: str | None) -> str | None:
        return f"Workload operation failed ({error_code})." if error_code else None

    @asynccontextmanager
    async def _open_client(self, workload: Workload) -> AsyncIterator[PowerApi]:
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
            yield UnavailablePowerApi(
                AppError(
                    status_code=409,
                    code="CLUSTER_UNAVAILABLE",
                    message="The workload cluster is unavailable.",
                )
            )
            return
        try:
            token_secret = self._cipher.decrypt(
                EncryptedCredential(
                    ciphertext=credential.secret_ciphertext,
                    nonce=credential.secret_nonce,
                    key_version=credential.key_version,
                ),
                cluster_id=cluster.id,
                credential_id=credential.id,
            )
        except (InvalidTag, UnicodeDecodeError):
            yield UnavailablePowerApi(
                AppError(
                    status_code=500,
                    code="CREDENTIAL_DECRYPTION_FAILED",
                    message="The stored Proxmox credential could not be decrypted.",
                )
            )
            return
        try:
            client = ProxmoxClient(
                api_base_url=cluster.api_base_url,
                token_identifier=credential.token_identifier,
                token_secret=token_secret,
                ca_bundle_pem=cluster.ca_bundle_pem,
                connect_timeout=self._settings.pve_connect_timeout_seconds,
                read_timeout=self._settings.pve_read_timeout_seconds,
                max_connections=self._settings.pve_max_connections,
                max_keepalive_connections=self._settings.pve_max_keepalive_connections,
                allowed_hosts=self._settings.pve_allowed_hosts,
                allowed_networks=self._settings.pve_allowed_networks,
            )
        except AppError as exc:
            yield UnavailablePowerApi(exc)
            return
        async with client:
            yield client


async def run_power_operation(operation_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            runner = PowerOperationRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
            )
            try:
                await runner.run(operation_id)
            except AppError:
                await session.rollback()
                raise
    finally:
        await engine.dispose()
