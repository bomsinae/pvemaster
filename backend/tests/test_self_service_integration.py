import base64
import os
from datetime import UTC, datetime
from secrets import token_urlsafe

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
from app.models.cluster import Cluster
from app.models.operation import Operation, Workload, WorkloadAssignment
from app.models.self_service import (
    ApprovalStep,
    OrganizationServiceQuota,
    SecurityGroup,
    ServiceRequest,
    SshPublicKey,
    WorkloadSecurityGroup,
    WorkloadSshPublicKey,
)
from app.security.passwords import PasswordManager

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear(app: FastAPI) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            WorkloadSecurityGroup,
            WorkloadSshPublicKey,
            ApprovalStep,
            ServiceRequest,
            OrganizationServiceQuota,
            SecurityGroup,
            SshPublicKey,
            AuditLog,
            Operation,
            WorkloadAssignment,
            Workload,
            OrganizationMember,
            RefreshToken,
            Organization,
            Cluster,
            LoginThrottle,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "device_label": "Self-service test"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_customer_self_service_approval_isolation_and_recovery() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    await _clear(app)
    passwords = {
        "admin": token_urlsafe(24),
        "customer": token_urlsafe(24),
        "other": token_urlsafe(24),
    }
    manager = PasswordManager()
    now = datetime.now(UTC)
    async with app.state.db_session_factory() as session:
        admin = User(
            email="self-service-admin@example.test",
            display_name="Self-service Admin",
            role=UserRole.SUPER_ADMIN.value,
            password_hash=manager.hash(passwords["admin"]),
            is_active=True,
        )
        customer = User(
            email="self-service-customer@example.test",
            display_name="Self-service Customer",
            role=UserRole.CUSTOMER.value,
            password_hash=manager.hash(passwords["customer"]),
            is_active=True,
        )
        other = User(
            email="self-service-other@example.test",
            display_name="Other Customer",
            role=UserRole.CUSTOMER.value,
            password_hash=manager.hash(passwords["other"]),
            is_active=True,
        )
        cluster = Cluster(
            name="self-service-cluster",
            api_base_url="https://8.8.8.8:8006",
            is_active=True,
            version=1,
        )
        session.add_all([admin, customer, other, cluster])
        await session.flush()
        organization = Organization(
            name="Self-service Organization",
            created_by_id=admin.id,
            is_active=True,
        )
        foreign_organization = Organization(
            name="Foreign Organization",
            created_by_id=admin.id,
            is_active=True,
        )
        session.add_all([organization, foreign_organization])
        await session.flush()
        workload = Workload(
            cluster_id=cluster.id,
            vmid=701,
            node="pve-a",
            kind="QEMU",
            name="customer-app",
            power_state="RUNNING",
            cpu_cores=2,
            memory_bytes=4 * 1024**3,
            disk_bytes=40 * 1024**3,
            is_template=False,
            is_present=True,
            sync_generation=1,
            organization_id=organization.id,
            observed_at=now,
            version=1,
        )
        ownership_change_workload = Workload(
            cluster_id=cluster.id,
            vmid=702,
            node="pve-a",
            kind="QEMU",
            name="ownership-change",
            power_state="STOPPED",
            cpu_cores=2,
            memory_bytes=2 * 1024**3,
            disk_bytes=20 * 1024**3,
            is_template=False,
            is_present=True,
            sync_generation=1,
            organization_id=organization.id,
            observed_at=now,
            version=1,
        )
        session.add_all([workload, ownership_change_workload])
        await session.flush()
        session.add_all(
            [
                OrganizationMember(
                    organization_id=organization.id,
                    user_id=customer.id,
                    added_by_id=admin.id,
                ),
                OrganizationMember(
                    organization_id=foreign_organization.id,
                    user_id=other.id,
                    added_by_id=admin.id,
                ),
                WorkloadAssignment(
                    workload_id=workload.id,
                    organization_id=organization.id,
                    assigned_by_id=admin.id,
                ),
                WorkloadAssignment(
                    workload_id=ownership_change_workload.id,
                    organization_id=organization.id,
                    assigned_by_id=admin.id,
                ),
                OrganizationServiceQuota(
                    organization_id=organization.id,
                    max_cpu_cores_per_vm=8,
                    max_memory_bytes_per_vm=16 * 1024**3,
                    max_disk_bytes_per_vm=200 * 1024**3,
                    max_pending_requests=10,
                ),
            ]
        )
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            customer_login = await _login(client, customer.email, passwords["customer"])
            other_login = await _login(client, other.email, passwords["other"])
            admin_login = await _login(client, admin.email, passwords["admin"])
            customer_headers = {
                "Authorization": f"Bearer {customer_login['access_token']}"
            }
            other_headers = {"Authorization": f"Bearer {other_login['access_token']}"}
            admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}

            private_material = await client.post(
                f"/api/v1/customer/vms/{workload.id}/ssh-keys",
                headers=customer_headers,
                json={
                    "label": "unsafe",
                    "public_key": "-----BEGIN PRIVATE KEY----- unsafe material",
                },
            )
            assert private_material.status_code == 422

            public_key = "ssh-ed25519 " + base64.b64encode(b"k" * 32).decode()
            key_response = await client.post(
                f"/api/v1/customer/vms/{workload.id}/ssh-keys",
                headers=customer_headers,
                json={
                    "label": "Customer laptop",
                    "public_key": public_key,
                },
            )
            assert key_response.status_code == 201, key_response.text
            key = key_response.json()
            assert "PRIVATE" not in key_response.text
            assert key["fingerprint"].startswith("SHA256:")

            foreign_key_access = await client.delete(
                f"/api/v1/customer/ssh-keys/{key['id']}",
                headers=other_headers,
            )
            assert foreign_key_access.status_code == 404

            group_response = await client.post(
                "/api/v1/admin/security-groups",
                headers=admin_headers,
                json={
                    "organization_id": str(organization.id),
                    "name": "web-ingress",
                    "description": "Allow HTTPS only",
                    "rules": [
                        {
                            "direction": "IN",
                            "action": "ACCEPT",
                            "protocol": "tcp",
                            "source": "192.0.2.0/24",
                            "ports": [443],
                        }
                    ],
                    "is_global": False,
                },
            )
            assert group_response.status_code == 201, group_response.text
            groups = await client.get(
                f"/api/v1/customer/vms/{workload.id}/security-groups",
                headers=customer_headers,
            )
            assert [item["name"] for item in groups.json()["items"]] == ["web-ingress"]

            injection = await client.post(
                f"/api/v1/customer/vms/{workload.id}/service-requests/preview",
                headers=customer_headers,
                json={
                    "request_type": "METADATA_CHANGE",
                    "input": {"hostname": "vm; shutdown -h now"},
                },
            )
            assert injection.status_code == 422

            disk_shrink = await client.post(
                f"/api/v1/customer/vms/{workload.id}/service-requests/preview",
                headers=customer_headers,
                json={
                    "request_type": "RESIZE",
                    "input": {"disk_bytes": 10 * 1024**3},
                },
            )
            assert disk_shrink.status_code == 409
            quota_overflow = await client.post(
                f"/api/v1/customer/vms/{workload.id}/service-requests/preview",
                headers=customer_headers,
                json={
                    "request_type": "RESIZE",
                    "input": {"cpu_cores": 16},
                },
            )
            assert quota_overflow.status_code == 409

            request_payload = {
                "request_type": "RESIZE",
                "input": {
                    "cpu_cores": 4,
                    "memory_bytes": 8 * 1024**3,
                    "disk_bytes": 80 * 1024**3,
                    "reason": "Expected traffic increase",
                },
            }
            created = await client.post(
                f"/api/v1/customer/vms/{workload.id}/service-requests",
                headers={**customer_headers, "Idempotency-Key": "resize-request-001"},
                json=request_payload,
            )
            assert created.status_code == 202, created.text
            request_item = created.json()
            replay = await client.post(
                f"/api/v1/customer/vms/{workload.id}/service-requests",
                headers={**customer_headers, "Idempotency-Key": "resize-request-001"},
                json=request_payload,
            )
            assert replay.status_code == 202
            assert replay.json()["id"] == request_item["id"]
            duplicate_active = await client.post(
                f"/api/v1/customer/vms/{workload.id}/service-requests",
                headers={**customer_headers, "Idempotency-Key": "resize-request-002"},
                json=request_payload,
            )
            assert duplicate_active.status_code == 409

            approved = await client.post(
                f"/api/v1/admin/service-requests/{request_item['id']}/approve",
                headers=admin_headers,
                json={
                    "version": request_item["version"],
                    "reason": "Capacity and quota verified",
                },
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "APPROVED"
            assert approved.json()["operation_id"] is not None
            cannot_cancel = await client.post(
                f"/api/v1/customer/service-requests/{request_item['id']}/cancel",
                headers=customer_headers,
                json={"version": approved.json()["version"]},
            )
            assert cannot_cancel.status_code == 409
            started = await client.post(
                f"/api/v1/admin/service-requests/{request_item['id']}/execution",
                headers=admin_headers,
                json={
                    "version": approved.json()["version"],
                    "outcome": "START",
                    "summary": "Controlled resize started",
                },
            )
            assert started.status_code == 200, started.text
            completed = await client.post(
                f"/api/v1/admin/service-requests/{request_item['id']}/execution",
                headers=admin_headers,
                json={
                    "version": started.json()["version"],
                    "outcome": "SUCCEEDED",
                    "summary": "Resize verified by inventory sync",
                },
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "SUCCEEDED"

            reinstall_without_mfa = await client.post(
                f"/api/v1/customer/vms/{workload.id}/service-requests",
                headers={**customer_headers, "Idempotency-Key": "reinstall-request-001"},
                json={
                    "request_type": "REINSTALL",
                    "input": {"confirmation": "REINSTALL customer-app"},
                },
            )
            assert reinstall_without_mfa.status_code == 403
            assert reinstall_without_mfa.json()["error"]["code"] == "MFA_ENROLLMENT_REQUIRED"

            ownership_request = await client.post(
                (
                    f"/api/v1/customer/vms/{ownership_change_workload.id}"
                    "/service-requests"
                ),
                headers={**customer_headers, "Idempotency-Key": "metadata-request-001"},
                json={
                    "request_type": "METADATA_CHANGE",
                    "input": {"hostname": "renamed-app"},
                },
            )
            assert ownership_request.status_code == 202
            async with app.state.db_session_factory() as session:
                changed = await session.get(Workload, ownership_change_workload.id)
                assert changed is not None
                changed.organization_id = foreign_organization.id
                await session.commit()
            ownership_approval = await client.post(
                (
                    "/api/v1/admin/service-requests/"
                    f"{ownership_request.json()['id']}/approve"
                ),
                headers=admin_headers,
                json={
                    "version": ownership_request.json()["version"],
                    "reason": "Attempt after reassignment",
                },
            )
            assert ownership_approval.status_code == 409
            assert (
                ownership_approval.json()["error"]["code"]
                == "SERVICE_REQUEST_OWNERSHIP_CHANGED"
            )

            async with app.state.db_session_factory() as session:
                operation = await session.get(
                    Operation, completed.json()["operation_id"]
                )
                assert operation is not None
                assert operation.status == "SUCCEEDED"
                actions = set(
                    await session.scalars(
                            select(AuditLog.action).where(
                                AuditLog.resource_id == request_item["id"]
                            )
                    )
                )
                assert {
                    "CUSTOMER_SERVICE_REQUEST_CREATED",
                    "SERVICE_REQUEST_APPROVED",
                    "SERVICE_REQUEST_EXECUTION_STARTED",
                    "SERVICE_REQUEST_EXECUTION_SUCCEEDED",
                }.issubset(actions)
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
