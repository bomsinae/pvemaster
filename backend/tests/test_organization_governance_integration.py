import asyncio
import os
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app
from app.models.auth import (
    Organization,
    OrganizationMember,
    OrganizationRole,
    User,
    UserRole,
)
from app.models.cluster import Cluster
from app.models.operation import Workload, WorkloadAssignment
from app.models.organization_governance import (
    OrganizationInvitation,
    OrganizationQuota,
)
from app.models.self_service import ServiceRequest
from app.security.passwords import PasswordManager
from app.services.quota import reserve_quota

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "device_label": "Governance test"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _seed(app: FastAPI) -> tuple[dict[str, str], dict[str, object]]:
    names = ("admin", "operator", "owner", "viewer", "invitee")
    passwords = {name: token_urlsafe(24) for name in names}
    password_manager = PasswordManager()
    ids = {name: uuid4() for name in passwords}
    organization_a = uuid4()
    organization_b = uuid4()
    async with app.state.db_session_factory() as session:
        await session.execute(text("TRUNCATE users, organizations CASCADE"))
        users = {
            "admin": User(
                id=ids["admin"],
                email="admin@example.test",
                display_name="Platform admin",
                role=UserRole.SUPER_ADMIN.value,
                password_hash=password_manager.hash(passwords["admin"]),
                is_active=True,
            ),
            "operator": User(
                id=ids["operator"],
                email="operator@example.test",
                display_name="Platform operator",
                role=UserRole.OPERATOR.value,
                password_hash=password_manager.hash(passwords["operator"]),
                is_active=True,
            ),
            "owner": User(
                id=ids["owner"],
                email="owner@example.test",
                display_name="Organization owner",
                role=UserRole.CUSTOMER.value,
                password_hash=password_manager.hash(passwords["owner"]),
                is_active=True,
            ),
            "viewer": User(
                id=ids["viewer"],
                email="viewer@example.test",
                display_name="Organization viewer",
                role=UserRole.CUSTOMER.value,
                password_hash=password_manager.hash(passwords["viewer"]),
                is_active=True,
            ),
            "invitee": User(
                id=ids["invitee"],
                email="invitee@example.test",
                display_name="Invited operator",
                role=UserRole.CUSTOMER.value,
                password_hash=password_manager.hash(passwords["invitee"]),
                is_active=True,
            ),
        }
        session.add_all(users.values())
        await session.flush()
        session.add_all(
            [
                Organization(
                    id=organization_a,
                    name="Alpha",
                    created_by_id=ids["admin"],
                    is_active=True,
                ),
                Organization(
                    id=organization_b,
                    name="Beta",
                    created_by_id=ids["admin"],
                    is_active=True,
                ),
            ]
        )
        owner_membership = uuid4()
        viewer_membership = uuid4()
        session.add_all(
            [
                OrganizationMember(
                    id=owner_membership,
                    organization_id=organization_a,
                    user_id=ids["owner"],
                    added_by_id=ids["admin"],
                    organization_role=OrganizationRole.ORG_OWNER.value,
                    status="ACTIVE",
                    version=1,
                ),
                OrganizationMember(
                    id=viewer_membership,
                    organization_id=organization_a,
                    user_id=ids["viewer"],
                    added_by_id=ids["admin"],
                    organization_role=OrganizationRole.ORG_VIEWER.value,
                    status="ACTIVE",
                    version=1,
                ),
                OrganizationMember(
                    organization_id=organization_b,
                    user_id=ids["viewer"],
                    added_by_id=ids["admin"],
                    organization_role=OrganizationRole.ORG_VIEWER.value,
                    status="ACTIVE",
                    expires_at=datetime.now(UTC) - timedelta(minutes=1),
                    version=1,
                ),
            ]
        )
        await session.commit()
    return passwords, {
        **ids,
        "organization_a": organization_a,
        "organization_b": organization_b,
        "owner_membership": owner_membership,
        "viewer_membership": viewer_membership,
    }


async def test_organization_rbac_invitation_quota_and_policy_boundaries() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    passwords, ids = await _seed(app)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        tokens = {
            name: await _login(client, f"{name}@example.test", passwords[name])
            for name in ("admin", "operator", "owner", "viewer", "invitee")
        }
        headers = {name: {"Authorization": f"Bearer {token}"} for name, token in tokens.items()}
        organization_a = ids["organization_a"]
        organization_b = ids["organization_b"]

        organizations = await client.get(
            "/api/v1/customer/organizations", headers=headers["viewer"]
        )
        assert organizations.status_code == 200
        assert [item["organization_name"] for item in organizations.json()] == ["Alpha"]

        cross_scope = await client.get(
            f"/api/v1/customer/organizations/{organization_b}/members",
            headers=headers["owner"],
        )
        assert cross_scope.status_code == 404

        viewer_invite = await client.post(
            f"/api/v1/customer/organizations/{organization_a}/invitations",
            headers=headers["viewer"],
            json={
                "email": "invitee@example.test",
                "organization_role": "ORG_OPERATOR",
            },
        )
        assert viewer_invite.status_code == 403

        invitation = await client.post(
            f"/api/v1/customer/organizations/{organization_a}/invitations",
            headers=headers["owner"],
            json={
                "email": "invitee@example.test",
                "organization_role": "ORG_OPERATOR",
                "expires_in_hours": 24,
            },
        )
        assert invitation.status_code == 201, invitation.text
        accept_token = invitation.json()["accept_token"]
        assert accept_token
        async with app.state.db_session_factory() as session:
            stored = await session.scalar(select(OrganizationInvitation))
            assert stored is not None
            assert accept_token.encode() not in stored.token_hash

        mismatch = await client.post(
            "/api/v1/customer/organization-invitations/accept",
            headers=headers["viewer"],
            json={"token": accept_token},
        )
        assert mismatch.status_code == 403

        accepted = await client.post(
            "/api/v1/customer/organization-invitations/accept",
            headers=headers["invitee"],
            json={"token": accept_token},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["organization_role"] == "ORG_OPERATOR"
        replay = await client.post(
            "/api/v1/customer/organization-invitations/accept",
            headers=headers["invitee"],
            json={"token": accept_token},
        )
        assert replay.status_code == 404

        last_owner = await client.patch(
            f"/api/v1/customer/organizations/{organization_a}/members/{ids['owner_membership']}",
            headers=headers["owner"],
            json={"organization_role": "ORG_VIEWER", "version": 1},
        )
        assert last_owner.status_code == 409
        assert last_owner.json()["error"]["code"] == "LAST_ORGANIZATION_OWNER"

        customer_admin_boundary = await client.get(
            "/api/v1/admin/organizations", headers=headers["owner"]
        )
        assert customer_admin_boundary.status_code == 403

        quota_payload = {
            "max_vcpu": 32,
            "max_memory_bytes": 137438953472,
            "max_disk_bytes": 1099511627776,
            "max_vms": 10,
            "max_ips": 16,
            "max_backup_bytes": 4398046511104,
        }
        quota = await client.put(
            f"/api/v1/admin/organizations/{organization_a}/quota",
            headers=headers["admin"],
            json=quota_payload,
        )
        assert quota.status_code == 200, quota.text
        assert quota.json()["limits"]["vcpu"] == 32
        operator_quota = await client.get(
            f"/api/v1/admin/organizations/{organization_a}/quota",
            headers=headers["operator"],
        )
        assert operator_quota.status_code == 200
        customer_quota = await client.get(
            f"/api/v1/customer/organizations/{organization_a}/quota",
            headers=headers["viewer"],
        )
        assert customer_quota.status_code == 200
        assert customer_quota.json()["usage"]["vms"] == 0

        policy = await client.put(
            f"/api/v1/admin/organizations/{organization_a}/approval-policies",
            headers=headers["admin"],
            json={
                "request_type": "RESIZE",
                "requires_approval": True,
                "minimum_role": "ORG_OPERATOR",
            },
        )
        assert policy.status_code == 200, policy.text
        policy_read = await client.get(
            f"/api/v1/customer/organizations/{organization_a}/approval-policies",
            headers=headers["viewer"],
        )
        assert policy_read.status_code == 200
        assert policy_read.json()[0]["request_type"] == "RESIZE"

        race_payload = {**quota_payload, "max_vcpu": 48, "version": 1}
        first, second = await asyncio.gather(
            client.put(
                f"/api/v1/admin/organizations/{organization_a}/quota",
                headers=headers["admin"],
                json=race_payload,
            ),
            client.put(
                f"/api/v1/admin/organizations/{organization_a}/quota",
                headers=headers["admin"],
                json=race_payload,
            ),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 409]

        activity = await client.get(
            f"/api/v1/customer/organizations/{organization_a}/activity",
            headers=headers["owner"],
        )
        assert activity.status_code == 200
        assert any(item["action"] == "ORGANIZATION_QUOTA_UPDATED" for item in activity.json())

    await app.state.db_engine.dispose()


async def test_concurrent_quota_reservations_cannot_overcommit() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    _passwords, ids = await _seed(app)
    organization_id = ids["organization_a"]
    admin_id = ids["admin"]
    owner_id = ids["owner"]
    cluster_id = uuid4()
    request_ids = (uuid4(), uuid4())
    now = datetime.now(UTC)
    async with app.state.db_session_factory() as session:
        session.add(
            Cluster(
                id=cluster_id,
                name="quota-race",
                api_base_url="https://quota-race.example.test:8006",
                is_active=True,
            )
        )
        await session.flush()
        workloads = [
            Workload(
                id=uuid4(),
                cluster_id=cluster_id,
                vmid=9100 + index,
                node="pve",
                kind="QEMU",
                name=f"quota-{index}",
                power_state="RUNNING",
                cpu_cores=0,
                memory_bytes=0,
                disk_bytes=0,
                is_template=False,
                is_present=True,
                organization_id=organization_id,
                observed_at=now,
            )
            for index in range(2)
        ]
        session.add_all(workloads)
        await session.flush()
        assignments = [
            WorkloadAssignment(
                id=uuid4(),
                workload_id=workload.id,
                organization_id=organization_id,
                assigned_by_id=admin_id,
            )
            for workload in workloads
        ]
        session.add_all(assignments)
        await session.flush()
        session.add(
            OrganizationQuota(
                organization_id=organization_id,
                max_vcpu=1,
                max_memory_bytes=1024,
                max_disk_bytes=1024,
                max_vms=10,
                max_ips=10,
                max_backup_bytes=1024,
                updated_by_id=admin_id,
                version=1,
            )
        )
        session.add_all(
            [
                ServiceRequest(
                    id=request_id,
                    request_type="RESIZE",
                    requested_by_id=owner_id,
                    organization_id=organization_id,
                    workload_id=workload.id,
                    assignment_id=assignment.id,
                    input_snapshot={"cpu_cores": 1},
                    impact_snapshot={"messages": []},
                    status="PENDING_APPROVAL",
                    idempotency_key_hash=f"quota-{index}".encode().ljust(32, b"0"),
                    request_fingerprint=f"fingerprint-{index}".encode().ljust(32, b"0"),
                    version=1,
                )
                for index, (request_id, workload, assignment) in enumerate(
                    zip(request_ids, workloads, assignments, strict=True)
                )
            ]
        )
        await session.commit()

    async def attempt(request_id: UUID) -> str:
        async with app.state.db_session_factory() as session:
            try:
                await reserve_quota(
                    session,
                    organization_id,
                    service_request_id=request_id,
                    vcpu=1,
                )
                await session.commit()
                return "RESERVED"
            except AppError as exc:
                await session.rollback()
                return exc.code

    results = await asyncio.gather(*(attempt(request_id) for request_id in request_ids))
    assert sorted(results) == ["ORGANIZATION_QUOTA_EXCEEDED", "RESERVED"]
    async with app.state.db_session_factory() as session:
        await session.execute(text("TRUNCATE users, organizations CASCADE"))
        await session.commit()
    await app.state.db_engine.dispose()
