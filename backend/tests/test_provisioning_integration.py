import asyncio
import base64
import os
from datetime import UTC, datetime
from decimal import Decimal
from secrets import token_urlsafe
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app
from app.models.auth import AuditLog, LoginThrottle, Organization, RefreshToken, User, UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.ipam import (
    IpAddress,
    IpAddressState,
    IpAllocation,
    IpAllocationStatus,
    IpPool,
    IpPoolExclusion,
)
from app.models.operation import Operation, PveTask, Workload, WorkloadAssignment
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
from app.schemas.provisioning import CloudInitRequest, ProvisioningRequestCreate
from app.security.access import Principal
from app.security.credentials import CredentialCipher
from app.security.passwords import PasswordManager
from app.services.ipam import IpamService
from app.services.provisioning import ProvisioningService
from app.services.provisioning_runner import ProvisioningRunner
from app.services.workloads import WorkloadService

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


class FakeProvisioningApi:
    def __init__(
        self,
        *,
        fail_clone: bool = False,
        fail_cloud_init: bool = False,
        cancel_compute: bool = False,
    ) -> None:
        self.guests: set[int] = {9000}
        self.pending_clone: int | None = None
        self.running: set[int] = set()
        self.clone_calls = 0
        self.configure_calls: list[dict[str, str]] = []
        self.fail_clone = fail_clone
        self.fail_cloud_init = fail_cloud_init
        self.cancel_compute = cancel_compute

    async def get_guests(self) -> list[dict[str, Any]]:
        return [{"vmid": item} for item in sorted(self.guests)]

    async def clone_qemu_template(self, **values: Any) -> str:
        self.clone_calls += 1
        if self.fail_clone:
            raise AppError(502, "PVE_TASK_FAILED", "The clone operation failed.")
        self.pending_clone = int(values["target_vmid"])
        return "UPID:clone"

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, Any]:
        del node
        if upid == "UPID:clone" and self.pending_clone is not None:
            self.guests.add(self.pending_clone)
        return {"status": "stopped", "exitstatus": "OK"}

    async def configure_qemu(self, *, node: str, vmid: int, values: dict[str, str]) -> None:
        del node, vmid
        if self.cancel_compute and "cores" in values:
            self.cancel_compute = False
            raise asyncio.CancelledError
        if self.fail_cloud_init and "ipconfig0" in values:
            raise AppError(502, "PVE_CONFIG_FAILED", "Cloud-Init network configuration failed.")
        self.configure_calls.append(values)

    async def resize_qemu_disk(self, *, node: str, vmid: int, disk: str, size_bytes: int) -> None:
        del node, vmid, disk, size_bytes

    async def submit_vm_power_action(self, *, node: str, vmid: int, action: str) -> str:
        del node, action
        self.running.add(vmid)
        return "UPID:start"

    async def get_vm_status(self, *, node: str, vmid: int) -> dict[str, Any]:
        del node
        return {"status": "running" if vmid in self.running else "stopped"}


class BlockingCloneApi(FakeProvisioningApi):
    def __init__(self) -> None:
        super().__init__()
        self.clone_started = asyncio.Event()
        self.release_clone = asyncio.Event()

    async def clone_qemu_template(self, **values: Any) -> str:
        self.clone_calls += 1
        self.clone_started.set()
        await self.release_clone.wait()
        self.pending_clone = int(values["target_vmid"])
        return "UPID:clone"


class VmidRaceApi(FakeProvisioningApi):
    def __init__(self, collision_vmid: int) -> None:
        super().__init__()
        self.guest_reads = 0
        self.collision_vmid = collision_vmid

    async def get_guests(self) -> list[dict[str, Any]]:
        self.guest_reads += 1
        guests = await super().get_guests()
        if self.guest_reads >= 2:
            guests.append(
                {
                    "vmid": self.collision_vmid,
                    "name": "unrelated-vm",
                    "node": "pve-target",
                    "type": "qemu",
                }
            )
        return guests


async def _clear(app: Any) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            AuditLog,
            ProvisioningStep,
            IpAllocation,
            ProvisioningRequest,
            IpAddress,
            IpPoolExclusion,
            IpPool,
            PveTask,
            Operation,
            Template,
            Product,
            ProvisioningNode,
            WorkloadAssignment,
            Workload,
            ClusterCredential,
            Cluster,
            Organization,
            RefreshToken,
            LoginThrottle,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


async def test_resumable_template_provisioning_and_failure_boundaries() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        pve_task_poll_interval_seconds=0.01,
        pve_task_max_poll_attempts=3,
    )
    app = create_app(settings)
    await _clear(app)
    ids = {
        name: uuid4()
        for name in (
            "admin",
            "org",
            "cluster",
            "source",
            "windows_source",
            "product",
            "template",
            "windows_template",
            "node",
            "pool",
        )
    }
    ssh_key = "ssh-ed25519 " + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    principal = Principal(ids["admin"], "provision@example.test", UserRole.SUPER_ADMIN, 0)
    async with app.state.db_session_factory() as session:
        admin = User(
            id=ids["admin"],
            email=principal.email,
            display_name="Provision Admin",
            role=UserRole.SUPER_ADMIN.value,
            password_hash=PasswordManager().hash(token_urlsafe(24)),
            is_active=True,
        )
        session.add(admin)
        await session.flush()
        session.add_all(
            [
                Organization(
                    id=ids["org"],
                    name="Provision Org",
                    is_active=True,
                    created_by_id=admin.id,
                    version=1,
                ),
                Cluster(
                    id=ids["cluster"],
                    name="provision-cluster",
                    api_base_url="https://provision.example.test:8006",
                    is_active=True,
                    version=1,
                ),
            ]
        )
        await session.flush()
        source = Workload(
            id=ids["source"],
            cluster_id=ids["cluster"],
            vmid=9000,
            node="pve-source",
            kind="QEMU",
            name="linux-cloud-template",
            power_state="STOPPED",
            is_template=True,
            is_present=True,
            observed_at=datetime.now(UTC),
            version=1,
        )
        windows_source = Workload(
            id=ids["windows_source"],
            cluster_id=ids["cluster"],
            vmid=9001,
            node="pve-source",
            kind="QEMU",
            name="windows-cloudbase-template",
            power_state="STOPPED",
            is_template=True,
            is_present=True,
            observed_at=datetime.now(UTC),
            version=1,
        )
        product = Product(
            id=ids["product"],
            name="standard-linux",
            cpu_cores=2,
            memory_bytes=2_147_483_648,
            disk_bytes=21_474_836_480,
            is_enabled=True,
            created_by_id=admin.id,
        )
        template = Template(
            id=ids["template"],
            name="debian-linux",
            source_workload_id=source.id,
            source_disk="scsi0",
            default_storage="local-lvm",
            default_bridge="vmbr0",
            default_vlan_tag=120,
            cloud_init_enabled=True,
            linux_only=True,
            os_type=TemplateOsType.LINUX.value,
            is_enabled=True,
            created_by_id=admin.id,
        )
        windows_template = Template(
            id=ids["windows_template"],
            name="windows-server-2025",
            source_workload_id=windows_source.id,
            source_disk="scsi0",
            default_storage="local-lvm",
            default_bridge="vmbr0",
            default_vlan_tag=120,
            cloud_init_enabled=True,
            linux_only=False,
            os_type=TemplateOsType.WINDOWS.value,
            is_enabled=True,
            created_by_id=admin.id,
        )
        node = ProvisioningNode(
            id=ids["node"],
            cluster_id=ids["cluster"],
            name="pve-target",
            is_enabled=True,
            is_maintenance=False,
            available_memory_bytes=64 * 1024**3,
            available_storage_bytes=2 * 1024**4,
        )
        pool = IpPool(
            id=ids["pool"],
            name="provision-v4",
            cluster_id=ids["cluster"],
            cidr="192.0.2.0/24",
            gateway="192.0.2.1",
            dns_servers=["192.0.2.53"],
            bridge="vmbr0",
            vlan_tag=120,
            ip_family=4,
            allocation_strategy="SEQUENTIAL",
            quarantine_seconds=600,
            next_offset=Decimal(0),
            is_active=True,
            created_by_id=admin.id,
            version=1,
        )
        session.add_all(
            [source, windows_source, product, template, windows_template, node, pool]
        )
        await session.commit()

    published: list[UUID] = []
    revealed_passwords: dict[str, str | None] = {}

    async def create_request(
        name: str,
        key: str,
        *,
        vmid: int | None = None,
        pool_id: UUID | None = None,
        template_id: UUID | None = None,
        username: str = "clouduser",
        ssh_keys: list[str] | None = None,
    ) -> ProvisioningRequest:
        async with app.state.db_session_factory() as session:
            service = ProvisioningService(
                session=session,
                settings=settings,
                principal=principal,
                publisher=lambda request_id, _task: published.append(request_id),
                request_id=f"request-{name}",
                source_ip="127.0.0.1",
            )
            payload = ProvisioningRequestCreate(
                product_id=ids["product"],
                template_id=template_id or ids["template"],
                organization_id=ids["org"],
                target_cluster_id=ids["cluster"],
                target_vmid=vmid,
                target_name=name,
                ip_pool_id=pool_id or ids["pool"],
                cloud_init=CloudInitRequest(
                    username=username,
                    ssh_public_keys=[ssh_key] if ssh_keys is None else ssh_keys,
                ),
                start_after_create=True,
            )
            response, _created = await service.create_request(payload, key)
            revealed_passwords[name] = response.initial_password
            request = await session.get(ProvisioningRequest, response.id)
            assert request is not None
            return request

    async def run_request(
        request_id: UUID,
        fake: FakeProvisioningApi,
        runner_type: type[ProvisioningRunner] = ProvisioningRunner,
    ) -> None:
        async with app.state.db_session_factory() as session:
            runner = runner_type(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                client=fake,
                sleep=lambda _seconds: asyncio.sleep(0),
            )
            await runner.run(request_id)

    try:
        with pytest.raises(AppError) as invalid_linux_identity:
            await create_request(
                "invalid-linux-user",
                "invalid-linux-user-idempotency",
                username="Administrator",
            )
        assert invalid_linux_identity.value.code == "LINUX_USERNAME_INVALID"
        with pytest.raises(AppError) as missing_linux_key:
            await create_request(
                "missing-linux-key",
                "missing-linux-key-idempotency",
                ssh_keys=[],
            )
        assert missing_linux_key.value.code == "SSH_KEYS_REQUIRED"

        normal = await create_request("normal-vm", "normal-idempotency")
        fake = FakeProvisioningApi()
        await run_request(normal.id, fake)
        await run_request(normal.id, fake)
        async with app.state.db_session_factory() as session:
            completed = await session.get(ProvisioningRequest, normal.id)
            assert completed is not None and completed.status == ProvisioningStatus.SUCCEEDED.value
            assert completed.workload_id is not None and completed.target_vmid is not None
            assert fake.clone_calls == 1
            steps = list(
                await session.scalars(
                    select(ProvisioningStep).where(
                        ProvisioningStep.provisioning_request_id == normal.id
                    )
                )
            )
            assert len(steps) == 15
            assert all(step.status == ProvisioningStepStatus.SUCCEEDED.value for step in steps)
            allocation = await session.scalar(
                select(IpAllocation).where(IpAllocation.provisioning_request_id == normal.id)
            )
            assert allocation is not None and allocation.status == IpAllocationStatus.ASSIGNED.value
            assert allocation.workload_id == completed.workload_id
            assert "password" not in str(completed.spec_snapshot).lower()
            workloads = await WorkloadService(
                session=session,
                principal=principal,
                request_id="workload-ip-listing",
            ).list_workloads(organization_id=None, cluster_id=None)
            listed = next(item for item in workloads if item.id == completed.workload_id)
            assert listed.assigned_ip_addresses == ["192.0.2.2"]

        windows = await create_request(
            "windows-vm",
            "windows-idempotency",
            template_id=ids["windows_template"],
            username="Administrator",
            ssh_keys=[],
        )
        async with app.state.db_session_factory() as session:
            service = ProvisioningService(
                session=session,
                settings=settings,
                principal=principal,
                publisher=lambda request_id, _task: published.append(request_id),
                request_id="windows-idempotency-replay",
                source_ip="127.0.0.1",
            )
            replay, created = await service.create_request(
                ProvisioningRequestCreate(
                    product_id=ids["product"],
                    template_id=ids["windows_template"],
                    organization_id=ids["org"],
                    target_cluster_id=ids["cluster"],
                    target_name="windows-vm",
                    ip_pool_id=ids["pool"],
                    cloud_init=CloudInitRequest(
                        username="Administrator",
                        ssh_public_keys=[],
                    ),
                    start_after_create=True,
                ),
                "windows-idempotency",
            )
            assert created is False
            assert replay.id == windows.id
            assert replay.initial_password is None
        windows_fake = FakeProvisioningApi()
        windows_fake.guests.add(9001)
        await run_request(windows.id, windows_fake)
        async with app.state.db_session_factory() as session:
            completed_windows = await session.get(ProvisioningRequest, windows.id)
            assert completed_windows is not None
            assert completed_windows.status == ProvisioningStatus.SUCCEEDED.value
            assert completed_windows.spec_snapshot["os_type"] == TemplateOsType.WINDOWS.value
            initial_password = revealed_passwords["windows-vm"]
            assert initial_password is not None and len(initial_password) == 24
            assert {
                "ciuser": "Administrator",
                "cipassword": initial_password,
            } in windows_fake.configure_calls
            assert completed_windows.initial_password_ciphertext is None
            assert completed_windows.initial_password_nonce is None
            assert completed_windows.initial_password_key_version is None
            assert completed_windows.initial_password_cleared_at is not None
            assert "password" not in str(completed_windows.spec_snapshot).lower()

        windows_failed = await create_request(
            "windows-fail-vm",
            "windows-fail-idempotency",
            template_id=ids["windows_template"],
            username="Administrator",
            ssh_keys=[],
        )
        windows_fail_fake = FakeProvisioningApi(fail_clone=True)
        windows_fail_fake.guests.add(9001)
        await run_request(windows_failed.id, windows_fail_fake)
        async with app.state.db_session_factory() as session:
            failed_windows = await session.get(ProvisioningRequest, windows_failed.id)
            assert failed_windows is not None
            assert failed_windows.status == ProvisioningStatus.MANUAL_REVIEW.value
            assert failed_windows.initial_password_ciphertext is None
            assert failed_windows.initial_password_nonce is None
            assert failed_windows.initial_password_key_version is None
            assert failed_windows.initial_password_cleared_at is not None

        async with app.state.db_session_factory() as session:
            service = ProvisioningService(
                session=session,
                settings=settings,
                principal=principal,
                publisher=lambda request_id, _task: published.append(request_id),
                request_id="duplicate",
                source_ip="127.0.0.1",
            )
            same_payload = ProvisioningRequestCreate(
                product_id=ids["product"],
                template_id=ids["template"],
                organization_id=ids["org"],
                target_cluster_id=ids["cluster"],
                target_name="normal-vm",
                ip_pool_id=ids["pool"],
                cloud_init=CloudInitRequest(username="clouduser", ssh_public_keys=[ssh_key]),
            )
            duplicate, created = await service.create_request(same_payload, "normal-idempotency")
            assert not created and duplicate.id == normal.id

        concurrent = await create_request("concurrent-vm", "concurrent-idempotency")
        concurrent_fake = BlockingCloneApi()
        first_runner = asyncio.create_task(run_request(concurrent.id, concurrent_fake))
        await asyncio.wait_for(concurrent_fake.clone_started.wait(), timeout=2)
        await asyncio.wait_for(run_request(concurrent.id, concurrent_fake), timeout=2)
        concurrent_fake.release_clone.set()
        await asyncio.wait_for(first_runner, timeout=5)
        async with app.state.db_session_factory() as session:
            concurrent_result = await session.get(ProvisioningRequest, concurrent.id)
            assert concurrent_result is not None
            assert concurrent_result.status == ProvisioningStatus.SUCCEEDED.value
            assert concurrent_fake.clone_calls == 1
            allocations = list(
                await session.scalars(
                    select(IpAllocation).where(
                        IpAllocation.provisioning_request_id == concurrent.id
                    )
                )
            )
            assert len(allocations) == 1

        ip_race = await create_request("ip-race-vm", "ip-race-idempotency")

        async def reserve_same_request() -> UUID:
            async with app.state.db_session_factory() as session:
                ipam = IpamService(
                    session=session,
                    principal=principal,
                    request_id="ip-race",
                    source_ip="127.0.0.1",
                )
                _address, allocation = await ipam.reserve_for_provisioning(
                    ids["pool"], ip_race.id, None
                )
                return allocation.id

        allocation_ids = await asyncio.gather(
            reserve_same_request(),
            reserve_same_request(),
        )
        assert allocation_ids[0] == allocation_ids[1]
        async with app.state.db_session_factory() as session:
            allocation_count = len(
                list(
                    await session.scalars(
                        select(IpAllocation).where(
                            IpAllocation.provisioning_request_id == ip_race.id
                        )
                    )
                )
            )
            assert allocation_count == 1

        collision_vmid = 55_555
        vmid_race = await create_request(
            "vmid-race-vm", "vmid-race-idempotency", vmid=collision_vmid
        )
        vmid_race_fake = VmidRaceApi(collision_vmid)
        await run_request(vmid_race.id, vmid_race_fake)
        async with app.state.db_session_factory() as session:
            raced = await session.get(ProvisioningRequest, vmid_race.id)
            assert raced is not None
            assert raced.status == ProvisioningStatus.MANUAL_REVIEW.value
            assert raced.error_code == "VMID_OWNERSHIP_CONFLICT"
            assert raced.workload_id is None
            assert vmid_race_fake.clone_calls == 0
            assert vmid_race_fake.configure_calls == []
            assert vmid_race_fake.running == set()

        restarted = await create_request("restart-vm", "restart-idempotency")
        restart_fake = FakeProvisioningApi(cancel_compute=True)
        with pytest.raises(asyncio.CancelledError):
            await run_request(restarted.id, restart_fake)
        await run_request(restarted.id, restart_fake)
        async with app.state.db_session_factory() as session:
            resumed = await session.get(ProvisioningRequest, restarted.id)
            assert resumed is not None and resumed.status == ProvisioningStatus.SUCCEEDED.value
            assert restart_fake.clone_calls == 1

        conflict = await create_request("conflict-vm", "conflict-idempotency", vmid=9000)
        await run_request(conflict.id, FakeProvisioningApi())
        async with app.state.db_session_factory() as session:
            failed = await session.get(ProvisioningRequest, conflict.id)
            assert failed is not None and failed.error_code == "VMID_CONFLICT"
            assert failed.status == ProvisioningStatus.FAILED.value

        exhausted_pool_id = uuid4()
        async with app.state.db_session_factory() as session:
            session.add(
                IpPool(
                    id=exhausted_pool_id,
                    name="exhausted-provision",
                    cluster_id=ids["cluster"],
                    cidr="198.51.100.0/30",
                    gateway="198.51.100.1",
                    dns_servers=[],
                    bridge="vmbr0",
                    vlan_tag=None,
                    ip_family=4,
                    allocation_strategy="SEQUENTIAL",
                    quarantine_seconds=600,
                    next_offset=Decimal(0),
                    is_active=True,
                    created_by_id=ids["admin"],
                    version=1,
                )
            )
            await session.flush()
            session.add(
                IpAddress(
                    id=uuid4(),
                    pool_id=exhausted_pool_id,
                    address="198.51.100.2",
                    state=IpAddressState.DISABLED.value,
                    reserved_for="reserved",
                    version=1,
                )
            )
            await session.commit()
        exhausted = await create_request(
            "exhausted-vm", "exhausted-idempotency", pool_id=exhausted_pool_id
        )
        await run_request(exhausted.id, FakeProvisioningApi())
        async with app.state.db_session_factory() as session:
            item = await session.get(ProvisioningRequest, exhausted.id)
            assert item is not None and item.error_code == "IP_POOL_EXHAUSTED"

        clone_failed = await create_request("clone-fail-vm", "clone-fail-idempotency")
        await run_request(clone_failed.id, FakeProvisioningApi(fail_clone=True))
        async with app.state.db_session_factory() as session:
            item = await session.get(ProvisioningRequest, clone_failed.id)
            assert item is not None and item.status == ProvisioningStatus.MANUAL_REVIEW.value

        cloud_failed = await create_request("cloud-fail-vm", "cloud-fail-idempotency")
        await run_request(cloud_failed.id, FakeProvisioningApi(fail_cloud_init=True))
        async with app.state.db_session_factory() as session:
            item = await session.get(ProvisioningRequest, cloud_failed.id)
            assert item is not None and item.status == ProvisioningStatus.MANUAL_REVIEW.value
            assert item.workload_id is not None
            partial_workload = await session.get(Workload, item.workload_id)
            assert partial_workload is not None
            assert partial_workload.organization_id is None
            assert partial_workload.is_present is True
            assert partial_workload.power_state == "UNKNOWN"

        class RollbackFailureRunner(ProvisioningRunner):
            def _ipam(self) -> Any:
                class BrokenRollback:
                    async def quarantine_provisioning_ip(self, _request_id: UUID) -> None:
                        raise RuntimeError("rollback unavailable")

                return BrokenRollback()

        rollback_failed = await create_request(
            "rollback-fail-vm", "rollback-fail-idempotency", vmid=9000
        )
        await run_request(rollback_failed.id, FakeProvisioningApi(), RollbackFailureRunner)
        async with app.state.db_session_factory() as session:
            item = await session.get(ProvisioningRequest, rollback_failed.id)
            assert item is not None and item.status == ProvisioningStatus.MANUAL_REVIEW.value
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
