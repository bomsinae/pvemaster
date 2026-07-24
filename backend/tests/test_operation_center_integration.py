import os
from datetime import UTC, datetime
from decimal import Decimal
from secrets import token_bytes, token_urlsafe
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, text

from app.core.config import Settings
from app.main import create_app
from app.models.auth import (
    AuditLog,
    LoginThrottle,
    Organization,
    RefreshToken,
    User,
    UserRole,
)
from app.models.cluster import Cluster
from app.models.ipam import IpPool
from app.models.operation import (
    Operation,
    OperationAssignment,
    OperationEvent,
    OperationStatus,
    PveTask,
    Workload,
)
from app.models.provisioning import (
    Product,
    ProvisioningRequest,
    ProvisioningStatus,
    ProvisioningStep,
    Template,
)
from app.models.scheduling import OperationOutbox
from app.security.passwords import PasswordManager

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear(app: FastAPI) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            OperationAssignment,
            OperationEvent,
            OperationOutbox,
            AuditLog,
            PveTask,
            Operation,
            ProvisioningStep,
            ProvisioningRequest,
            Template,
            Product,
            IpPool,
            Workload,
            Cluster,
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


async def test_operation_center_cancel_retry_assignment_and_version_conflict() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    published: list[object] = []
    provisioning_published: list[object] = []
    app.state.operation_publisher = lambda operation_id, _task_id: published.append(operation_id)
    app.state.provisioning_publisher = lambda request_id, _task_id: provisioning_published.append(
        request_id
    )
    password = token_urlsafe(24)
    customer_password = token_urlsafe(24)
    await _clear(app)
    password_manager = PasswordManager()
    async with app.state.db_session_factory() as session:
        admin = User(
            id=uuid4(),
            email="operation-center-admin@example.test",
            display_name="Operation Admin",
            role=UserRole.SUPER_ADMIN.value,
            password_hash=password_manager.hash(password),
            is_active=True,
        )
        customer = User(
            id=uuid4(),
            email="operation-center-customer@example.test",
            display_name="Customer",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(customer_password),
            is_active=True,
        )
        organization = Organization(
            id=uuid4(),
            name="Operation Organization",
            is_active=True,
            created_by_id=admin.id,
            version=1,
        )
        cluster = Cluster(
            id=uuid4(),
            name="operation-cluster",
            api_base_url="https://operation-pve.example.test:8006",
            is_active=True,
            version=1,
        )
        workload = Workload(
            id=uuid4(),
            cluster_id=cluster.id,
            vmid=410,
            node="pve-operation",
            kind="QEMU",
            name="operation-vm",
            power_state="STOPPED",
            is_template=False,
            is_present=True,
            organization_id=organization.id,
            observed_at=datetime.now(UTC),
            version=1,
        )
        session.add_all([admin, customer])
        await session.flush()
        session.add_all([organization, cluster])
        await session.flush()
        session.add(workload)
        await session.flush()
        product = Product(
            id=uuid4(),
            name="operation-product",
            cpu_cores=2,
            memory_bytes=2_147_483_648,
            disk_bytes=21_474_836_480,
            is_enabled=True,
            created_by_id=admin.id,
        )
        template = Template(
            id=uuid4(),
            name="operation-template",
            source_workload_id=workload.id,
            source_disk="scsi0",
            default_storage="local-lvm",
            default_bridge="vmbr0",
            cloud_init_enabled=True,
            linux_only=True,
            is_enabled=True,
            created_by_id=admin.id,
        )
        pool = IpPool(
            id=uuid4(),
            name="operation-pool",
            cluster_id=cluster.id,
            cidr="192.0.2.0/24",
            gateway="192.0.2.1",
            dns_servers=["192.0.2.53"],
            bridge="vmbr0",
            ip_family=4,
            allocation_strategy="SEQUENTIAL",
            quarantine_seconds=600,
            next_offset=Decimal(0),
            is_active=True,
            created_by_id=admin.id,
            version=1,
        )
        session.add_all([product, template, pool])
        await session.flush()
        queued = Operation(
            id=uuid4(),
            operation_type="POWER_START",
            action="start",
            status=OperationStatus.QUEUED.value,
            requested_by_id=admin.id,
            source_ip="127.0.0.1",
            organization_id=organization.id,
            cluster_id=cluster.id,
            workload_id=workload.id,
            idempotency_key_hash=token_bytes(32),
            request_fingerprint=token_bytes(32),
            celery_task_id=str(uuid4()),
            result={"action_mode": "STANDARD"},
            queued_at=datetime.now(UTC),
            version=1,
        )
        session.add(queued)
        queued_provisioning = ProvisioningRequest(
            id=uuid4(),
            requested_by_id=admin.id,
            idempotency_key_hash=token_bytes(32),
            request_fingerprint=token_bytes(32),
            product_id=product.id,
            template_id=template.id,
            organization_id=organization.id,
            target_cluster_id=cluster.id,
            target_name="queued-provision",
            ip_pool_id=pool.id,
            status=ProvisioningStatus.QUEUED.value,
            current_step="VALIDATE_REQUEST",
            spec_snapshot={},
            celery_task_id=str(uuid4()),
            clone_submitted=False,
            version=1,
        )
        failed_provisioning = ProvisioningRequest(
            id=uuid4(),
            requested_by_id=admin.id,
            idempotency_key_hash=token_bytes(32),
            request_fingerprint=token_bytes(32),
            product_id=product.id,
            template_id=template.id,
            organization_id=organization.id,
            target_cluster_id=cluster.id,
            target_name="failed-provision",
            ip_pool_id=pool.id,
            status=ProvisioningStatus.FAILED.value,
            current_step="VALIDATE_REQUEST",
            spec_snapshot={},
            celery_task_id=str(uuid4()),
            clone_submitted=False,
            error_code="CLUSTER_UNREACHABLE",
            error_summary="Cluster temporarily unavailable.",
            finished_at=datetime.now(UTC),
            version=1,
        )
        session.add_all([queued_provisioning, failed_provisioning])
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin_token = await _login(client, admin.email, password)
            customer_token = await _login(client, customer.email, customer_password)
            headers = {"Authorization": f"Bearer {admin_token}"}

            denied = await client.get(
                "/api/v1/admin/operations",
                headers={"Authorization": f"Bearer {customer_token}"},
            )
            assert denied.status_code == 403

            listing = await client.get(
                "/api/v1/admin/operations?status=QUEUED&operation_type=POWER_START",
                headers=headers,
            )
            assert listing.status_code == 200, listing.text
            assert listing.json()["total"] == 1
            assert listing.json()["items"][0]["available_actions"] == [
                "CANCEL",
                "ASSIGN",
                "ACKNOWLEDGE",
            ]

            detail = await client.get(
                f"/api/v1/admin/operations/{queued.id}",
                headers=headers,
            )
            assert detail.status_code == 200
            assert detail.json()["events"][0]["event_type"] == "CREATED"
            assert "UPID:" not in detail.text

            cancelled = await client.post(
                f"/api/v1/admin/operations/{queued.id}/cancel",
                headers=headers,
                json={"version": 1},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "CANCELLED"
            unsafe_cancel = await client.post(
                f"/api/v1/admin/operations/{queued.id}/cancel",
                headers=headers,
                json={"version": 2},
            )
            assert unsafe_cancel.status_code == 409
            assert unsafe_cancel.json()["error"]["code"] == "OPERATION_CANCEL_UNSAFE"

            async with app.state.db_session_factory() as session:
                failed = Operation(
                    id=uuid4(),
                    operation_type="POWER_START",
                    action="start",
                    status=OperationStatus.FAILED.value,
                    requested_by_id=admin.id,
                    source_ip="127.0.0.1",
                    organization_id=organization.id,
                    cluster_id=cluster.id,
                    workload_id=workload.id,
                    idempotency_key_hash=token_bytes(32),
                    request_fingerprint=token_bytes(32),
                    celery_task_id=str(uuid4()),
                    result={"action_mode": "STANDARD"},
                    error_code="CLUSTER_UNREACHABLE",
                    error_summary="Cluster temporarily unavailable.",
                    retryable=True,
                    finished_at=datetime.now(UTC),
                    version=1,
                )
                session.add(failed)
                await session.commit()
                failed_id = failed.id

            assigned = await client.post(
                f"/api/v1/admin/operations/{failed_id}/assign",
                headers=headers,
                json={"version": 1, "assigned_to_id": str(admin.id)},
            )
            assert assigned.status_code == 200
            assert assigned.json()["version"] == 2
            assert assigned.json()["assignment"]["assigned_to_id"] == str(admin.id)

            stale_assign = await client.post(
                f"/api/v1/admin/operations/{failed_id}/assign",
                headers=headers,
                json={"version": 1, "assigned_to_id": str(admin.id)},
            )
            assert stale_assign.status_code == 409
            assert stale_assign.json()["error"]["code"] == "OPERATION_VERSION_CONFLICT"

            acknowledged = await client.post(
                f"/api/v1/admin/operations/{failed_id}/acknowledge",
                headers=headers,
                json={"version": 2},
            )
            assert acknowledged.status_code == 200
            assert acknowledged.json()["version"] == 3

            resolved = await client.post(
                f"/api/v1/admin/operations/{failed_id}/resolve-manually",
                headers=headers,
                json={"version": 3, "resolution_note": "Cluster state verified by operator."},
            )
            assert resolved.status_code == 200
            assert resolved.json()["version"] == 4
            assert resolved.json()["assignment"]["resolution_note"] == (
                "Cluster state verified by operator."
            )

            retried = await client.post(
                f"/api/v1/admin/operations/{failed_id}/retry",
                headers=headers,
                json={"version": 4},
            )
            duplicate_retry = await client.post(
                f"/api/v1/admin/operations/{failed_id}/retry",
                headers=headers,
                json={"version": 4},
            )
            assert retried.status_code == duplicate_retry.status_code == 202
            assert (
                retried.json()["created_operation_id"]
                == duplicate_retry.json()["created_operation_id"]
            )
            assert len(published) == 1

            cancelled_provisioning = await client.post(
                f"/api/v1/admin/operations/{queued_provisioning.id}/cancel",
                headers=headers,
                json={"version": 1},
            )
            assert cancelled_provisioning.status_code == 200
            assert cancelled_provisioning.json()["status"] == "CANCELLED"

            retried_provisioning = await client.post(
                f"/api/v1/admin/operations/{failed_provisioning.id}/retry",
                headers=headers,
                json={"version": 1},
            )
            duplicate_provisioning_retry = await client.post(
                f"/api/v1/admin/operations/{failed_provisioning.id}/retry",
                headers=headers,
                json={"version": 1},
            )
            assert retried_provisioning.status_code == 202
            assert (
                retried_provisioning.json()["created_operation_id"]
                == duplicate_provisioning_retry.json()["created_operation_id"]
            )
            assert len(provisioning_published) == 1
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
        await app.state.redis.aclose()
