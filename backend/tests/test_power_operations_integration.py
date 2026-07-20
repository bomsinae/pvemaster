import os
from datetime import UTC, datetime
from secrets import token_bytes, token_urlsafe
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.core.errors import AppError
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
from app.models.operation import (
    Operation,
    OperationStatus,
    PveTask,
    Workload,
    WorkloadAssignment,
)
from app.security.credentials import CredentialCipher
from app.security.passwords import PasswordManager
from app.services.power_runner import PowerOperationRunner

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


class FakePowerApi:
    def __init__(
        self,
        *,
        vm_states: list[str] | None = None,
        task_statuses: list[dict[str, str]] | None = None,
        submit_error: AppError | None = None,
    ) -> None:
        self.vm_states = vm_states or ["stopped", "running"]
        self.task_statuses = task_statuses or [{"status": "stopped", "exitstatus": "OK"}]
        self.submit_error = submit_error
        self.submit_count = 0
        self.status_count = 0
        self.submissions: list[tuple[str, str]] = []
        self.upid = f"UPID:pve-a:{uuid4()}:power:101:service@pve:"

    async def get_guest_status(self, *, kind: str, node: str, vmid: int) -> dict[str, str]:
        del kind, node, vmid
        state = self.vm_states[min(self.status_count, len(self.vm_states) - 1)]
        self.status_count += 1
        return {"status": state}

    async def submit_guest_power_action(
        self, *, kind: str, node: str, vmid: int, action: str
    ) -> str:
        del node, vmid
        self.submit_count += 1
        self.submissions.append((kind, action))
        if self.submit_error is not None:
            raise self.submit_error
        return self.upid

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, str]:
        del node, upid
        if len(self.task_statuses) > 1:
            return self.task_statuses.pop(0)
        return self.task_statuses[0]


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


async def _seed(app: FastAPI, admin_password: str, customer_password: str) -> tuple[User, Workload]:
    passwords = PasswordManager()
    cipher = app.state.credential_cipher
    assert isinstance(cipher, CredentialCipher)
    async with app.state.db_session_factory() as session:
        admin = User(
            id=uuid4(),
            email="power-admin@example.test",
            display_name="Power admin",
            role=UserRole.OPERATOR.value,
            password_hash=passwords.hash(admin_password),
            is_active=True,
        )
        customer = User(
            id=uuid4(),
            email="power-customer@example.test",
            display_name="Power customer",
            role=UserRole.CUSTOMER.value,
            password_hash=passwords.hash(customer_password),
            is_active=True,
        )
        cluster_id = uuid4()
        credential_id = uuid4()
        encrypted = cipher.encrypt(
            token_urlsafe(32), cluster_id=cluster_id, credential_id=credential_id
        )
        cluster = Cluster(
            id=cluster_id,
            name="power-test-cluster",
            api_base_url="https://pve.example.test:8006",
            is_active=True,
            version=1,
        )
        credential = ClusterCredential(
            id=credential_id,
            cluster_id=cluster_id,
            token_identifier="service@pve!power-test",
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
            name="test-vm",
            power_state="STOPPED",
            is_template=False,
            is_present=True,
            observed_at=datetime.now(UTC),
            version=1,
        )
        session.add_all([admin, customer, cluster, credential, workload])
        await session.commit()
        return admin, workload


async def _login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _no_sleep(_: float) -> None:
    return None


async def _run(
    app: FastAPI,
    settings: Settings,
    operation_id: str,
    client: FakePowerApi,
) -> None:
    async with app.state.db_session_factory() as session:
        runner = PowerOperationRunner(
            session=session,
            settings=settings,
            cipher=app.state.credential_cipher,
            client=client,
            sleep=_no_sleep,
        )
        await runner.run(uuid4() if operation_id == "missing" else UUID(operation_id))


async def test_power_job_lifecycle_failures_recovery_and_access_control() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        pve_task_poll_interval_seconds=0.001,
        pve_task_timeout_seconds=10,
        pve_task_max_poll_attempts=2,
        pve_action_max_attempts=2,
    )
    app = create_app(settings)
    published: list[tuple[object, str]] = []
    app.state.operation_publisher = lambda operation_id, task_id: published.append(
        (operation_id, task_id)
    )
    admin_password = token_urlsafe(24)
    customer_password = token_urlsafe(24)
    await _clear(app)
    admin, workload = await _seed(app, admin_password, customer_password)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            admin_token = await _login(http, admin.email, admin_password)
            customer_token = await _login(http, "power-customer@example.test", customer_password)
            admin_headers = {
                "Authorization": f"Bearer {admin_token}",
                "Idempotency-Key": token_urlsafe(18),
            }
            accepted = await http.post(
                f"/api/v1/admin/workloads/{workload.id}/actions/start",
                headers=admin_headers,
                json={},
            )
            assert accepted.status_code == 202, accepted.text
            assert accepted.headers["location"].endswith(accepted.json()["id"])
            assert accepted.json()["action_mode"] == "STANDARD"
            assert accepted.json()["workload_id"] == str(workload.id)
            assert accepted.json()["vm_id"] == str(workload.id)
            assert accepted.json()["result"]["workload_kind"] == "QEMU"
            assert len(published) == 1

            duplicate = await http.post(
                f"/api/v1/admin/vms/{workload.id}/actions/start",
                headers=admin_headers,
                json={},
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["id"] == accepted.json()["id"]
            assert len(published) == 1

            reused = await http.post(
                f"/api/v1/admin/vms/{workload.id}/actions/stop",
                headers=admin_headers,
                json={},
            )
            assert reused.status_code == 409
            assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

            missing = await http.post(
                f"/api/v1/admin/vms/{uuid4()}/actions/start",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={},
            )
            assert missing.status_code == 404

            denied = await http.post(
                f"/api/v1/admin/vms/{workload.id}/actions/start",
                headers={
                    "Authorization": f"Bearer {customer_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={},
            )
            assert denied.status_code == 403

            normal = FakePowerApi()
            await _run(app, settings, accepted.json()["id"], normal)
            completed = await http.get(
                f"/api/v1/jobs/{accepted.json()['id']}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "SUCCEEDED"
            assert completed.json()["pve_upid"] == normal.upid
            assert normal.submit_count == 1
            async with app.state.db_session_factory() as session:
                final_audit = await session.scalar(
                    select(AuditLog).where(AuditLog.pve_upid == normal.upid)
                )
                assert final_audit is not None
                assert final_audit.actor_user_id == admin.id
                assert final_audit.workload_id == workload.id
                assert final_audit.operation_id == UUID(accepted.json()["id"])
                assert final_audit.source_ip == "127.0.0.1"
                assert final_audit.outcome == "SUCCEEDED"

            no_op = await http.post(
                f"/api/v1/admin/vms/{workload.id}/actions/start",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={},
            )
            no_op_client = FakePowerApi(vm_states=["running"])
            await _run(app, settings, no_op.json()["id"], no_op_client)
            no_op_job = await http.get(
                f"/api/v1/jobs/{no_op.json()['id']}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert no_op_job.json()["status"] == "SUCCEEDED"
            assert no_op_job.json()["result"]["no_op"] is True
            assert no_op_client.submit_count == 0

            failure_key = token_urlsafe(18)
            failure = await http.post(
                f"/api/v1/admin/vms/{workload.id}/actions/reboot",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "Idempotency-Key": failure_key,
                },
                json={},
            )
            failure_client = FakePowerApi(
                vm_states=["running"],
                submit_error=AppError(
                    status_code=403,
                    code="PVE_PERMISSION_DENIED",
                    message="permission denied",
                ),
            )
            await _run(app, settings, failure.json()["id"], failure_client)
            failed = await http.get(
                f"/api/v1/jobs/{failure.json()['id']}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert failed.json()["status"] == "FAILED"
            assert failed.json()["retryable"] is False
            assert failure_client.submit_count == 1

            timeout = await http.post(
                f"/api/v1/admin/vms/{workload.id}/actions/reset",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={},
            )
            timeout_client = FakePowerApi(
                vm_states=["running"],
                task_statuses=[{"status": "running"}],
            )
            await _run(app, settings, timeout.json()["id"], timeout_client)
            timed_out = await http.get(
                f"/api/v1/jobs/{timeout.json()['id']}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert timed_out.json()["status"] == "TIMEOUT"
            assert timed_out.json()["retryable"] is True

            async with app.state.db_session_factory() as session:
                recovered = Operation(
                    id=uuid4(),
                    operation_type="POWER_START",
                    action="start",
                    status=OperationStatus.RUNNING.value,
                    requested_by_id=admin.id,
                    source_ip="127.0.0.1",
                    cluster_id=workload.cluster_id,
                    workload_id=workload.id,
                    idempotency_key_hash=token_bytes(32),
                    request_fingerprint=token_bytes(32),
                    celery_task_id=str(uuid4()),
                    result={"action_mode": "STANDARD"},
                    attempt_count=1,
                    started_at=datetime.now(UTC),
                    heartbeat_at=datetime.now(UTC),
                    version=1,
                )
                session.add(recovered)
                await session.flush()
                session.add(
                    PveTask(
                        operation_id=recovered.id,
                        cluster_id=workload.cluster_id,
                        workload_id=workload.id,
                        step_name="power_start",
                        upid="UPID:pve-a:restart-recovery",
                        status="RUNNING",
                        pve_node="pve-a",
                        submitted_at=datetime.now(UTC),
                        poll_attempts=1,
                    )
                )
                await session.commit()
                recovered_id = recovered.id
            restart_client = FakePowerApi(vm_states=["running"])
            await _run(app, settings, str(recovered_id), restart_client)
            recovered_job = await http.get(
                f"/api/v1/jobs/{recovered_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert recovered_job.json()["status"] == "SUCCEEDED"
            assert restart_client.submit_count == 0

            async with app.state.db_session_factory() as session:
                lxc = Workload(
                    id=uuid4(),
                    cluster_id=workload.cluster_id,
                    vmid=202,
                    node="pve-a",
                    kind="LXC",
                    name="test-container",
                    power_state="STOPPED",
                    is_template=False,
                    is_present=True,
                    observed_at=datetime.now(UTC),
                    version=1,
                )
                session.add(lxc)
                await session.commit()
                lxc_id = lxc.id

            lxc_cases = {
                "start": ["stopped", "running"],
                "shutdown": ["running", "stopped"],
                "reboot": ["running", "running"],
                "stop": ["running", "stopped"],
            }
            for action, states in lxc_cases.items():
                accepted_lxc = await http.post(
                    f"/api/v1/admin/workloads/{lxc_id}/actions/{action}",
                    headers={
                        "Authorization": f"Bearer {admin_token}",
                        "Idempotency-Key": token_urlsafe(18),
                    },
                    json={},
                )
                assert accepted_lxc.status_code == 202, accepted_lxc.text
                lxc_client = FakePowerApi(vm_states=states)
                await _run(app, settings, accepted_lxc.json()["id"], lxc_client)
                completed_lxc = await http.get(
                    f"/api/v1/jobs/{accepted_lxc.json()['id']}",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                assert completed_lxc.json()["status"] == "SUCCEEDED"
                assert completed_lxc.json()["workload_id"] == str(lxc_id)
                assert completed_lxc.json()["result"]["workload_kind"] == "LXC"
                assert lxc_client.submissions == [("LXC", action)]

            rejected_lxc_reset = await http.post(
                f"/api/v1/admin/workloads/{lxc_id}/actions/reset",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "Idempotency-Key": token_urlsafe(18),
                },
                json={},
            )
            assert rejected_lxc_reset.status_code == 409
            assert rejected_lxc_reset.json()["error"]["code"] == "POWER_ACTION_UNSUPPORTED"
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
