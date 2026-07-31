import asyncio
import ipaddress
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import and_, func, nullsfirst, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.models.auth import Organization, User, UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.ipam import IpAddress, IpPool
from app.models.operation import Workload, WorkloadAssignment
from app.models.provisioning import (
    Product,
    ProvisioningNode,
    ProvisioningRequest,
    ProvisioningStatus,
    ProvisioningStep,
    ProvisioningStepStatus,
    Template,
    TemplateOsType,
)
from app.proxmox.client import ProxmoxClient
from app.security.access import Principal
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.security.provisioning_secrets import (
    EncryptedProvisioningSecret,
    ProvisioningSecretCipher,
)
from app.services.audit import add_audit_event
from app.services.ipam import IpamService

Sleep = Callable[[float], Awaitable[None]]
logger = logging.getLogger(__name__)


class ProvisioningApi(Protocol):
    async def get_guests(self) -> list[dict[str, Any]]: ...

    async def clone_qemu_template(
        self,
        *,
        source_node: str,
        source_vmid: int,
        target_node: str,
        target_vmid: int,
        name: str,
        storage: str,
    ) -> str: ...

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]: ...

    async def configure_qemu(self, *, node: str, vmid: int, values: dict[str, str]) -> None: ...

    async def resize_qemu_disk(
        self, *, node: str, vmid: int, disk: str, size_bytes: int
    ) -> None: ...

    async def submit_vm_power_action(self, *, node: str, vmid: int, action: str) -> str: ...

    async def get_vm_status(self, *, node: str, vmid: int) -> dict[str, Any]: ...


class UnavailableProvisioningApi:
    def __init__(self, error: AppError) -> None:
        self._error = error

    async def get_guests(self) -> list[dict[str, Any]]:
        raise self._error

    async def clone_qemu_template(self, **_: Any) -> str:
        raise self._error

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]:
        del node, upid
        raise self._error

    async def configure_qemu(self, *, node: str, vmid: int, values: dict[str, str]) -> None:
        del node, vmid, values
        raise self._error

    async def resize_qemu_disk(self, *, node: str, vmid: int, disk: str, size_bytes: int) -> None:
        del node, vmid, disk, size_bytes
        raise self._error

    async def submit_vm_power_action(self, *, node: str, vmid: int, action: str) -> str:
        del node, vmid, action
        raise self._error

    async def get_vm_status(self, *, node: str, vmid: int) -> dict[str, Any]:
        del node, vmid
        raise self._error


class ProvisioningRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        client: ProvisioningApi | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._injected_client = client
        self._sleep = sleep
        self._request: ProvisioningRequest
        self._actor: User
        self._principal: Principal
        self._runner_id = uuid4()

    async def run(self, request_id: UUID) -> None:
        request = await self._claim(request_id)
        if request is None:
            return
        self._request = request
        try:
            await self._run_claimed(request)
        finally:
            await self._release_claim(request.id)

    async def _claim(self, request_id: UUID) -> ProvisioningRequest | None:
        now = datetime.now(UTC)
        claimed_id = await self._session.scalar(
            update(ProvisioningRequest)
            .where(
                ProvisioningRequest.id == request_id,
                or_(
                    ProvisioningRequest.status == ProvisioningStatus.QUEUED.value,
                    and_(
                        ProvisioningRequest.status == ProvisioningStatus.RUNNING.value,
                        or_(
                            ProvisioningRequest.runner_id.is_(None),
                            ProvisioningRequest.lease_expires_at.is_(None),
                            ProvisioningRequest.lease_expires_at <= now,
                        ),
                    ),
                ),
            )
            .values(
                status=ProvisioningStatus.RUNNING.value,
                runner_id=self._runner_id,
                lease_expires_at=self._lease_deadline(now),
                started_at=func.coalesce(ProvisioningRequest.started_at, now),
                heartbeat_at=now,
                version=ProvisioningRequest.version + 1,
            )
            .returning(ProvisioningRequest.id)
        )
        if claimed_id is None:
            await self._session.rollback()
            return None
        await self._session.commit()
        return await self._session.get(ProvisioningRequest, claimed_id)

    async def _run_claimed(self, request: ProvisioningRequest) -> None:
        actor = await self._session.get(User, request.requested_by_id)
        if actor is None or not actor.is_active or actor.role != UserRole.SUPER_ADMIN.value:
            request.runner_id = None
            request.lease_expires_at = None
            await self._fail_request(
                request,
                None,
                AppError(
                    status_code=403,
                    code="PERMISSION_REVOKED",
                    message="The provisioning requester is no longer authorized.",
                ),
            )
            return
        self._actor = actor
        self._principal = Principal(
            user_id=actor.id,
            email=actor.email,
            role=UserRole(actor.role),
            session_epoch=actor.session_epoch,
        )
        async with self._open_client(request.target_cluster_id) as client:
            steps = await self._session.scalars(
                select(ProvisioningStep)
                .where(ProvisioningStep.provisioning_request_id == request.id)
                .order_by(ProvisioningStep.step_order)
            )
            for step in steps.all():
                if step.status == ProvisioningStepStatus.SUCCEEDED.value:
                    continue
                request.current_step = step.step_name
                self._touch_lease(request)
                step.status = ProvisioningStepStatus.RUNNING.value
                step.started_at = step.started_at or datetime.now(UTC)
                step.attempt_count += 1
                step.error_code = None
                step.error_summary = None
                await self._session.commit()
                try:
                    result = await self._execute_step(step, client)
                except AppError as exc:
                    await self._fail_request(request, step, exc)
                    return
                except Exception as exc:
                    await self._fail_request(
                        request,
                        step,
                        AppError(
                            status_code=500,
                            code="PROVISIONING_INTERNAL_ERROR",
                            message="The provisioning step failed unexpectedly.",
                        ),
                    )
                    raise exc
                step.status = ProvisioningStepStatus.SUCCEEDED.value
                step.safe_result = result
                step.finished_at = datetime.now(UTC)
                request.version += 1
                self._touch_lease(request)
                await self._session.commit()

        request.status = ProvisioningStatus.SUCCEEDED.value
        request.current_step = "COMPLETED"
        request.finished_at = datetime.now(UTC)
        request.heartbeat_at = request.finished_at
        request.runner_id = None
        request.lease_expires_at = None
        request.error_code = None
        request.error_summary = None
        add_audit_event(
            self._session,
            action="VM_PROVISION",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=actor.id,
            actor_role=UserRole(actor.role),
            organization_id=request.organization_id,
            workload_id=request.workload_id,
            target_type="provisioning_request",
            target_id=request.id,
            details={"target_vmid": request.target_vmid},
        )
        await self._session.commit()

    async def _record_partial_workload(self) -> None:
        if self._request.target_vmid is None or self._request.target_node_id is None:
            return
        clone_wait = await self._session.scalar(
            select(ProvisioningStep).where(
                ProvisioningStep.provisioning_request_id == self._request.id,
                ProvisioningStep.step_name == "WAIT_CLONE",
            )
        )
        if clone_wait is None or clone_wait.status != ProvisioningStepStatus.SUCCEEDED.value:
            return
        node = await self._session.get(ProvisioningNode, self._request.target_node_id)
        if node is None:
            return
        workload_id = uuid4()
        await self._session.execute(
            insert(Workload)
            .values(
                id=workload_id,
                cluster_id=self._request.target_cluster_id,
                vmid=self._request.target_vmid,
                node=node.name,
                kind="QEMU",
                name=self._request.target_name,
                power_state="UNKNOWN",
                cpu_cores=self._int_spec("cpu_cores"),
                memory_bytes=self._int_spec("memory_bytes"),
                disk_bytes=self._int_spec("disk_bytes"),
                is_template=False,
                is_present=True,
                organization_id=None,
                observed_at=datetime.now(UTC),
                version=1,
            )
            .on_conflict_do_nothing(index_elements=["cluster_id", "vmid"])
        )
        workload = await self._session.scalar(
            select(Workload).where(
                Workload.cluster_id == self._request.target_cluster_id,
                Workload.vmid == self._request.target_vmid,
            )
        )
        if workload is None:
            return
        if workload.organization_id not in (None, self._request.organization_id):
            return
        workload.is_present = True
        workload.observed_at = datetime.now(UTC)
        self._request.workload_id = workload.id
        add_audit_event(
            self._session,
            action="PARTIAL_PROVISIONING_VM_RECORDED",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._request.requested_by_id,
            actor_role=UserRole.SUPER_ADMIN,
            organization_id=workload.organization_id,
            workload_id=workload.id,
            target_type="workload",
            target_id=workload.id,
            after={
                "cluster_id": str(workload.cluster_id),
                "vmid": workload.vmid,
                "organization_id": (
                    str(workload.organization_id) if workload.organization_id else None
                ),
                "provisioning_request_id": str(self._request.id),
            },
        )

    def _touch_lease(self, request: ProvisioningRequest) -> None:
        now = datetime.now(UTC)
        request.heartbeat_at = now
        request.lease_expires_at = self._lease_deadline(now)

    def _lease_deadline(self, now: datetime) -> datetime:
        request_window = (
            self._settings.pve_connect_timeout_seconds
            + self._settings.pve_read_timeout_seconds
            + self._settings.pve_task_poll_interval_seconds
            + 5
        )
        seconds = max(float(self._settings.operation_lease_seconds), request_window)
        return now + timedelta(seconds=seconds)

    async def _release_claim(self, request_id: UUID) -> None:
        try:
            await self._session.rollback()
            await self._session.execute(
                update(ProvisioningRequest)
                .where(
                    ProvisioningRequest.id == request_id,
                    ProvisioningRequest.runner_id == self._runner_id,
                    ProvisioningRequest.status == ProvisioningStatus.RUNNING.value,
                )
                .values(runner_id=None, lease_expires_at=None)
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()

    async def _execute_step(
        self, step: ProvisioningStep, client: ProvisioningApi
    ) -> dict[str, object]:
        handlers: dict[str, Callable[[], Awaitable[dict[str, object]]]] = {
            "VALIDATE_REQUEST": self._validate_request,
            "CHECK_PRODUCT": self._check_product,
            "SELECT_TARGET": self._select_target,
            "RESERVE_VMID": lambda: self._reserve_vmid(client),
            "RESERVE_IP": self._reserve_ip,
            "FULL_CLONE": lambda: self._full_clone(step, client),
            "WAIT_CLONE": lambda: self._wait_clone(client),
            "CONFIGURE_COMPUTE": lambda: self._configure_compute(client),
            "RESIZE_DISK": lambda: self._resize_disk(client),
            "CONFIGURE_NETWORK": lambda: self._configure_network(client),
            "CONFIGURE_IDENTITY": lambda: self._configure_identity(client),
            "START_VM": lambda: self._start_vm(step, client),
            "VERIFY_STATUS": lambda: self._verify_status(client),
            "ASSIGN_ORGANIZATION": self._assign_organization,
            "CONFIRM_IP": self._confirm_ip,
        }
        handler = handlers.get(step.step_name)
        if handler is None:
            raise AppError(500, "UNKNOWN_PROVISIONING_STEP", "The provisioning step is unknown.")
        return await handler()

    async def _validate_request(self) -> dict[str, object]:
        organization = await self._session.get(Organization, self._request.organization_id)
        cluster = await self._session.get(Cluster, self._request.target_cluster_id)
        if organization is None or not organization.is_active:
            raise AppError(409, "ORGANIZATION_UNAVAILABLE", "The organization is unavailable.")
        if cluster is None or not cluster.is_active:
            raise AppError(409, "CLUSTER_UNAVAILABLE", "The cluster is unavailable.")
        return {"validated": True}

    async def _check_product(self) -> dict[str, object]:
        product = await self._session.get(Product, self._request.product_id)
        template = await self._session.get(Template, self._request.template_id)
        if product is None or not product.is_enabled:
            raise AppError(409, "PRODUCT_UNAVAILABLE", "The product is unavailable.")
        if (
            template is None
            or not template.is_enabled
            or not template.cloud_init_enabled
            or template.os_type not in {item.value for item in TemplateOsType}
        ):
            raise AppError(409, "TEMPLATE_UNAVAILABLE", "The QEMU template is unavailable.")
        if self._request.spec_snapshot.get("os_type") != template.os_type:
            raise AppError(
                409,
                "TEMPLATE_OS_TYPE_CHANGED",
                "The template operating system type changed.",
            )
        spec = self._request.spec_snapshot
        if (
            spec.get("cpu_cores") != product.cpu_cores
            or spec.get("memory_bytes") != product.memory_bytes
            or spec.get("disk_bytes") != product.disk_bytes
        ):
            raise AppError(409, "PRODUCT_SPEC_CHANGED", "The product specification changed.")
        return {"product_id": str(product.id)}

    async def _select_target(self) -> dict[str, object]:
        memory = self._int_spec("memory_bytes")
        disk = self._int_spec("disk_bytes")
        if self._request.spec_snapshot.get("node_capacity_reserved") is True:
            resumed_node = await self._node()
            return {"node": resumed_node.name, "resumed": True}
        if self._request.target_node_id is not None:
            node = await self._session.scalar(
                select(ProvisioningNode)
                .where(ProvisioningNode.id == self._request.target_node_id)
                .with_for_update()
            )
            if node is None:
                raise AppError(409, "NO_ELIGIBLE_NODE", "The selected node is unavailable.")
        else:
            node = await self._session.scalar(
                select(ProvisioningNode)
                .where(
                    ProvisioningNode.cluster_id == self._request.target_cluster_id,
                    ProvisioningNode.is_enabled.is_(True),
                    ProvisioningNode.is_maintenance.is_(False),
                    ProvisioningNode.available_memory_bytes >= memory,
                    ProvisioningNode.available_storage_bytes >= disk,
                )
                .order_by(nullsfirst(ProvisioningNode.last_selected_at), ProvisioningNode.name)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if node is None:
                raise AppError(409, "NO_ELIGIBLE_NODE", "No node has sufficient capacity.")
            self._request.target_node_id = node.id
        if (
            not node.is_enabled
            or node.is_maintenance
            or node.available_memory_bytes < memory
            or node.available_storage_bytes < disk
        ):
            raise AppError(409, "NO_ELIGIBLE_NODE", "The selected node is unavailable.")
        node.available_memory_bytes -= memory
        node.available_storage_bytes -= disk
        node.last_selected_at = datetime.now(UTC)
        self._request.spec_snapshot = {
            **self._request.spec_snapshot,
            "node_capacity_reserved": True,
        }
        await self._session.commit()
        return {"node": node.name}

    async def _reserve_vmid(self, client: ProvisioningApi) -> dict[str, object]:
        lock_key = self._request.target_cluster_id.int % (2**63 - 1)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )
        guests = await client.get_guests()
        remote = {int(item["vmid"]) for item in guests if isinstance(item.get("vmid"), int)}
        local = set(
            await self._session.scalars(
                select(Workload.vmid).where(Workload.cluster_id == self._request.target_cluster_id)
            )
        )
        reserved = set(
            await self._session.scalars(
                select(ProvisioningRequest.target_vmid).where(
                    ProvisioningRequest.target_cluster_id == self._request.target_cluster_id,
                    ProvisioningRequest.id != self._request.id,
                    ProvisioningRequest.target_vmid.is_not(None),
                    ProvisioningRequest.status.in_(
                        [
                            ProvisioningStatus.QUEUED.value,
                            ProvisioningStatus.RUNNING.value,
                            ProvisioningStatus.SUCCEEDED.value,
                            ProvisioningStatus.MANUAL_REVIEW.value,
                        ]
                    ),
                )
            )
        )
        unavailable = remote | local | {int(item) for item in reserved if item is not None}
        vmid = self._request.target_vmid
        if vmid is not None and vmid in unavailable:
            raise AppError(409, "VMID_CONFLICT", "The requested VMID is already in use.")
        if vmid is None:
            vmid = next(
                (candidate for candidate in range(100, 1_000_000) if candidate not in unavailable),
                None,
            )
        if vmid is None:
            raise AppError(409, "VMID_EXHAUSTED", "No VMID is available.")
        self._request.target_vmid = vmid
        await self._session.commit()
        return {"vmid": vmid}

    async def _reserve_ip(self) -> dict[str, object]:
        ipam = self._ipam()
        address, _allocation = await ipam.reserve_for_provisioning(
            self._request.ip_pool_id,
            self._request.id,
            self._request.requested_ip_address,
        )
        self._request.ip_address_id = address.id
        await self._session.commit()
        return {"ip_address": str(address.address)}

    async def _full_clone(
        self, step: ProvisioningStep, client: ProvisioningApi
    ) -> dict[str, object]:
        node = await self._node()
        vmid = self._vmid()
        if await self._vm_exists(client, vmid):
            if self._request.clone_submitted and step.pve_upid is not None:
                return {"already_exists": True, "submission_verified": True}
            raise AppError(
                409,
                "VMID_OWNERSHIP_CONFLICT",
                "The reserved VMID is occupied by a VM not verified as this provisioning request.",
            )
        self._request.clone_submitted = True
        await self._session.commit()
        upid = await client.clone_qemu_template(
            source_node=self._str_spec("source_node"),
            source_vmid=self._int_spec("source_vmid"),
            target_node=node.name,
            target_vmid=vmid,
            name=self._request.target_name,
            storage=self._str_spec("storage"),
        )
        step.pve_upid = upid
        await self._session.commit()
        return {"submitted": True}

    async def _wait_clone(self, client: ProvisioningApi) -> dict[str, object]:
        clone_step = await self._step("FULL_CLONE")
        if clone_step.pve_upid is not None:
            await self._wait_task(client, (await self._node()).name, clone_step.pve_upid)
        elif await self._vm_exists(client, self._vmid()):
            raise AppError(
                409,
                "VMID_OWNERSHIP_CONFLICT",
                "The clone VM exists without a verified Proxmox task identifier.",
            )
        else:
            raise AppError(409, "CLONE_STATE_UNKNOWN", "The clone result could not be determined.")
        return {"clone_complete": True}

    async def _configure_compute(self, client: ProvisioningApi) -> dict[str, object]:
        await client.configure_qemu(
            node=(await self._node()).name,
            vmid=self._vmid(),
            values={
                "cores": str(self._int_spec("cpu_cores")),
                "memory": str(self._int_spec("memory_bytes") // (1024 * 1024)),
            },
        )
        return {"configured": True}

    async def _resize_disk(self, client: ProvisioningApi) -> dict[str, object]:
        await client.resize_qemu_disk(
            node=(await self._node()).name,
            vmid=self._vmid(),
            disk=self._str_spec("source_disk"),
            size_bytes=self._int_spec("disk_bytes"),
        )
        return {"resized": True}

    async def _configure_network(self, client: ProvisioningApi) -> dict[str, object]:
        address = await self._address()
        pool = await self._session.get(IpPool, self._request.ip_pool_id)
        assert pool is not None
        network = ipaddress.ip_network(str(pool.cidr), strict=False)
        ipconfig = f"ip={address.address}/{network.prefixlen}"
        if pool.gateway is not None:
            ipconfig += f",gw={pool.gateway}"
        net0 = f"virtio,bridge={self._str_spec('bridge')}"
        vlan = self._request.spec_snapshot.get("vlan_tag")
        if isinstance(vlan, int):
            net0 += f",tag={vlan}"
        values = {"net0": net0, "ipconfig0": ipconfig}
        if pool.dns_servers:
            values["nameserver"] = " ".join(str(item) for item in pool.dns_servers)
        await client.configure_qemu(
            node=(await self._node()).name, vmid=self._vmid(), values=values
        )
        return {
            "network_configured": True,
            "initializer": (
                "CLOUDBASE_INIT"
                if self._request.spec_snapshot.get("os_type") == TemplateOsType.WINDOWS.value
                else "CLOUD_INIT"
            ),
        }

    async def _configure_identity(self, client: ProvisioningApi) -> dict[str, object]:
        if self._request.spec_snapshot.get("os_type") == TemplateOsType.WINDOWS.value:
            password = self._decrypt_initial_password()
            await client.configure_qemu(
                node=(await self._node()).name,
                vmid=self._vmid(),
                values={
                    "ciuser": self._str_spec("cloud_init_username"),
                    "cipassword": password,
                },
            )
            self._clear_initial_password()
            await self._session.commit()
            return {
                "credential": "ONE_TIME_PASSWORD",
                "initializer": "CLOUDBASE_INIT",
            }

        keys = self._request.spec_snapshot.get("ssh_public_keys")
        if (
            not isinstance(keys, list)
            or not keys
            or not all(isinstance(item, str) for item in keys)
        ):
            raise AppError(409, "SSH_KEYS_INVALID", "The SSH public keys are invalid.")
        await client.configure_qemu(
            node=(await self._node()).name,
            vmid=self._vmid(),
            values={
                "ciuser": self._str_spec("cloud_init_username"),
                "sshkeys": "\n".join(cast(list[str], keys)),
            },
        )
        return {
            "ssh_key_count": len(keys),
            "initializer": (
                "CLOUDBASE_INIT"
                if self._request.spec_snapshot.get("os_type") == TemplateOsType.WINDOWS.value
                else "CLOUD_INIT"
            ),
        }

    async def _start_vm(self, step: ProvisioningStep, client: ProvisioningApi) -> dict[str, object]:
        if self._request.spec_snapshot.get("start_after_create") is not True:
            return {"skipped": True}
        current = await client.get_vm_status(node=(await self._node()).name, vmid=self._vmid())
        if current.get("status") == "running":
            return {"already_running": True}
        upid = await client.submit_vm_power_action(
            node=(await self._node()).name, vmid=self._vmid(), action="start"
        )
        step.pve_upid = upid
        await self._session.commit()
        await self._wait_task(client, (await self._node()).name, upid)
        return {"started": True}

    async def _verify_status(self, client: ProvisioningApi) -> dict[str, object]:
        status = await client.get_vm_status(node=(await self._node()).name, vmid=self._vmid())
        expected_running = self._request.spec_snapshot.get("start_after_create") is True
        if expected_running and status.get("status") != "running":
            raise AppError(
                409, "VM_START_VERIFICATION_FAILED", "The VM did not reach running state."
            )
        return {"power_state": str(status.get("status", "unknown")).upper()}

    async def _assign_organization(self) -> dict[str, object]:
        workload = await self._session.scalar(
            select(Workload).where(
                Workload.cluster_id == self._request.target_cluster_id,
                Workload.vmid == self._vmid(),
            )
        )
        if workload is None:
            workload = Workload(
                id=uuid4(),
                cluster_id=self._request.target_cluster_id,
                vmid=self._vmid(),
                node=(await self._node()).name,
                kind="QEMU",
                name=self._request.target_name,
                power_state="RUNNING"
                if self._request.spec_snapshot.get("start_after_create") is True
                else "STOPPED",
                cpu_cores=self._int_spec("cpu_cores"),
                memory_bytes=self._int_spec("memory_bytes"),
                disk_bytes=self._int_spec("disk_bytes"),
                is_template=False,
                is_present=True,
                organization_id=self._request.organization_id,
                observed_at=datetime.now(UTC),
                version=1,
            )
            self._session.add(workload)
            await self._session.flush()
        elif workload.organization_id not in {None, self._request.organization_id}:
            raise AppError(409, "WORKLOAD_OWNERSHIP_CONFLICT", "The VM is assigned elsewhere.")
        assignment = await self._session.scalar(
            select(WorkloadAssignment).where(
                WorkloadAssignment.workload_id == workload.id,
                WorkloadAssignment.revoked_at.is_(None),
            )
        )
        if assignment is not None and assignment.organization_id != self._request.organization_id:
            raise AppError(409, "WORKLOAD_OWNERSHIP_CONFLICT", "The VM is assigned elsewhere.")
        if assignment is None:
            self._session.add(
                WorkloadAssignment(
                    workload_id=workload.id,
                    organization_id=self._request.organization_id,
                    assigned_by_id=self._actor.id,
                )
            )
        workload.organization_id = self._request.organization_id
        workload.is_present = True
        self._request.workload_id = workload.id
        add_audit_event(
            self._session,
            action="VM_ASSIGNED",
            outcome="SUCCEEDED",
            request_id=None,
            actor_user_id=self._actor.id,
            actor_role=UserRole(self._actor.role),
            organization_id=self._request.organization_id,
            workload_id=workload.id,
            target_type="workload",
            target_id=workload.id,
            after={"organization_id": str(self._request.organization_id)},
        )
        await self._session.commit()
        return {"workload_id": str(workload.id)}

    async def _confirm_ip(self) -> dict[str, object]:
        if self._request.workload_id is None:
            raise AppError(409, "WORKLOAD_MISSING", "The created workload is missing.")
        await self._ipam().confirm_provisioning_ip(self._request.id, self._request.workload_id)
        return {"assigned": True}

    async def _fail_request(
        self,
        request: ProvisioningRequest,
        step: ProvisioningStep | None,
        error: AppError,
    ) -> None:
        if step is not None:
            step.status = ProvisioningStepStatus.FAILED.value
            step.error_code = error.code
            step.error_summary = error.message
            step.finished_at = datetime.now(UTC)
        manual_review = request.clone_submitted or error.code == "VMID_OWNERSHIP_CONFLICT"
        if not manual_review:
            try:
                await self._ipam().quarantine_provisioning_ip(request.id)
                await self._release_node_capacity()
            except Exception:
                manual_review = True
        request.status = (
            ProvisioningStatus.MANUAL_REVIEW.value
            if manual_review
            else ProvisioningStatus.FAILED.value
        )
        request.error_code = error.code
        request.error_summary = error.message
        request.finished_at = datetime.now(UTC)
        request.runner_id = None
        request.lease_expires_at = None
        self._clear_initial_password()
        add_audit_event(
            self._session,
            action="VM_PROVISION",
            outcome="MANUAL_REVIEW" if manual_review else "FAILED",
            request_id=None,
            actor_user_id=request.requested_by_id,
            actor_role=UserRole.SUPER_ADMIN,
            organization_id=request.organization_id,
            workload_id=request.workload_id,
            target_type="provisioning_request",
            target_id=request.id,
            details={"error_code": error.code, "step": step.step_name if step else None},
            error_code=error.code,
        )
        await self._session.commit()

        if manual_review:
            try:
                await self._record_partial_workload()
                await self._session.commit()
            except Exception:
                await self._session.rollback()
                logger.exception(
                    "Failed to record partially provisioned VM",
                    extra={"provisioning_request_id": str(request.id)},
                )

    async def _release_node_capacity(self) -> None:
        if self._request.spec_snapshot.get("node_capacity_reserved") is not True:
            return
        if self._request.target_node_id is None:
            raise RuntimeError("reserved node is missing")
        node = await self._session.scalar(
            select(ProvisioningNode)
            .where(ProvisioningNode.id == self._request.target_node_id)
            .with_for_update()
        )
        if node is None:
            raise RuntimeError("reserved node no longer exists")
        node.available_memory_bytes += self._int_spec("memory_bytes")
        node.available_storage_bytes += self._int_spec("disk_bytes")
        self._request.spec_snapshot = {
            **self._request.spec_snapshot,
            "node_capacity_reserved": False,
        }
        await self._session.commit()

    async def _wait_task(self, client: ProvisioningApi, node: str, upid: str) -> None:
        for _ in range(self._settings.pve_task_max_poll_attempts):
            self._touch_lease(self._request)
            await self._session.commit()
            status = await client.get_task_status(node=node, upid=upid)
            if status.get("status") == "running":
                await self._sleep(self._settings.pve_task_poll_interval_seconds)
                continue
            if status.get("status") == "stopped" and status.get("exitstatus") == "OK":
                return
            raise AppError(502, "PVE_TASK_FAILED", "The Proxmox task failed.")
        raise AppError(504, "PVE_TASK_TIMEOUT", "The Proxmox task timed out.")

    async def _vm_exists(self, client: ProvisioningApi, vmid: int) -> bool:
        return any(item.get("vmid") == vmid for item in await client.get_guests())

    async def _node(self) -> ProvisioningNode:
        if self._request.target_node_id is None:
            raise AppError(409, "TARGET_NODE_MISSING", "The target node is missing.")
        node = await self._session.get(ProvisioningNode, self._request.target_node_id)
        if node is None:
            raise AppError(409, "TARGET_NODE_MISSING", "The target node is missing.")
        return node

    async def _address(self) -> IpAddress:
        if self._request.ip_address_id is None:
            raise AppError(409, "IP_RESERVATION_MISSING", "The IP reservation is missing.")
        address = await self._session.get(IpAddress, self._request.ip_address_id)
        if address is None:
            raise AppError(409, "IP_RESERVATION_MISSING", "The IP reservation is missing.")
        return address

    async def _step(self, name: str) -> ProvisioningStep:
        step = await self._session.scalar(
            select(ProvisioningStep).where(
                ProvisioningStep.provisioning_request_id == self._request.id,
                ProvisioningStep.step_name == name,
            )
        )
        assert step is not None
        return step

    def _ipam(self) -> IpamService:
        return IpamService(
            session=self._session,
            principal=self._principal,
            request_id="worker",
            source_ip="worker",
        )

    def _vmid(self) -> int:
        if self._request.target_vmid is None:
            raise AppError(409, "VMID_RESERVATION_MISSING", "The VMID reservation is missing.")
        return self._request.target_vmid

    def _int_spec(self, key: str) -> int:
        value = self._request.spec_snapshot.get(key)
        if not isinstance(value, int):
            raise AppError(409, "SPEC_SNAPSHOT_INVALID", "The saved specification is invalid.")
        return value

    def _str_spec(self, key: str) -> str:
        value = self._request.spec_snapshot.get(key)
        if not isinstance(value, str):
            raise AppError(409, "SPEC_SNAPSHOT_INVALID", "The saved specification is invalid.")
        return value

    def _decrypt_initial_password(self) -> str:
        request = self._request
        if (
            request.initial_password_ciphertext is None
            or request.initial_password_nonce is None
            or request.initial_password_key_version is None
        ):
            raise AppError(
                409,
                "WINDOWS_INITIAL_PASSWORD_UNAVAILABLE",
                "The Windows initial password is unavailable.",
            )
        try:
            return ProvisioningSecretCipher(
                self._settings.app_secret_key.get_secret_value()
            ).decrypt(
                EncryptedProvisioningSecret(
                    ciphertext=request.initial_password_ciphertext,
                    nonce=request.initial_password_nonce,
                    key_version=request.initial_password_key_version,
                ),
                cluster_id=request.target_cluster_id,
                request_id=request.id,
            )
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise AppError(
                500,
                "WINDOWS_INITIAL_PASSWORD_DECRYPTION_FAILED",
                "The Windows initial password could not be decrypted.",
            ) from exc

    def _clear_initial_password(self) -> None:
        request = self._request
        if (
            request.initial_password_ciphertext is not None
            or request.initial_password_nonce is not None
            or request.initial_password_key_version is not None
        ):
            request.initial_password_ciphertext = None
            request.initial_password_nonce = None
            request.initial_password_key_version = None
            request.initial_password_cleared_at = datetime.now(UTC)

    @asynccontextmanager
    async def _open_client(self, cluster_id: UUID) -> AsyncIterator[ProvisioningApi]:
        if self._injected_client is not None:
            yield self._injected_client
            return
        cluster = await self._session.get(Cluster, cluster_id)
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == cluster_id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if cluster is None or credential is None or not cluster.is_active:
            yield UnavailableProvisioningApi(
                AppError(409, "CLUSTER_UNAVAILABLE", "The cluster is unavailable.")
            )
            return
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
        except (InvalidTag, UnicodeDecodeError):
            yield UnavailableProvisioningApi(
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
                token_secret=secret,
                ca_bundle_pem=cluster.ca_bundle_pem,
                connect_timeout=self._settings.pve_connect_timeout_seconds,
                read_timeout=self._settings.pve_read_timeout_seconds,
                max_connections=self._settings.pve_max_connections,
                max_keepalive_connections=self._settings.pve_max_keepalive_connections,
                allowed_hosts=self._settings.pve_allowed_hosts,
                allowed_networks=self._settings.pve_allowed_networks,
            )
        except AppError as exc:
            yield UnavailableProvisioningApi(exc)
            return
        async with client:
            yield client


async def run_provisioning_request(request_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            runner = ProvisioningRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
            )
            await runner.run(request_id)
    finally:
        await engine.dispose()
