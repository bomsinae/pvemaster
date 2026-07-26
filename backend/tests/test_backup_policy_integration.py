import os
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.db import create_engine, create_session_factory
from app.models.auth import AuditLog, User, UserRole
from app.models.backup import (
    BackupPolicy,
    BackupPolicyAssignment,
    BackupRun,
    BackupTarget,
    BackupVerification,
)
from app.models.cluster import Cluster
from app.models.operation import Operation, OperationEvent, Workload
from app.models.scheduling import OperationOutbox
from app.schemas.backup import BackupPolicyAssignmentRequest, BackupPolicyCreate
from app.security.access import Principal
from app.security.credentials import CredentialCipher
from app.services.backup_policies import BackupPolicyService

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr(os.environ.get("AUTH_TEST_REDIS_URL", "redis://localhost/15")),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )


async def _clear(session) -> None:
    await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
    for model in (
        BackupVerification,
        AuditLog,
        OperationEvent,
        OperationOutbox,
        BackupRun,
        BackupPolicyAssignment,
        BackupPolicy,
        Operation,
        BackupTarget,
        Workload,
        Cluster,
        User,
    ):
        await session.execute(delete(model))
    await session.commit()


async def test_policy_dispatch_is_timezone_aware_idempotent_and_skippable() -> None:
    settings = _settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    published: list[UUID] = []

    def publish(operation_id: UUID, _: str) -> None:
        published.append(operation_id)

    try:
        async with factory() as session:
            await _clear(session)
            admin = User(
                email="backup-policy-admin@example.test",
                display_name="Backup Policy Admin",
                role=UserRole.SUPER_ADMIN.value,
                password_hash="unused",
                is_active=True,
            )
            cluster = Cluster(
                name="backup-policy-cluster",
                api_base_url="https://8.8.8.8:8006",
                is_active=True,
                version=1,
            )
            session.add_all([admin, cluster])
            await session.flush()
            workload = Workload(
                cluster_id=cluster.id,
                vmid=701,
                node="pve-a",
                kind="QEMU",
                name="policy-vm",
                power_state="RUNNING",
                is_template=False,
                is_present=True,
                observed_at=datetime.now(UTC),
                version=1,
            )
            target = BackupTarget(
                cluster_id=cluster.id,
                storage_id="pbs-policy",
                is_enabled=True,
                last_observed_available=True,
                created_by_id=admin.id,
            )
            session.add_all([workload, target])
            await session.commit()
            principal = Principal(
                user_id=admin.id,
                email=admin.email,
                role=UserRole.SUPER_ADMIN,
                session_epoch=admin.session_epoch,
            )
            service = BackupPolicyService(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                principal=principal,
                publisher=publish,
                restore_publisher=publish,
                request_id="policy-test",
                source_ip="127.0.0.1",
            )
            created = await service.create_policy(
                BackupPolicyCreate(
                    name="daily-seoul",
                    backup_target_id=target.id,
                    schedule="0 2 * * *",
                    timezone="Asia/Seoul",
                    retention_reference="daily-30",
                    assignments=[
                        BackupPolicyAssignmentRequest(workload_id=workload.id)
                    ],
                )
            )
            policy = await session.get(BackupPolicy, created.id)
            assert policy is not None
            due = datetime.now(UTC) - timedelta(minutes=1)
            policy.next_run_at = due
            await session.commit()

            scheduler = BackupPolicyService(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                publisher=publish,
                restore_publisher=publish,
            )
            assert await scheduler.dispatch_due(now=datetime.now(UTC)) == 1
            assert len(published) == 1
            run = await session.scalar(select(BackupRun))
            assert run is not None
            assert run.trigger_type == "SCHEDULED"
            assert run.scheduled_for == due
            assert await scheduler.dispatch_due(now=datetime.now(UTC)) == 0
            assert len(published) == 1

            policy = await session.get(BackupPolicy, created.id)
            assert policy is not None
            policy.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
            policy.skip_next_at = policy.next_run_at
            await session.commit()
            assert await scheduler.dispatch_due(now=datetime.now(UTC)) == 0
            assert len(published) == 1
    finally:
        async with factory() as session:
            await _clear(session)
        await engine.dispose()
