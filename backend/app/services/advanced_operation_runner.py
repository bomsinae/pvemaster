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
from app.models.advanced_operations import AdvancedOperationIntent, AdvancedOperationTarget
from app.models.auth import User, UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, OperationStatus, PveTask, Workload
from app.proxmox.client import ProxmoxClient
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event

Sleep = Callable[[float], Awaitable[None]]
RETRYABLE = {"CLUSTER_UNREACHABLE", "PVE_UPSTREAM_ERROR", "PVE_TIMEOUT"}


class AdvancedApi(Protocol):
    async def submit_guest_snapshot(
        self,
        *,
        kind: str,
        node: str,
        vmid: int,
        snapshot_name: str,
        include_memory: bool,
    ) -> str: ...

    async def delete_guest_snapshot(
        self, *, kind: str, node: str, vmid: int, snapshot_name: str
    ) -> str: ...

    async def rollback_guest_snapshot(
        self, *, kind: str, node: str, vmid: int, snapshot_name: str
    ) -> str: ...

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
    ) -> str: ...

    async def submit_guest_power_action(
        self, *, kind: str, node: str, vmid: int, action: str
    ) -> str: ...

    async def update_ha_resource(
        self, *, resource_id: str, state: str, group: str | None
    ) -> None: ...

    async def configure_guest_advanced(
        self, *, kind: str, node: str, vmid: int, values: dict[str, str]
    ) -> None: ...

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]: ...


class AdvancedOperationRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        client: AdvancedApi | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._client = client
        self._sleep = sleep

    async def run(self, operation_id: UUID) -> None:
        row = (
            await self._session.execute(
                select(Operation, AdvancedOperationIntent)
                .join(
                    AdvancedOperationIntent,
                    AdvancedOperationIntent.operation_id == Operation.id,
                )
                .where(Operation.id == operation_id)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            return
        operation, intent = row
        if operation.status in {
            item.value
            for item in (
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.TIMEOUT,
                OperationStatus.CANCELLED,
                OperationStatus.NEEDS_ATTENTION,
            )
        }:
            return
        actor = await self._session.get(User, operation.requested_by_id)
        if (
            actor is None
            or not actor.is_active
            or actor.role != UserRole.SUPER_ADMIN.value
        ):
            await self._finish(
                operation,
                intent,
                actor,
                OperationStatus.FAILED,
                "PERMISSION_REVOKED",
            )
            return
        if operation.status == OperationStatus.RUNNING.value:
            task = await self._current_task(operation, intent)
            if task is None:
                await self._finish(
                    operation,
                    intent,
                    actor,
                    OperationStatus.NEEDS_ATTENTION,
                    "OPERATION_STATE_UNKNOWN",
                )
                return
        operation.status = OperationStatus.RUNNING.value
        intent.status = "RUNNING"
        operation.started_at = operation.started_at or datetime.now(UTC)
        operation.heartbeat_at = datetime.now(UTC)
        operation.attempt_count += 1
        operation.version += 1
        await self._session.commit()

        first_target = intent.target_snapshot[0]
        first_workload = await self._session.get(
            Workload, UUID(str(first_target["workload_id"]))
        )
        if first_workload is None:
            await self._finish(
                operation, intent, actor, OperationStatus.FAILED, "WORKLOAD_NOT_FOUND"
            )
            return
        async with self._open_client(first_workload) as client:
            while intent.current_target_index < len(intent.target_snapshot):
                target = intent.target_snapshot[intent.current_target_index]
                workload = await self._validate_target(target)
                if workload is None:
                    await self._finish(
                        operation,
                        intent,
                        actor,
                        OperationStatus.NEEDS_ATTENTION,
                        "TARGET_STATE_CHANGED",
                    )
                    return
                task = await self._current_task(operation, intent)
                if task is None:
                    try:
                        upid = await self._submit(intent, workload, client)
                    except AppError as exc:
                        state = (
                            OperationStatus.NEEDS_ATTENTION
                            if exc.code == "PVE_TIMEOUT"
                            else OperationStatus.FAILED
                        )
                        await self._finish(operation, intent, actor, state, exc.code)
                        return
                    if upid is None:
                        await self._complete_target(intent, workload)
                        continue
                    task = PveTask(
                        id=uuid4(),
                        operation_id=operation.id,
                        cluster_id=workload.cluster_id,
                        workload_id=workload.id,
                        step_name=f"advanced_target_{intent.current_target_index}",
                        upid=upid,
                        status="RUNNING",
                        pve_node=workload.node,
                        submitted_at=datetime.now(UTC),
                        poll_attempts=0,
                    )
                    self._session.add(task)
                    operation.heartbeat_at = datetime.now(UTC)
                    await self._session.commit()
                if not await self._poll(operation, intent, actor, workload, task, client):
                    return
            await self._finish(
                operation, intent, actor, OperationStatus.SUCCEEDED, None
            )

    async def _submit(
        self,
        intent: AdvancedOperationIntent,
        workload: Workload,
        client: AdvancedApi,
    ) -> str | None:
        options = intent.options_snapshot
        if intent.feature == "SNAPSHOT":
            name = str(options["snapshot_name"])
            if intent.action == "CREATE":
                return await client.submit_guest_snapshot(
                    kind=workload.kind,
                    node=workload.node,
                    vmid=workload.vmid,
                    snapshot_name=name,
                    include_memory=bool(options.get("include_memory")),
                )
            if intent.action == "DELETE":
                return await client.delete_guest_snapshot(
                    kind=workload.kind,
                    node=workload.node,
                    vmid=workload.vmid,
                    snapshot_name=name,
                )
            return await client.rollback_guest_snapshot(
                kind=workload.kind,
                node=workload.node,
                vmid=workload.vmid,
                snapshot_name=name,
            )
        if intent.feature == "NODE_MAINTENANCE" and intent.action in {"ENTER", "EXIT"}:
            return None
        if intent.feature in {"MIGRATION", "NODE_MAINTENANCE"}:
            return await client.migrate_guest(
                kind=workload.kind,
                node=workload.node,
                vmid=workload.vmid,
                target_node=str(options["target_node"]),
                online=(
                    intent.feature == "MIGRATION" and intent.action == "LIVE"
                )
                or workload.power_state.upper() == "RUNNING",
                target_storage=self._optional_string(options.get("target_storage")),
                target_network=self._optional_string(options.get("target_network")),
            )
        if intent.feature == "BULK":
            return await client.submit_guest_power_action(
                kind=workload.kind,
                node=workload.node,
                vmid=workload.vmid,
                action=intent.action.lower(),
            )
        if intent.feature == "HA":
            await client.update_ha_resource(
                resource_id=f"{'vm' if workload.kind == 'QEMU' else 'ct'}:{workload.vmid}",
                state=str(options["requested_state"]),
                group=self._optional_string(options.get("group")),
            )
            return None
        if intent.feature == "GUEST_CONFIG":
            await client.configure_guest_advanced(
                kind=workload.kind,
                node=workload.node,
                vmid=workload.vmid,
                values=self._config_values(options),
            )
            return None
        raise AppError(409, "ADVANCED_ACTION_UNSUPPORTED", "The action cannot be executed.")

    async def _poll(
        self,
        operation: Operation,
        intent: AdvancedOperationIntent,
        actor: User,
        workload: Workload,
        task: PveTask,
        client: AdvancedApi,
    ) -> bool:
        deadline = datetime.now(UTC) + timedelta(
            seconds=self._settings.pve_task_timeout_seconds
        )
        while (
            datetime.now(UTC) < deadline
            and task.poll_attempts < self._settings.pve_task_max_poll_attempts
        ):
            task.poll_attempts += 1
            task.last_polled_at = datetime.now(UTC)
            operation.heartbeat_at = task.last_polled_at
            try:
                status = await client.get_task_status(node=task.pve_node, upid=task.upid)
            except AppError as exc:
                if exc.code in RETRYABLE:
                    await self._session.commit()
                    await self._sleep(self._settings.pve_task_poll_interval_seconds)
                    continue
                task.status = "FAILED"
                await self._finish(
                    operation, intent, actor, OperationStatus.FAILED, exc.code
                )
                return False
            if status.get("status") == "running":
                await self._session.commit()
                await self._sleep(self._settings.pve_task_poll_interval_seconds)
                continue
            task.completed_at = datetime.now(UTC)
            task.pve_exit_status = str(status.get("exitstatus", ""))
            if status.get("status") != "stopped" or status.get("exitstatus") != "OK":
                task.status = "FAILED"
                await self._finish(
                    operation, intent, actor, OperationStatus.FAILED, "PVE_TASK_FAILED"
                )
                return False
            task.status = "SUCCEEDED"
            await self._complete_target(intent, workload)
            await self._session.commit()
            return True
        task.status = "TIMEOUT"
        await self._finish(
            operation, intent, actor, OperationStatus.TIMEOUT, "PVE_TASK_TIMEOUT"
        )
        return False

    async def _complete_target(
        self, intent: AdvancedOperationIntent, workload: Workload
    ) -> None:
        if intent.feature == "MIGRATION" or (
            intent.feature == "NODE_MAINTENANCE" and intent.action == "DRAIN"
        ):
            workload.node = str(intent.options_snapshot["target_node"])
            workload.observed_at = datetime.now(UTC)
            workload.version += 1
        if intent.feature == "BULK":
            workload.power_state = (
                "STOPPED"
                if intent.action in {"STOP", "SHUTDOWN"}
                else "RUNNING"
            )
            workload.observed_at = datetime.now(UTC)
        observed = dict(intent.observed_state)
        observed[str(workload.id)] = {
            "status": "SUCCEEDED",
            "node": workload.node,
            "power_state": workload.power_state,
        }
        intent.observed_state = observed
        intent.current_target_index += 1
        await self._session.commit()

    async def _validate_target(
        self, target: dict[str, object]
    ) -> Workload | None:
        workload = await self._session.get(Workload, UUID(str(target["workload_id"])))
        if (
            workload is None
            or not workload.is_present
            or workload.is_template
            or workload.version != int(str(target["version"]))
            or workload.node != str(target["node"])
        ):
            return None
        return workload

    async def _current_task(
        self, operation: Operation, intent: AdvancedOperationIntent
    ) -> PveTask | None:
        task: PveTask | None = await self._session.scalar(
            select(PveTask).where(
                PveTask.operation_id == operation.id,
                PveTask.step_name == f"advanced_target_{intent.current_target_index}",
            )
        )
        return task

    async def _finish(
        self,
        operation: Operation,
        intent: AdvancedOperationIntent,
        actor: User | None,
        status: OperationStatus,
        error_code: str | None,
    ) -> None:
        now = datetime.now(UTC)
        operation.status = status.value
        operation.finished_at = now
        operation.heartbeat_at = now
        operation.error_code = error_code
        operation.error_summary = (
            f"Advanced operation failed ({error_code})." if error_code else None
        )
        operation.retryable = status is OperationStatus.TIMEOUT
        operation.version += 1
        intent.status = (
            status.value
            if status
            in {
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.NEEDS_ATTENTION,
            }
            else "FAILED"
        )
        targets = await self._session.scalars(
            select(AdvancedOperationTarget).where(
                AdvancedOperationTarget.operation_id == operation.id
            )
        )
        for target in targets:
            target.active = False
        add_audit_event(
            self._session,
            action=operation.operation_type,
            outcome="SUCCEEDED" if status is OperationStatus.SUCCEEDED else "FAILED",
            request_id=None,
            actor_user_id=actor.id if actor is not None else operation.requested_by_id,
            actor_role=UserRole(actor.role) if actor is not None else None,
            workload_id=operation.workload_id,
            operation_id=operation.id,
            source_ip=operation.source_ip,
            target_type="advanced_operation",
            target_id=operation.id,
            details={
                "feature": intent.feature,
                "target_count": len(intent.target_snapshot),
                "completed_targets": intent.current_target_index,
                "error_code": error_code or "",
            },
        )
        await self._session.commit()

    @staticmethod
    def _config_values(options: dict[str, object]) -> dict[str, str]:
        values: dict[str, str] = {}
        if isinstance(options.get("cores"), int):
            values["cores"] = str(options["cores"])
        if isinstance(options.get("memory_mib"), int):
            values["memory"] = str(options["memory_mib"])
        bridge = options.get("bridge")
        vlan = options.get("vlan_tag")
        if isinstance(bridge, str):
            values["net0"] = f"virtio,bridge={bridge}" + (
                f",tag={vlan}" if isinstance(vlan, int) else ""
            )
        if isinstance(options.get("boot_order"), str):
            values["boot"] = f"order={options['boot_order']}"
        cloud_init = options.get("cloud_init")
        if isinstance(cloud_init, dict):
            for source, target in (
                ("user", "ciuser"),
                ("nameserver", "nameserver"),
                ("ipconfig0", "ipconfig0"),
            ):
                value = cloud_init.get(source)
                if isinstance(value, str):
                    values[target] = value
        if not values:
            raise AppError(422, "GUEST_CONFIG_EMPTY", "No supported configuration was provided.")
        return values

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @asynccontextmanager
    async def _open_client(self, workload: Workload) -> AsyncIterator[AdvancedApi]:
        if self._client is not None:
            yield self._client
            return
        cluster = await self._session.get(Cluster, workload.cluster_id)
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == workload.cluster_id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if cluster is None or credential is None or not cluster.is_active:
            raise AppError(409, "CLUSTER_UNAVAILABLE", "The cluster is unavailable.")
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


async def run_advanced_operation(operation_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            runner = AdvancedOperationRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
            )
            await runner.run(operation_id)
    finally:
        await engine.dispose()
