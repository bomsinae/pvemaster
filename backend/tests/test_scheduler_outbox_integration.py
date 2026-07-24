import os
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.main import create_app
from app.models.auth import AuditLog, LoginThrottle, Organization, RefreshToken, User, UserRole
from app.models.backup import BackupRun, BackupTarget
from app.models.cluster import Cluster, ClusterCredential
from app.models.ipam import (
    IpAddress,
    IpAddressState,
    IpAllocation,
    IpAllocationKind,
    IpAllocationStatus,
    IpPool,
)
from app.models.operation import Operation, PowerAction, PveTask, Workload, WorkloadAssignment
from app.models.scheduling import (
    MaintenanceRun,
    OperationOutbox,
    OutboxStatus,
    SchedulerLease,
    SyncRun,
)
from app.security.access import Principal
from app.security.credentials import CredentialCipher
from app.security.passwords import PasswordManager
from app.services.backup_metadata import BackupMetadataReconciler
from app.services.inventory_sync import ScheduledInventorySyncRunner
from app.services.maintenance import (
    acquire_lease,
    operation_watchdog_callback,
    release_expired_ip_quarantine,
    release_lease,
)
from app.services.operations import OperationService
from app.services.outbox import POWER_EVENT, dispatch_due_events

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
            OperationOutbox,
            BackupRun,
            Operation,
            WorkloadAssignment,
            IpAllocation,
            IpAddress,
            IpPool,
            Workload,
            SyncRun,
            BackupTarget,
            ClusterCredential,
            Cluster,
            RefreshToken,
            Organization,
            LoginThrottle,
            User,
            MaintenanceRun,
            SchedulerLease,
        ):
            await session.execute(delete(model))
        await session.commit()


async def test_publish_failure_is_recovered_from_transactional_outbox() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        outbox_max_backoff_seconds=5,
    )
    app = create_app(settings)
    await _clear(app)
    passwords = PasswordManager()
    try:
        async with app.state.db_session_factory() as session:
            user = User(
                id=uuid4(),
                email="outbox-operator@example.test",
                display_name="Outbox Operator",
                role=UserRole.OPERATOR.value,
                password_hash=passwords.hash(token_urlsafe(24)),
                is_active=True,
            )
            cluster = Cluster(
                id=uuid4(),
                name="outbox-cluster",
                api_base_url="https://outbox-pve.example.test:8006",
                is_active=True,
            )
            workload = Workload(
                id=uuid4(),
                cluster_id=cluster.id,
                vmid=901,
                node="pve-a",
                kind="QEMU",
                name="outbox-vm",
                power_state="STOPPED",
                is_template=False,
                is_present=True,
                observed_at=datetime.now(UTC),
            )
            session.add_all([user, cluster, workload])
            await session.commit()

            def unavailable(_operation_id: object, _task_id: str) -> None:
                raise ConnectionError("redis unavailable")

            service = OperationService(
                session=session,
                settings=settings,
                principal=Principal(
                    user_id=user.id,
                    email=user.email,
                    role=UserRole.OPERATOR,
                    session_epoch=0,
                ),
                publisher=unavailable,
                request_id="outbox-test",
                source_ip="192.0.2.10",
            )
            response, created = await service.request_power_action(
                workload_id=workload.id,
                action=PowerAction.START,
                idempotency_key=token_urlsafe(18),
                reason=None,
            )
            assert created
            event = await session.scalar(
                select(OperationOutbox).where(OperationOutbox.operation_id == response.id)
            )
            assert event is not None
            assert event.status == OutboxStatus.PENDING.value
            assert event.last_error_code == "BROKER_UNAVAILABLE"

            published: list[tuple[object, str]] = []
            dispatched, failed = await dispatch_due_events(
                session,
                settings,
                {
                    POWER_EVENT: lambda operation_id, task_id: published.append(
                        (operation_id, task_id)
                    )
                },
                now=event.next_attempt_at + timedelta(seconds=1),
            )
            assert (dispatched, failed) == (1, 0)
            assert published[0][0] == response.id
            await session.refresh(event)
            assert event.status == OutboxStatus.PUBLISHED.value
            assert event.published_at is not None
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_watchdog_rearms_stale_operation_without_creating_a_second_intent() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        operation_watchdog_seconds=30,
    )
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            user = User(
                id=uuid4(),
                email="watchdog@example.test",
                display_name="Watchdog",
                role=UserRole.OPERATOR.value,
                password_hash=PasswordManager().hash(token_urlsafe(24)),
                is_active=True,
            )
            cluster = Cluster(
                id=uuid4(),
                name="watchdog-cluster",
                api_base_url="https://watchdog-pve.example.test:8006",
                is_active=True,
            )
            workload = Workload(
                id=uuid4(),
                cluster_id=cluster.id,
                vmid=902,
                node="pve-a",
                kind="QEMU",
                name="watchdog-vm",
                power_state="STOPPED",
                is_template=False,
                is_present=True,
                observed_at=datetime.now(UTC),
            )
            operation = Operation(
                id=uuid4(),
                operation_type="POWER_START",
                action="start",
                status="QUEUED",
                requested_by_id=user.id,
                source_ip="192.0.2.11",
                cluster_id=cluster.id,
                workload_id=workload.id,
                idempotency_key_hash=os.urandom(32),
                request_fingerprint=os.urandom(32),
                celery_task_id=str(uuid4()),
                result={},
                requested_at=datetime.now(UTC) - timedelta(minutes=2),
            )
            event = OperationOutbox(
                operation_id=operation.id,
                event_type=POWER_EVENT,
                payload={"operation_id": str(operation.id)},
                status=OutboxStatus.PUBLISHED.value,
                attempt_count=1,
                next_attempt_at=datetime.now(UTC),
                published_at=datetime.now(UTC) - timedelta(minutes=2),
            )
            session.add_all([user, cluster, workload])
            await session.commit()
            session.add(operation)
            await session.flush()
            session.add(event)
            await session.commit()

            processed = await operation_watchdog_callback(settings)(session)
            assert processed == 1
            await session.refresh(event)
            assert event.status == OutboxStatus.PENDING.value
            assert event.published_at is None
            assert event.last_error_code == "WATCHDOG_REDELIVERY"
            operation_count = await session.scalar(
                select(Operation).where(Operation.id == operation.id)
            )
            assert operation_count is not None
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_expired_ip_quarantine_moves_address_and_allocation_atomically() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            user = User(
                id=uuid4(),
                email="ip-maintenance@example.test",
                display_name="IP Maintenance",
                role=UserRole.SUPER_ADMIN.value,
                password_hash=PasswordManager().hash(token_urlsafe(24)),
                is_active=True,
            )
            pool = IpPool(
                id=uuid4(),
                name="maintenance-pool",
                cidr="192.0.2.0/29",
                gateway="192.0.2.1",
                dns_servers=[],
                bridge="vmbr0",
                ip_family=4,
                allocation_strategy="SEQUENTIAL",
                quarantine_seconds=60,
                is_active=True,
                created_by_id=user.id,
            )
            address = IpAddress(
                id=uuid4(),
                pool_id=pool.id,
                address="192.0.2.2",
                state=IpAddressState.QUARANTINED.value,
                quarantined_until=datetime.now(UTC) - timedelta(seconds=1),
            )
            allocation = IpAllocation(
                id=uuid4(),
                ip_address_id=address.id,
                kind=IpAllocationKind.MANUAL.value,
                status=IpAllocationStatus.QUARANTINED.value,
                allocated_by_id=user.id,
            )
            session.add(user)
            await session.flush()
            session.add(pool)
            await session.flush()
            session.add(address)
            await session.flush()
            session.add(allocation)
            await session.commit()

            assert await release_expired_ip_quarantine(session) == 1
            await session.refresh(address)
            await session.refresh(allocation)
            assert address.state == IpAddressState.AVAILABLE.value
            assert address.quarantined_until is None
            assert allocation.status == IpAllocationStatus.RELEASED.value
            assert allocation.released_at is not None
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_scheduled_inventory_sync_uses_cluster_lease_and_records_generation() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            cluster = Cluster(
                id=uuid4(),
                name="scheduled-sync-cluster",
                api_base_url="https://scheduled-sync.example.test:8006",
                is_active=True,
            )
            session.add(cluster)
            await session.commit()

            async def load_guests(_cluster_id: UUID) -> list[dict[str, object]]:
                return [
                    {
                        "vmid": 903,
                        "node": "pve-a",
                        "type": "qemu",
                        "name": "scheduled-vm",
                        "status": "running",
                        "maxcpu": 4,
                        "maxmem": 8 * 1024**3,
                        "maxdisk": 40 * 1024**3,
                        "template": 0,
                    }
                ]

            runner = ScheduledInventorySyncRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                guest_loader=load_guests,
            )
            run = await runner.run(cluster.id)
            assert run is not None
            assert run.status == "SUCCEEDED"
            assert run.generation == 1
            assert run.resource_counts == {
                "discovered": 1,
                "created": 1,
                "updated": 0,
            }
            workload = await session.scalar(
                select(Workload).where(
                    Workload.cluster_id == cluster.id,
                    Workload.vmid == 903,
                )
            )
            assert workload is not None
            assert workload.power_state == "RUNNING"
            assert workload.cpu_cores == 4
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_backup_snapshot_metadata_is_reconciled_without_repeating_backup() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    await _clear(app)
    try:
        async with app.state.db_session_factory() as session:
            user = User(
                id=uuid4(),
                email="metadata@example.test",
                display_name="Metadata Reconciler",
                role=UserRole.OPERATOR.value,
                password_hash=PasswordManager().hash(token_urlsafe(24)),
                is_active=True,
            )
            cluster = Cluster(
                id=uuid4(),
                name="metadata-cluster",
                api_base_url="https://metadata-pve.example.test:8006",
                is_active=True,
            )
            workload = Workload(
                id=uuid4(),
                cluster_id=cluster.id,
                vmid=904,
                node="pve-a",
                kind="QEMU",
                name="metadata-vm",
                power_state="STOPPED",
                is_template=False,
                is_present=True,
                observed_at=datetime.now(UTC),
            )
            session.add_all([user, cluster, workload])
            await session.commit()
            target = BackupTarget(
                id=uuid4(),
                cluster_id=cluster.id,
                storage_id="pbs-main",
                is_enabled=True,
                created_by_id=user.id,
            )
            operation = Operation(
                id=uuid4(),
                operation_type="WORKLOAD_BACKUP",
                action="backup",
                status="SUCCEEDED",
                requested_by_id=user.id,
                source_ip="192.0.2.12",
                cluster_id=cluster.id,
                workload_id=workload.id,
                idempotency_key_hash=os.urandom(32),
                request_fingerprint=os.urandom(32),
                celery_task_id=str(uuid4()),
                result={"metadata_pending": True},
                finished_at=datetime.now(UTC),
            )
            session.add_all([target, operation])
            await session.flush()
            run = BackupRun(
                id=uuid4(),
                operation_id=operation.id,
                backup_target_id=target.id,
                workload_id=workload.id,
                mode="snapshot",
                compression="zstd",
                status="SUCCEEDED",
                finished_at=datetime.now(UTC),
            )
            session.add(run)
            await session.commit()

            async def load_content(_run_id: UUID) -> list[dict[str, object]]:
                return [
                    {
                        "volid": "pbs-main:backup/vm/904/2026-07-24T00:00:00Z",
                        "content": "backup",
                        "vmid": 904,
                        "ctime": 1_774_483_200,
                        "size": 12_345,
                    }
                ]

            reconciler = BackupMetadataReconciler(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                content_loader=load_content,
            )
            assert await reconciler.reconcile() == 1
            await session.refresh(run)
            await session.refresh(operation)
            assert run.snapshot_volume_id is not None
            assert run.size_bytes == 12_345
            assert operation.result["metadata_pending"] is False
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()


async def test_scheduler_lease_rejects_overlap_and_increments_fencing_token() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    await _clear(app)
    now = datetime.now(UTC)
    owner_a = uuid4()
    owner_b = uuid4()
    try:
        async with app.state.db_session_factory() as session:
            first = await acquire_lease(
                session,
                name="test-scheduler-job",
                owner_id=owner_a,
                ttl_seconds=30,
                now=now,
            )
            assert first is not None
            assert first.fencing_token == 1

        async with app.state.db_session_factory() as session:
            overlap = await acquire_lease(
                session,
                name="test-scheduler-job",
                owner_id=owner_b,
                ttl_seconds=30,
                now=now + timedelta(seconds=1),
            )
            assert overlap is None

        async with app.state.db_session_factory() as session:
            takeover = await acquire_lease(
                session,
                name="test-scheduler-job",
                owner_id=owner_b,
                ttl_seconds=30,
                now=now + timedelta(seconds=31),
            )
            assert takeover is not None
            assert takeover.fencing_token == 2
            await release_lease(session, takeover)
    finally:
        await _clear(app)
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()
