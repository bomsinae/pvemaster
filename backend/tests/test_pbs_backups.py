import os
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app
from app.models.auth import AuditLog, LoginThrottle, RefreshToken, User, UserRole
from app.models.backup import BackupRun, BackupTarget, RestoreRun
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, PveTask, Workload
from app.security.credentials import CredentialCipher
from app.security.passwords import PasswordManager
from app.services.backup_runner import BackupOperationRunner
from app.services.restore_runner import RestoreOperationRunner

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


class FakeBackupApi:
    def __init__(self, *, submit_error: AppError | None = None) -> None:
        self.submit_error = submit_error
        self.submit_count = 0
        self.upid = "UPID:pve-a:00000001:00000002:00000003:vzdump:101:service@pve:"

    async def submit_guest_backup(
        self,
        *,
        node: str,
        vmid: int,
        storage: str,
        mode: str,
        compression: str,
    ) -> str:
        assert (node, vmid, storage, mode, compression) == (
            "pve-a",
            101,
            "pbs-main",
            "snapshot",
            "zstd",
        )
        self.submit_count += 1
        if self.submit_error is not None:
            raise self.submit_error
        return self.upid

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, str]:
        assert node == "pve-a"
        assert upid == self.upid
        return {"status": "stopped", "exitstatus": "OK"}

    async def get_backup_content(
        self, *, node: str, storage: str, vmid: int
    ) -> list[dict[str, object]]:
        assert (node, storage, vmid) == ("pve-a", "pbs-main", 101)
        return [
            {
                "content": "backup",
                "volid": "pbs-main:backup/vm/101/1784592000",
                "vmid": 101,
                "ctime": 1784592000,
                "size": 4096,
            }
        ]

    async def get_task_log(self, *, node: str, upid: str) -> list[dict[str, object]]:
        assert node == "pve-a"
        assert upid == self.upid
        return [
            {"n": 1, "t": "INFO: backup was done incrementally, reused 12.00 GiB (75%)"},
            {"n": 2, "t": "INFO: transferred 16.00 GiB in 10 seconds"},
        ]


class FakeRestoreApi:
    def __init__(self) -> None:
        self.submit_count = 0
        self.upid = "UPID:pve-a:00000001:00000002:00000003:qmrestore:220:service@pve:"

    async def get_nodes(self) -> list[dict[str, object]]:
        return [{"node": "pve-a", "status": "online"}]

    async def get_guests(self) -> list[dict[str, object]]:
        return [{"vmid": 101, "node": "pve-a"}]

    async def submit_guest_restore(
        self, *, kind: str, node: str, archive: str, vmid: int, name: str
    ) -> str:
        assert (kind, node, archive, vmid, name) == (
            "QEMU",
            "pve-a",
            "pbs-main:backup/vm/101/1784592000",
            220,
            "backup-vm-restored",
        )
        self.submit_count += 1
        return self.upid

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, str]:
        assert (node, upid) == ("pve-a", self.upid)
        return {"status": "stopped", "exitstatus": "OK"}


async def _clear(app: FastAPI) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            AuditLog,
            PveTask,
            RestoreRun,
            BackupRun,
            Operation,
            BackupTarget,
            Workload,
            ClusterCredential,
            Cluster,
            RefreshToken,
            LoginThrottle,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


async def _seed(app: FastAPI, password: str) -> tuple[User, User, User, Workload, Cluster]:
    passwords = PasswordManager()
    cipher = app.state.credential_cipher
    assert isinstance(cipher, CredentialCipher)
    async with app.state.db_session_factory() as session:
        users = [
            User(
                id=uuid4(),
                email=f"backup-{role.value.lower()}@example.test",
                display_name=role.value,
                role=role.value,
                password_hash=passwords.hash(password),
                is_active=True,
            )
            for role in (UserRole.SUPER_ADMIN, UserRole.OPERATOR, UserRole.CUSTOMER)
        ]
        cluster_id = uuid4()
        credential_id = uuid4()
        encrypted = cipher.encrypt(
            token_urlsafe(32), cluster_id=cluster_id, credential_id=credential_id
        )
        cluster = Cluster(
            id=cluster_id,
            name="backup-cluster",
            api_base_url="https://8.8.8.8:8006",
            is_active=True,
            version=1,
        )
        credential = ClusterCredential(
            id=credential_id,
            cluster_id=cluster_id,
            token_identifier="service@pve!backup-test",
            secret_ciphertext=encrypted.ciphertext,
            secret_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            is_active=True,
        )
        workload = Workload(
            id=uuid4(),
            cluster_id=cluster_id,
            vmid=101,
            node="pve-a",
            kind="QEMU",
            name="backup-vm",
            power_state="RUNNING",
            is_template=False,
            is_present=True,
            observed_at=datetime.now(UTC),
            version=1,
        )
        session.add_all([*users, cluster, credential, workload])
        await session.commit()
        return users[0], users[1], users[2], workload, cluster


async def _login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _no_sleep(_: float) -> None:
    return None


async def _run(app: FastAPI, settings: Settings, operation_id: str, api: FakeBackupApi) -> None:
    async with app.state.db_session_factory() as session:
        runner = BackupOperationRunner(
            session=session,
            settings=settings,
            cipher=app.state.credential_cipher,
            client=api,
            sleep=_no_sleep,
        )
        await runner.run(UUID(operation_id))


async def _run_restore(
    app: FastAPI, settings: Settings, operation_id: str, api: FakeRestoreApi
) -> None:
    async with app.state.db_session_factory() as session:
        runner = RestoreOperationRunner(
            session=session,
            settings=settings,
            cipher=app.state.credential_cipher,
            client=api,
            sleep=_no_sleep,
        )
        await runner.run(UUID(operation_id))


async def test_pbs_target_backup_lifecycle_idempotency_and_access_control() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr(
            os.environ.get("AUTH_TEST_REDIS_URL", "redis://localhost:6379/15")
        ),
        app_secret_key=SecretStr(token_urlsafe(32)),
        pve_allowed_networks=["8.8.8.8/32"],
        pve_task_poll_interval_seconds=0.001,
        pve_task_timeout_seconds=10,
        pve_task_max_poll_attempts=2,
    )
    app = create_app(settings)
    published: list[tuple[object, str]] = []
    app.state.backup_publisher = lambda operation_id, task_id: published.append(
        (operation_id, task_id)
    )
    restored_published: list[tuple[object, str]] = []
    app.state.restore_publisher = lambda operation_id, task_id: restored_published.append(
        (operation_id, task_id)
    )

    def pve_response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api2/json/storage":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "storage": "pbs-main",
                            "type": "pbs",
                            "content": "backup",
                            "datastore": "main",
                            "namespace": "pvemaster/backup-cluster",
                        },
                        {"storage": "local-lvm", "type": "lvmthin", "content": "images"},
                    ]
                },
            )
        if request.url.path == "/api2/json/nodes":
            return httpx.Response(200, json={"data": [{"node": "pve-a", "status": "online"}]})
        if (
            request.url.path == "/api2/json/cluster/resources"
            and request.url.params.get("type") == "vm"
        ):
            return httpx.Response(200, json={"data": [{"vmid": 101, "node": "pve-a"}]})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "storage": "pbs-main",
                        "type": "pbs",
                        "status": "available",
                    }
                ]
            },
        )

    app.state.proxmox_transport = httpx.MockTransport(pve_response)
    password = token_urlsafe(24)
    await _clear(app)
    super_admin, operator, customer, workload, cluster = await _seed(app, password)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            super_token = await _login(http, super_admin.email, password)
            operator_token = await _login(http, operator.email, password)
            customer_token = await _login(http, customer.email, password)

            discovery = await http.get(
                f"/api/v1/admin/clusters/{cluster.id}/backup-storages",
                headers={"Authorization": f"Bearer {super_token}"},
            )
            assert discovery.status_code == 200, discovery.text
            assert [item["storage_id"] for item in discovery.json()["items"]] == ["pbs-main"]
            assert discovery.json()["items"][0]["available"] is True

            created_target = await http.post(
                "/api/v1/admin/backup-targets",
                headers={"Authorization": f"Bearer {super_token}"},
                json={"cluster_id": str(cluster.id), "storage_id": "pbs-main"},
            )
            assert created_target.status_code == 201, created_target.text
            target_id = created_target.json()["id"]

            disabled_target = await http.patch(
                f"/api/v1/admin/backup-targets/{target_id}",
                headers={"Authorization": f"Bearer {super_token}"},
                json={"is_enabled": False, "version": created_target.json()["version"]},
            )
            assert disabled_target.status_code == 200, disabled_target.text
            assert disabled_target.json()["is_enabled"] is False

            enabled_target = await http.patch(
                f"/api/v1/admin/backup-targets/{target_id}",
                headers={"Authorization": f"Bearer {super_token}"},
                json={"is_enabled": True, "version": disabled_target.json()["version"]},
            )
            assert enabled_target.status_code == 200, enabled_target.text
            assert enabled_target.json()["is_enabled"] is True

            operator_create = await http.post(
                "/api/v1/admin/backup-targets",
                headers={"Authorization": f"Bearer {operator_token}"},
                json={"cluster_id": str(cluster.id), "storage_id": "pbs-main"},
            )
            assert operator_create.status_code == 403

            idempotency_key = token_urlsafe(18)
            request_headers = {
                "Authorization": f"Bearer {operator_token}",
                "Idempotency-Key": idempotency_key,
            }
            accepted = await http.post(
                f"/api/v1/admin/workloads/{workload.id}/backups",
                headers=request_headers,
                json={"backup_target_id": target_id},
            )
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["status"] == "QUEUED"
            assert len(published) == 1

            duplicate = await http.post(
                f"/api/v1/admin/workloads/{workload.id}/backups",
                headers=request_headers,
                json={"backup_target_id": target_id},
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["id"] == accepted.json()["id"]
            assert len(published) == 1

            denied = await http.post(
                f"/api/v1/admin/workloads/{workload.id}/backups",
                headers={
                    "Authorization": f"Bearer {customer_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={"backup_target_id": target_id},
            )
            assert denied.status_code == 403

            fake = FakeBackupApi()
            await _run(app, settings, accepted.json()["operation_id"], fake)
            completed = await http.get(
                f"/api/v1/admin/backups/{accepted.json()['id']}",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "SUCCEEDED"
            assert completed.json()["snapshot_volume_id"].startswith("pbs-main:backup/vm/101")
            assert completed.json()["size_bytes"] == 4096
            assert completed.json()["transferred_bytes"] == 4 * 1024**3
            assert fake.submit_count == 1

            operator_restore = await http.post(
                f"/api/v1/admin/backups/{accepted.json()['id']}/restores",
                headers={
                    "Authorization": f"Bearer {operator_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={
                    "target_node": "pve-a",
                    "target_vmid": 220,
                    "target_name": "backup-vm-restored",
                },
            )
            assert operator_restore.status_code == 403

            restore_key = token_urlsafe(18)
            restore_headers = {
                "Authorization": f"Bearer {super_token}",
                "Idempotency-Key": restore_key,
            }
            restore_request = await http.post(
                f"/api/v1/admin/backups/{accepted.json()['id']}/restores",
                headers=restore_headers,
                json={
                    "target_node": "pve-a",
                    "target_vmid": 220,
                    "target_name": "backup-vm-restored",
                },
            )
            assert restore_request.status_code == 202, restore_request.text
            assert restore_request.json()["status"] == "QUEUED"
            assert len(restored_published) == 1

            duplicate_restore = await http.post(
                f"/api/v1/admin/backups/{accepted.json()['id']}/restores",
                headers=restore_headers,
                json={
                    "target_node": "pve-a",
                    "target_vmid": 220,
                    "target_name": "backup-vm-restored",
                },
            )
            assert duplicate_restore.status_code == 202
            assert duplicate_restore.json()["id"] == restore_request.json()["id"]
            assert len(restored_published) == 1

            restore_api = FakeRestoreApi()
            await _run_restore(app, settings, restore_request.json()["operation_id"], restore_api)
            completed_restore = await http.get(
                f"/api/v1/admin/restores/{restore_request.json()['id']}",
                headers={"Authorization": f"Bearer {super_token}"},
            )
            assert completed_restore.status_code == 200
            assert completed_restore.json()["status"] == "SUCCEEDED"
            assert completed_restore.json()["target_vmid"] == 220
            assert restore_api.submit_count == 1

            timeout_request = await http.post(
                f"/api/v1/admin/workloads/{workload.id}/backups",
                headers={
                    "Authorization": f"Bearer {operator_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={"backup_target_id": target_id},
            )
            timeout_api = FakeBackupApi(
                submit_error=AppError(504, "PVE_TIMEOUT", "The backup submission timed out.")
            )
            await _run(app, settings, timeout_request.json()["operation_id"], timeout_api)
            timed_out = await http.get(
                f"/api/v1/admin/backups/{timeout_request.json()['id']}",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
            assert timed_out.json()["status"] == "TIMEOUT"
            assert timed_out.json()["retryable"] is False
            assert timeout_api.submit_count == 1

            async with app.state.db_session_factory() as session:
                final_audit = await session.scalar(
                    select(AuditLog).where(AuditLog.pve_upid == fake.upid)
                )
                assert final_audit is not None
                assert final_audit.outcome == "SUCCEEDED"
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
        await app.state.redis.aclose()
