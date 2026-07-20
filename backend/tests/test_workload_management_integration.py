import os
from secrets import token_urlsafe
from uuid import uuid4

import httpx
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
    OrganizationMember,
    RefreshToken,
    User,
    UserRole,
)
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, PveTask, Workload, WorkloadAssignment
from app.security.credentials import CredentialCipher
from app.security.passwords import PasswordManager

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
            WorkloadAssignment,
            Workload,
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


async def test_import_membership_assignment_and_revocation_flow() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        pve_allowed_hosts=["1.1.1.1"],
    )
    app = create_app(settings)
    password_manager = PasswordManager()
    passwords = {name: token_urlsafe(24) for name in ("admin", "customer", "operator")}
    secret = token_urlsafe(32)
    await _clear(app)

    async with app.state.db_session_factory() as session:
        admin = User(
            id=uuid4(),
            email="ownership-admin@example.test",
            display_name="Ownership Admin",
            role=UserRole.SUPER_ADMIN.value,
            password_hash=password_manager.hash(passwords["admin"]),
            is_active=True,
        )
        operator = User(
            id=uuid4(),
            email="ownership-operator@example.test",
            display_name="Ownership Operator",
            role=UserRole.OPERATOR.value,
            password_hash=password_manager.hash(passwords["operator"]),
            is_active=True,
        )
        customer = User(
            id=uuid4(),
            email="ownership-customer@example.test",
            display_name="Ownership Customer",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(passwords["customer"]),
            is_active=True,
        )
        session.add_all([admin, operator, customer])
        await session.flush()
        organization = Organization(
            id=uuid4(),
            name="Ownership Organization",
            is_active=True,
            created_by_id=admin.id,
            version=1,
        )
        other_organization = Organization(
            id=uuid4(),
            name="Other Organization",
            is_active=True,
            created_by_id=admin.id,
            version=1,
        )
        cluster = Cluster(
            id=uuid4(),
            name="ownership-pve",
            api_base_url="https://1.1.1.1",
            is_active=True,
            version=1,
        )
        credential_id = uuid4()
        encrypted = CredentialCipher(settings.app_secret_key.get_secret_value()).encrypt(
            secret,
            cluster_id=cluster.id,
            credential_id=credential_id,
        )
        credential = ClusterCredential(
            id=credential_id,
            cluster_id=cluster.id,
            token_identifier="service@pve!ownership",
            secret_ciphertext=encrypted.ciphertext,
            secret_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            is_active=True,
        )
        session.add_all([organization, other_organization, cluster, credential])
        await session.commit()

    requests: list[str] = []

    def pve(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.raw_path.decode())
        assert request.headers["Authorization"] == f"PVEAPIToken=service@pve!ownership={secret}"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "vmid": 101,
                        "node": "pve-a",
                        "type": "qemu",
                        "name": "existing-vm",
                        "status": "running",
                        "maxcpu": 4,
                        "maxmem": 8_589_934_592,
                        "maxdisk": 107_374_182_400,
                        "template": 0,
                    },
                    {
                        "vmid": 202,
                        "node": "pve-a",
                        "type": "lxc",
                        "name": "existing-ct",
                        "status": "stopped",
                        "maxcpu": 2,
                        "maxmem": 2_147_483_648,
                        "maxdisk": 21_474_836_480,
                        "template": 0,
                    },
                ]
            },
        )

    app.state.proxmox_transport = httpx.MockTransport(pve)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            admin_token = await _login(client, admin.email, passwords["admin"])
            operator_token = await _login(client, operator.email, passwords["operator"])
            customer_token = await _login(client, customer.email, passwords["customer"])
            admin_headers = {"Authorization": f"Bearer {admin_token}"}
            operator_headers = {"Authorization": f"Bearer {operator_token}"}
            customer_headers = {"Authorization": f"Bearer {customer_token}"}

            imported = await client.post(
                f"/api/v1/admin/clusters/{cluster.id}/workloads/import",
                headers=admin_headers,
            )
            repeated = await client.post(
                f"/api/v1/admin/clusters/{cluster.id}/workloads/import",
                headers=admin_headers,
            )
            assert imported.status_code == repeated.status_code == 200
            assert imported.json()["created"] == 2
            assert repeated.json()["created"] == 0
            assert repeated.json()["updated"] == 2
            assert len(requests) == 2

            listing = await client.get("/api/v1/admin/workloads", headers=operator_headers)
            assert listing.status_code == 200
            assert {item["kind"] for item in listing.json()["items"]} == {"QEMU", "LXC"}
            vm = next(item for item in listing.json()["items"] if item["kind"] == "QEMU")
            ct = next(item for item in listing.json()["items"] if item["kind"] == "LXC")
            assert (vm["cpu_cores"], vm["memory_bytes"], vm["disk_bytes"]) == (
                4,
                8_589_934_592,
                107_374_182_400,
            )
            assert (ct["cpu_cores"], ct["memory_bytes"], ct["disk_bytes"]) == (
                2,
                2_147_483_648,
                21_474_836_480,
            )

            added = await client.post(
                f"/api/v1/admin/organizations/{organization.id}/members",
                headers=admin_headers,
                json={"user_id": str(customer.id)},
            )
            assert added.status_code == 201
            members = await client.get(
                f"/api/v1/admin/organizations/{organization.id}/members",
                headers=operator_headers,
            )
            assert members.status_code == 200
            assert members.json()["items"][0]["email"] == customer.email

            assigned = await client.post(
                f"/api/v1/admin/workloads/{vm['id']}/assign",
                headers=operator_headers,
                json={"organization_id": str(organization.id)},
            )
            duplicate = await client.post(
                f"/api/v1/admin/workloads/{vm['id']}/assign",
                headers=operator_headers,
                json={"organization_id": str(organization.id)},
            )
            conflict = await client.post(
                f"/api/v1/admin/workloads/{vm['id']}/assign",
                headers=operator_headers,
                json={"organization_id": str(other_organization.id)},
            )
            assert assigned.status_code == duplicate.status_code == 200
            assert assigned.json()["id"] == duplicate.json()["id"]
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "WORKLOAD_ALREADY_ASSIGNED"

            visible = await client.get("/api/v1/customer/vms", headers=customer_headers)
            assert [item["name"] for item in visible.json()["items"]] == ["existing-vm"]
            assert visible.json()["items"][0]["cpu_cores"] == 4

            customer_admin_attempt = await client.get(
                "/api/v1/admin/workloads", headers=customer_headers
            )
            operator_remove_attempt = await client.delete(
                f"/api/v1/admin/organizations/{organization.id}/members/{customer.id}",
                headers=operator_headers,
            )
            assert customer_admin_attempt.status_code == 403
            assert operator_remove_attempt.status_code == 403

            removed = await client.delete(
                f"/api/v1/admin/organizations/{organization.id}/members/{customer.id}",
                headers=admin_headers,
            )
            assert removed.status_code == 204
            hidden = await client.get("/api/v1/customer/vms", headers=customer_headers)
            assert hidden.json()["items"] == []

            unassigned = await client.delete(
                f"/api/v1/admin/workloads/{vm['id']}/assignment",
                headers=operator_headers,
                params={"reason": "contract-ended"},
            )
            history = await client.get(
                f"/api/v1/admin/workloads/{vm['id']}/assignments",
                headers=operator_headers,
            )
            assert unassigned.status_code == 204
            assert len(history.json()["items"]) == 1
            assert history.json()["items"][0]["revoked_at"] is not None
            assert history.json()["items"][0]["revoke_reason"] == "contract-ended"
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
