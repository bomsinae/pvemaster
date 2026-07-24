import os
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.main import create_app
from app.models.auth import AuditLog, Organization, User, UserRole
from app.models.cluster import Cluster
from app.models.inventory import (
    FindingKind,
    FindingStatus,
    InventoryNode,
    InventoryStorage,
    ReconciliationFinding,
    WorkloadChangeEvent,
)
from app.models.operation import Workload, WorkloadAssignment
from app.models.scheduling import RunStatus, SyncRun
from app.security.access import Principal
from app.security.credentials import CredentialCipher
from app.security.passwords import PasswordManager
from app.services.inventory_sync import InventorySnapshot, ScheduledInventorySyncRunner
from app.services.reconciliation import ReconciliationService, create_sync_run

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear(app: FastAPI) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            AuditLog,
            WorkloadChangeEvent,
            ReconciliationFinding,
            InventoryNode,
            InventoryStorage,
            SyncRun,
            WorkloadAssignment,
            Workload,
            Cluster,
            Organization,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )


def _snapshot(
    guests: list[dict[str, object]] | None,
    *,
    errors: dict[str, str] | None = None,
) -> InventorySnapshot:
    return InventorySnapshot(
        nodes=[{"node": "pve-a", "status": "online", "maxcpu": 16}],
        guests=guests,
        storages=[{"storage": "local-lvm", "status": "available"}],
        errors=errors or {},
    )


async def _runner(
    app: FastAPI,
    settings: Settings,
    snapshot: InventorySnapshot,
) -> ScheduledInventorySyncRunner:
    async def load(_cluster_id: object) -> InventorySnapshot:
        return snapshot

    return ScheduledInventorySyncRunner(
        session=app.state.test_session,
        settings=settings,
        cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
        snapshot_loader=load,
    )


async def test_same_vmid_is_isolated_by_cluster_and_generations() -> None:
    settings = _settings()
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            app.state.test_session = session
            clusters = [
                Cluster(
                    id=uuid4(),
                    name=f"cluster-{suffix}",
                    api_base_url=f"https://pve-{suffix}.example.test:8006",
                    is_active=True,
                )
                for suffix in ("a", "b")
            ]
            session.add_all(clusters)
            await session.commit()
            for cluster in clusters:
                runner = await _runner(
                    app,
                    settings,
                    _snapshot(
                        [
                            {
                                "vmid": 101,
                                "node": "pve-a",
                                "type": "qemu",
                                "name": f"vm-{cluster.name}",
                                "status": "running",
                            }
                        ]
                    ),
                )
                run = await runner.run(cluster.id)
                assert run is not None
                assert run.status == RunStatus.SUCCEEDED.value
                assert run.generation == 1
            workloads = (await session.scalars(select(Workload).where(Workload.vmid == 101))).all()
            assert len(workloads) == 2
            assert {item.cluster_id for item in workloads} == {item.id for item in clusters}
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_complete_sync_tombstones_without_releasing_assignment() -> None:
    settings = _settings()
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            app.state.test_session = session
            user = User(
                id=uuid4(),
                email="inventory-owner@example.test",
                display_name="Inventory Owner",
                role=UserRole.OPERATOR.value,
                password_hash=PasswordManager().hash(token_urlsafe(24)),
                is_active=True,
            )
            session.add(user)
            await session.commit()
            organization = Organization(
                id=uuid4(),
                name="Inventory Tenant",
                is_active=True,
                created_by_id=user.id,
            )
            cluster = Cluster(
                id=uuid4(),
                name="tombstone-cluster",
                api_base_url="https://tombstone.example.test:8006",
                is_active=True,
            )
            workload = Workload(
                id=uuid4(),
                cluster_id=cluster.id,
                vmid=201,
                node="pve-a",
                kind="QEMU",
                name="assigned-vm",
                power_state="RUNNING",
                is_template=False,
                is_present=True,
                organization_id=organization.id,
                observed_at=datetime.now(UTC),
            )
            assignment = WorkloadAssignment(
                workload_id=workload.id,
                organization_id=organization.id,
                assigned_by_id=user.id,
            )
            session.add_all([organization, cluster, workload])
            await session.flush()
            session.add(assignment)
            await session.commit()

            run = await (await _runner(app, settings, _snapshot([]))).run(cluster.id)
            assert run is not None and run.status == RunStatus.SUCCEEDED.value
            await session.refresh(workload)
            await session.refresh(assignment)
            assert workload.is_present is False
            assert workload.missing_since is not None
            assert workload.organization_id == organization.id
            assert assignment.revoked_at is None
            finding = await session.scalar(
                select(ReconciliationFinding).where(
                    ReconciliationFinding.workload_id == workload.id,
                    ReconciliationFinding.kind == FindingKind.EXTERNAL_DELETE.value,
                )
            )
            assert finding is not None
            assert finding.status == FindingStatus.OPEN.value
            assert finding.severity == "CRITICAL"
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_partial_guest_failure_never_tombstones_workload() -> None:
    settings = _settings()
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            app.state.test_session = session
            cluster = Cluster(
                id=uuid4(),
                name="partial-cluster",
                api_base_url="https://partial.example.test:8006",
                is_active=True,
            )
            workload = Workload(
                id=uuid4(),
                cluster_id=cluster.id,
                vmid=301,
                node="pve-a",
                kind="QEMU",
                name="preserved-vm",
                power_state="RUNNING",
                is_template=False,
                is_present=True,
                observed_at=datetime.now(UTC),
            )
            session.add_all([cluster, workload])
            await session.commit()
            snapshot = _snapshot(None, errors={"guests": "PVE_TIMEOUT"})
            run = await (await _runner(app, settings, snapshot)).run(cluster.id)
            assert run is not None
            assert run.status == RunStatus.PARTIAL.value
            assert run.partial_failure is True
            await session.refresh(workload)
            assert workload.is_present is True
            assert workload.missing_since is None
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_reappearing_workload_records_node_spec_and_power_drift() -> None:
    settings = _settings()
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            app.state.test_session = session
            cluster = Cluster(
                id=uuid4(),
                name="drift-cluster",
                api_base_url="https://drift.example.test:8006",
                is_active=True,
            )
            workload = Workload(
                id=uuid4(),
                cluster_id=cluster.id,
                vmid=401,
                node="pve-old",
                kind="QEMU",
                name="old-name",
                power_state="STOPPED",
                cpu_cores=2,
                memory_bytes=1024,
                disk_bytes=2048,
                is_template=False,
                is_present=True,
                observed_at=datetime.now(UTC),
            )
            session.add_all([cluster, workload])
            await session.commit()
            await (await _runner(app, settings, _snapshot([]))).run(cluster.id)
            changed = _snapshot(
                [
                    {
                        "vmid": 401,
                        "node": "pve-a",
                        "type": "qemu",
                        "name": "new-name",
                        "status": "running",
                        "maxcpu": 4,
                        "maxmem": 4096,
                        "maxdisk": 8192,
                    }
                ]
            )
            run = await (await _runner(app, settings, changed)).run(cluster.id)
            assert run is not None and run.status == RunStatus.SUCCEEDED.value
            await session.refresh(workload)
            assert workload.is_present is True
            assert workload.node == "pve-a"
            assert workload.cpu_cores == 4
            findings = (
                await session.scalars(
                    select(ReconciliationFinding).where(
                        ReconciliationFinding.workload_id == workload.id
                    )
                )
            ).all()
            by_kind = {item.kind: item for item in findings}
            assert by_kind[FindingKind.EXTERNAL_DELETE.value].status == "RESOLVED"
            assert FindingKind.NODE_MOVED.value in by_kind
            assert FindingKind.SPEC_DRIFT.value in by_kind
            assert FindingKind.POWER_STATE_DRIFT.value in by_kind
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_operator_can_acknowledge_and_resolve_finding() -> None:
    settings = _settings()
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            user = User(
                id=uuid4(),
                email="reconcile-operator@example.test",
                display_name="Reconcile Operator",
                role=UserRole.OPERATOR.value,
                password_hash=PasswordManager().hash(token_urlsafe(24)),
                is_active=True,
            )
            cluster = Cluster(
                id=uuid4(),
                name="finding-cluster",
                api_base_url="https://finding.example.test:8006",
                is_active=True,
            )
            session.add_all([user, cluster])
            await session.commit()
            now = datetime.now(UTC)
            finding = ReconciliationFinding(
                fingerprint=f"cluster:{cluster.id}:test",
                kind=FindingKind.SPEC_DRIFT.value,
                severity="WARNING",
                status="OPEN",
                cluster_id=cluster.id,
                target_type="cluster",
                target_id=cluster.id,
                summary="Test finding",
                details={},
                first_observed_at=now,
                last_observed_at=now,
            )
            session.add(finding)
            await session.commit()
            service = ReconciliationService(
                session=session,
                settings=settings,
                principal=Principal(
                    user_id=user.id,
                    email=user.email,
                    role=UserRole.OPERATOR,
                    session_epoch=0,
                ),
                publisher=lambda _run_id: None,
                request_id="reconciliation-test",
            )
            acknowledged = await service.acknowledge(finding.id, assigned_to_id=user.id)
            assert acknowledged.status == "ACKNOWLEDGED"
            assert acknowledged.assigned_to_id == user.id
            resolved = await service.resolve(finding.id, resolution_note="Verified in Proxmox")
            assert resolved.status == "RESOLVED"
            assert resolved.resolved_by_id == user.id
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_duplicate_full_sync_request_reuses_active_run() -> None:
    settings = _settings()
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            cluster = Cluster(
                id=uuid4(),
                name="duplicate-sync-cluster",
                api_base_url="https://duplicate-sync.example.test:8006",
                is_active=True,
            )
            session.add(cluster)
            await session.commit()

            first, first_created = await create_sync_run(
                session,
                cluster_id=cluster.id,
                triggered_by="admin",
            )
            second, second_created = await create_sync_run(
                session,
                cluster_id=cluster.id,
                triggered_by="scheduler",
            )

            assert first_created is True
            assert second_created is False
            assert second.id == first.id
            assert second.generation == first.generation
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()
