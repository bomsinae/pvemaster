import os
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.main import create_app
from app.models.auth import (
    AuditLog,
    LoginThrottle,
    Organization,
    OrganizationMember,
    RefreshToken,
    User,
    UserRole,
)
from app.models.cluster import Cluster, ClusterCredential
from app.models.ipam import (
    IpAddress,
    IpAddressState,
    IpAllocation,
    IpAllocationKind,
    IpAllocationStatus,
    IpPool,
)
from app.models.metrics import WorkloadMetric
from app.models.operation import Operation, PveTask, Workload, WorkloadAssignment
from app.security.credentials import CredentialCipher
from app.security.passwords import PasswordManager
from app.services.workload_metrics import WorkloadMetricService

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear(app: FastAPI) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            AuditLog,
            PveTask,
            Operation,
            WorkloadMetric,
            WorkloadAssignment,
            IpAllocation,
            Workload,
            IpAddress,
            IpPool,
            ClusterCredential,
            Cluster,
            OrganizationMember,
            RefreshToken,
            Organization,
            LoginThrottle,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


async def _login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def test_customer_portal_prevents_cross_organization_idor() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    published: list[object] = []
    app.state.operation_publisher = lambda operation_id, _task_id: published.append(operation_id)
    passwords = {name: token_urlsafe(24) for name in ("a", "b", "removed", "inactive", "admin")}
    await _clear(app)
    password_manager = PasswordManager()
    async with app.state.db_session_factory() as session:
        admin = User(
            id=uuid4(),
            email="portal-admin@example.test",
            display_name="Admin",
            role=UserRole.SUPER_ADMIN.value,
            password_hash=password_manager.hash(passwords["admin"]),
            is_active=True,
        )
        customer_a = User(
            id=uuid4(),
            email="customer-a@example.test",
            display_name="Customer A",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(passwords["a"]),
            is_active=True,
        )
        customer_b = User(
            id=uuid4(),
            email="customer-b@example.test",
            display_name="Customer B",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(passwords["b"]),
            is_active=True,
        )
        removed = User(
            id=uuid4(),
            email="removed@example.test",
            display_name="Removed",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(passwords["removed"]),
            is_active=True,
        )
        inactive = User(
            id=uuid4(),
            email="inactive-portal@example.test",
            display_name="Inactive",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(passwords["inactive"]),
            is_active=False,
        )
        organization_a = Organization(
            id=uuid4(), name="Organization A", is_active=True, created_by_id=admin.id, version=1
        )
        organization_b = Organization(
            id=uuid4(), name="Organization B", is_active=True, created_by_id=admin.id, version=1
        )
        organization_c = Organization(
            id=uuid4(), name="Organization C", is_active=True, created_by_id=admin.id, version=1
        )
        cluster = Cluster(
            id=uuid4(),
            name="customer-portal-cluster",
            api_base_url="https://portal-pve.example.test:8006",
            is_active=True,
            version=1,
        )
        vm_a = Workload(
            id=uuid4(),
            cluster_id=cluster.id,
            vmid=201,
            node="private-node-a",
            kind="QEMU",
            name="Customer A VM",
            power_state="STOPPED",
            cpu_cores=4,
            memory_bytes=8_589_934_592,
            disk_bytes=107_374_182_400,
            uptime_seconds=86_400,
            is_template=False,
            is_present=True,
            organization_id=organization_a.id,
            observed_at=datetime.now(UTC),
            version=1,
        )
        vm_b = Workload(
            id=uuid4(),
            cluster_id=cluster.id,
            vmid=202,
            node="private-node-b",
            kind="QEMU",
            name="Customer B VM",
            power_state="RUNNING",
            is_template=False,
            is_present=True,
            organization_id=organization_b.id,
            observed_at=datetime.now(UTC),
            version=1,
        )
        vm_c = Workload(
            id=uuid4(),
            cluster_id=cluster.id,
            vmid=203,
            node="private-node-c",
            kind="QEMU",
            name="Customer C VM",
            power_state="STOPPED",
            is_template=False,
            is_present=True,
            organization_id=organization_c.id,
            observed_at=datetime.now(UTC),
            version=1,
        )
        ip_pool = IpPool(
            id=uuid4(),
            name="customer-portal-pool",
            cluster_id=cluster.id,
            cidr="192.0.2.0/24",
            gateway="192.0.2.1",
            dns_servers=["192.0.2.53"],
            bridge="vmbr0",
            vlan_tag=None,
            ip_family=4,
            allocation_strategy="SEQUENTIAL",
            quarantine_seconds=600,
            is_active=True,
            created_by_id=admin.id,
            version=1,
        )
        ip_address = IpAddress(
            id=uuid4(),
            pool_id=ip_pool.id,
            address="192.0.2.24",
            state=IpAddressState.ASSIGNED.value,
            reserved_for=None,
            quarantined_until=None,
            version=1,
        )
        ip_allocation = IpAllocation(
            id=uuid4(),
            ip_address_id=ip_address.id,
            workload_id=vm_a.id,
            provisioning_request_id=None,
            kind=IpAllocationKind.MANUAL.value,
            status=IpAllocationStatus.ASSIGNED.value,
            allocated_by_id=admin.id,
            released_at=None,
            release_reason=None,
            version=1,
        )
        removed_membership = OrganizationMember(
            organization_id=organization_a.id,
            user_id=removed.id,
            added_by_id=admin.id,
        )
        session.add_all([admin, customer_a, customer_b, removed, inactive])
        await session.flush()
        session.add_all([organization_a, organization_b, organization_c, cluster])
        await session.flush()
        session.add_all(
            [
                vm_a,
                vm_b,
                vm_c,
                ip_pool,
                removed_membership,
                OrganizationMember(
                    organization_id=organization_a.id,
                    user_id=customer_a.id,
                    added_by_id=admin.id,
                ),
                OrganizationMember(
                    organization_id=organization_b.id,
                    user_id=customer_b.id,
                    added_by_id=admin.id,
                ),
                OrganizationMember(
                    organization_id=organization_c.id,
                    user_id=customer_a.id,
                    added_by_id=admin.id,
                ),
            ]
        )
        await session.flush()
        assignment_started_at = datetime.now(UTC) - timedelta(minutes=30)
        session.add_all(
            [
                WorkloadAssignment(
                    workload_id=vm_a.id,
                    organization_id=organization_a.id,
                    assigned_by_id=admin.id,
                    assigned_at=assignment_started_at,
                ),
                WorkloadAssignment(
                    workload_id=vm_b.id,
                    organization_id=organization_b.id,
                    assigned_by_id=admin.id,
                    assigned_at=assignment_started_at,
                ),
                WorkloadAssignment(
                    workload_id=vm_c.id,
                    organization_id=organization_c.id,
                    assigned_by_id=admin.id,
                    assigned_at=assignment_started_at,
                ),
                WorkloadMetric(
                    workload_id=vm_a.id,
                    organization_id=organization_a.id,
                    resolution_seconds=60,
                    bucket_at=datetime.now(UTC) - timedelta(minutes=10),
                    sample_count=1,
                    cpu_avg=0.25,
                    cpu_max=0.4,
                    memory_used_avg=4_294_967_296,
                    memory_used_max=5_368_709_120,
                ),
                WorkloadMetric(
                    workload_id=vm_a.id,
                    organization_id=organization_a.id,
                    resolution_seconds=60,
                    bucket_at=datetime.now(UTC) - timedelta(hours=1),
                    sample_count=1,
                    cpu_avg=0.95,
                    cpu_max=0.99,
                ),
                WorkloadMetric(
                    workload_id=vm_a.id,
                    organization_id=organization_b.id,
                    resolution_seconds=60,
                    bucket_at=datetime.now(UTC) - timedelta(minutes=5),
                    sample_count=1,
                    cpu_avg=0.75,
                    cpu_max=0.8,
                ),
            ]
        )
        session.add(ip_address)
        await session.flush()
        session.add(ip_allocation)
        await session.flush()
        await session.delete(removed_membership)
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            token_a = await _login(client, customer_a.email, passwords["a"])
            token_b = await _login(client, customer_b.email, passwords["b"])
            token_removed = await _login(client, removed.email, passwords["removed"])
            headers_a = {"Authorization": f"Bearer {token_a}"}
            headers_b = {"Authorization": f"Bearer {token_b}"}
            headers_removed = {"Authorization": f"Bearer {token_removed}"}

            listing = await client.get("/api/v1/customer/vms", headers=headers_a)
            assert listing.status_code == 200
            assert [item["id"] for item in listing.json()["items"]] == [
                str(vm_a.id),
                str(vm_c.id),
            ]
            assert [item["organization_name"] for item in listing.json()["items"]] == [
                "Organization A",
                "Organization C",
            ]
            assert listing.json()["items"][0]["assigned_ip_addresses"] == ["192.0.2.24"]
            assert "organization_id" not in listing.text
            assert "cluster_id" not in listing.text
            assert "node" not in listing.text
            assert "token" not in listing.text.lower()

            own = await client.get(f"/api/v1/customer/vms/{vm_a.id}", headers=headers_a)
            foreign = await client.get(f"/api/v1/customer/vms/{vm_b.id}", headers=headers_a)
            missing = await client.get(f"/api/v1/customer/vms/{uuid4()}", headers=headers_a)
            assert own.status_code == 200
            assert own.json()["cpu_cores"] == 4
            assert own.json()["organization_name"] == "Organization A"
            assert own.json()["memory_bytes"] == 8_589_934_592
            assert own.json()["disk_bytes"] == 107_374_182_400
            assert own.json()["uptime_seconds"] == 86_400
            assert own.json()["assigned_ip_addresses"] == ["192.0.2.24"]
            assert "cluster_id" not in own.text
            assert "node" not in own.text
            assert foreign.status_code == missing.status_code == 404

            metrics = await client.get(
                f"/api/v1/customer/vms/{vm_a.id}/metrics?range=day",
                headers=headers_a,
            )
            foreign_metrics = await client.get(
                f"/api/v1/customer/vms/{vm_b.id}/metrics?range=day",
                headers=headers_a,
            )
            assert metrics.status_code == 200
            assert [item["cpu_avg"] for item in metrics.json()["items"]] == [0.25]
            assert metrics.json()["assignment_started_at"] is not None
            assert "organization_id" not in metrics.text
            assert "cluster" not in metrics.text
            assert "node" not in metrics.text
            assert foreign_metrics.status_code == 404
            assert foreign.json()["error"]["code"] == missing.json()["error"]["code"]

            own_console = await client.post(
                f"/api/v1/customer/vms/{vm_a.id}/console-sessions", headers=headers_a
            )
            foreign_console = await client.post(
                f"/api/v1/customer/vms/{vm_b.id}/console-sessions", headers=headers_a
            )
            admin_console_as_customer = await client.post(
                f"/api/v1/admin/workloads/{vm_a.id}/console-sessions", headers=headers_a
            )
            assert own_console.status_code == 409
            assert own_console.json()["error"]["code"] == "CONSOLE_REQUIRES_RUNNING_GUEST"
            assert foreign_console.status_code == 404
            assert admin_console_as_customer.status_code == 403

            direct_url_change = await client.get(
                f"/api/v1/customer/vms/{vm_a.id}", headers=headers_b
            )
            assert direct_url_change.status_code == 404

            foreign_action = await client.post(
                f"/api/v1/customer/vms/{vm_b.id}/actions/start",
                headers={**headers_a, "Idempotency-Key": token_urlsafe(18)},
                json={},
            )
            assert foreign_action.status_code == 404

            removed_listing = await client.get("/api/v1/customer/vms", headers=headers_removed)
            removed_detail = await client.get(
                f"/api/v1/customer/vms/{vm_a.id}", headers=headers_removed
            )
            assert removed_listing.json()["items"] == []
            assert removed_detail.status_code == 404
            removed_console = await client.post(
                f"/api/v1/customer/vms/{vm_a.id}/console-sessions",
                headers=headers_removed,
            )
            assert removed_console.status_code == 404

            inactive_login = await client.post(
                "/api/v1/auth/login",
                json={"email": inactive.email, "password": passwords["inactive"]},
            )
            assert inactive_login.status_code == 401

            key = token_urlsafe(18)
            accepted = await client.post(
                f"/api/v1/customer/vms/{vm_a.id}/actions/start",
                headers={**headers_a, "Idempotency-Key": key},
                json={},
            )
            duplicate = await client.post(
                f"/api/v1/customer/vms/{vm_a.id}/actions/start",
                headers={**headers_a, "Idempotency-Key": key},
                json={},
            )
            assert accepted.status_code == duplicate.status_code == 202
            assert accepted.json()["id"] == duplicate.json()["id"]
            assert len(published) == 1
            assert "pve_upid" not in accepted.text
            assert "organization_id" not in accepted.text

            own_job = await client.get(
                f"/api/v1/customer/jobs/{accepted.json()['id']}", headers=headers_a
            )
            foreign_job = await client.get(
                f"/api/v1/customer/jobs/{accepted.json()['id']}", headers=headers_b
            )
            assert own_job.status_code == 200
            assert foreign_job.status_code == 404
            own_jobs = await client.get("/api/v1/customer/jobs", headers=headers_a)
            assert own_jobs.status_code == 200
            assert own_jobs.json()["items"][0]["id"] == accepted.json()["id"]
            assert own_jobs.json()["total"] == 1
            assert own_jobs.json()["limit"] == 50
            assert own_jobs.json()["offset"] == 0
            assert "cluster_id" not in own_jobs.text
            assert "node" not in own_jobs.text
            filtered_jobs = await client.get(
                f"/api/v1/customer/jobs?vm_id={vm_a.id}&status=QUEUED&limit=1&offset=0",
                headers=headers_a,
            )
            assert filtered_jobs.status_code == 200
            assert filtered_jobs.json()["total"] == 1
            too_old = (datetime.now(UTC) - timedelta(days=366)).isoformat()
            oversized_history = await client.get(
                f"/api/v1/customer/jobs?started_at={too_old}",
                headers=headers_a,
            )
            assert oversized_history.status_code == 422
            timezone_missing = await client.get(
                "/api/v1/customer/jobs?started_at=2026-07-26T12:00:00",
                headers=headers_a,
            )
            assert timezone_missing.status_code == 422
            assert timezone_missing.json()["error"]["code"] == "INVALID_TIME_RANGE"

            updated_detail = await client.get(f"/api/v1/customer/vms/{vm_a.id}", headers=headers_a)
            assert updated_detail.json()["recent_jobs"][0]["id"] == accepted.json()["id"]

            foreign_stop = await client.post(
                f"/api/v1/customer/vms/{vm_b.id}/actions/stop",
                headers={**headers_a, "Idempotency-Key": token_urlsafe(18)},
                json={"confirm_forced": True},
            )
            assert foreign_stop.status_code == 404

            unconfirmed_stop = await client.post(
                f"/api/v1/customer/vms/{vm_b.id}/actions/stop",
                headers={**headers_b, "Idempotency-Key": token_urlsafe(18)},
                json={},
            )
            assert unconfirmed_stop.status_code == 422
            assert unconfirmed_stop.json()["error"]["code"] == "FORCED_ACTION_CONFIRMATION_REQUIRED"

            accepted_stop = await client.post(
                f"/api/v1/customer/vms/{vm_b.id}/actions/stop",
                headers={**headers_b, "Idempotency-Key": token_urlsafe(18)},
                json={"confirm_forced": True, "reason": "Guest OS is unresponsive"},
            )
            assert accepted_stop.status_code == 202
            assert accepted_stop.json()["action"] == "stop"
            assert accepted_stop.json()["action_mode"] == "FORCED"
            assert len(published) == 2

            async with app.state.db_session_factory() as session:
                created_rollups = await WorkloadMetricService(
                    session=session,
                    settings=settings,
                    cipher=CredentialCipher(
                        settings.app_secret_key.get_secret_value()
                    ),
                ).downsample_and_retain(now=datetime.now(UTC))
                assert created_rollups >= 4
                resolutions = set(
                    await session.scalars(
                        select(WorkloadMetric.resolution_seconds).where(
                            WorkloadMetric.workload_id == vm_a.id,
                            WorkloadMetric.organization_id == organization_a.id,
                        )
                    )
                )
                assert resolutions == {60, 300, 3600}

                reassigned = await session.get(Workload, vm_a.id, with_for_update=True)
                assert reassigned is not None
                reassigned.organization_id = organization_b.id
                reassigned.version += 1
                await session.commit()

            past_owner_job = await client.get(
                f"/api/v1/customer/jobs/{accepted.json()['id']}",
                headers=headers_a,
            )
            jobs_after_reassignment = await client.get(
                "/api/v1/customer/jobs",
                headers=headers_a,
            )
            assert past_owner_job.status_code == 404
            assert accepted.json()["id"] not in {
                item["id"] for item in jobs_after_reassignment.json()["items"]
            }
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
