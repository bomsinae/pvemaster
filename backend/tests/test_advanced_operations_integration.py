import os
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.models.advanced_operations import AdvancedOperationTarget
from app.models.auth import User, UserRole
from app.models.cluster import Cluster
from app.models.operation import Operation, OperationStatus, Workload
from app.schemas.advanced_operations import (
    AdvancedFeature,
    AdvancedOperationCreate,
    AdvancedPreviewRequest,
)
from app.security.access import Principal
from app.security.credentials import CredentialCipher
from app.services.advanced_operation_runner import AdvancedOperationRunner
from app.services.advanced_operations import AdvancedOperationService

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


class FakeAdvancedApi:
    def __init__(self, *, timeout_snapshot: bool = False) -> None:
        self.timeout_snapshot = timeout_snapshot
        self.power_targets: list[int] = []

    async def submit_guest_power_action(
        self, *, kind: str, node: str, vmid: int, action: str
    ) -> str:
        assert kind in {"QEMU", "LXC"}
        assert node == "pve-a"
        assert action == "start"
        self.power_targets.append(vmid)
        return f"UPID:power:{vmid}"

    async def submit_guest_snapshot(
        self,
        *,
        kind: str,
        node: str,
        vmid: int,
        snapshot_name: str,
        include_memory: bool,
    ) -> str:
        del kind, node, vmid, snapshot_name, include_memory
        if self.timeout_snapshot:
            raise AppError(504, "PVE_TIMEOUT", "The request timed out.")
        return "UPID:snapshot"

    async def delete_guest_snapshot(
        self, *, kind: str, node: str, vmid: int, snapshot_name: str
    ) -> str:
        del kind, node, vmid, snapshot_name
        return "UPID:snapshot-delete"

    async def rollback_guest_snapshot(
        self, *, kind: str, node: str, vmid: int, snapshot_name: str
    ) -> str:
        del kind, node, vmid, snapshot_name
        return "UPID:snapshot-rollback"

    async def migrate_guest(
        self,
        *,
        kind: str,
        node: str,
        vmid: int,
        target_node: str,
        online: bool,
        target_storage: str | None,
        target_network: str | None,
    ) -> str:
        del kind, node, vmid, target_node, online, target_storage, target_network
        return "UPID:migrate"

    async def update_ha_resource(self, *, resource_id: str, state: str, group: str | None) -> None:
        del resource_id, state, group

    async def configure_guest_advanced(
        self, *, kind: str, node: str, vmid: int, values: dict[str, str]
    ) -> None:
        del kind, node, vmid, values

    async def get_task_status(self, *, node: str, upid: str) -> dict[str, str]:
        assert node == "pve-a"
        assert upid.startswith("UPID:")
        return {"status": "stopped", "exitstatus": "OK"}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        advanced_snapshot_enabled=True,
        advanced_migration_enabled=True,
        advanced_ha_enabled=True,
        advanced_node_maintenance_enabled=True,
        advanced_bulk_enabled=True,
        advanced_guest_config_enabled=True,
        advanced_firewall_sdn_enabled=True,
        pve_task_poll_interval_seconds=0.001,
        pve_task_timeout_seconds=10,
        pve_task_max_poll_attempts=2,
    )


async def test_advanced_preview_conflict_execution_and_ambiguous_timeout() -> None:
    settings = _settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(text("TRUNCATE users, organizations, clusters CASCADE"))
            admin = User(
                email="advanced-admin@example.test",
                display_name="Advanced Admin",
                role=UserRole.SUPER_ADMIN.value,
                password_hash="unused",
                is_active=True,
            )
            operator = User(
                email="advanced-operator@example.test",
                display_name="Advanced Operator",
                role=UserRole.OPERATOR.value,
                password_hash="unused",
                is_active=True,
            )
            cluster = Cluster(
                name="advanced-cluster",
                api_base_url="https://advanced.example.test:8006",
                is_active=True,
            )
            session.add_all([admin, operator, cluster])
            await session.flush()
            workloads = [
                Workload(
                    cluster_id=cluster.id,
                    vmid=501 + index,
                    node="pve-a",
                    kind="QEMU" if index == 0 else "LXC",
                    name=f"advanced-{index}",
                    power_state="STOPPED",
                    is_template=False,
                    is_present=True,
                    observed_at=datetime.now(UTC),
                    version=1,
                )
                for index in range(2)
            ]
            session.add_all(workloads)
            await session.commit()

            published: list[tuple[UUID, str]] = []
            principal = Principal(
                user_id=admin.id,
                email=admin.email,
                role=UserRole.SUPER_ADMIN,
                session_epoch=0,
            )
            service = AdvancedOperationService(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                principal=principal,
                publisher=lambda operation_id, task_id: published.append((operation_id, task_id)),
                request_id="advanced-test",
                source_ip="127.0.0.1",
            )
            operator_service = AdvancedOperationService(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                principal=Principal(
                    user_id=operator.id,
                    email=operator.email,
                    role=UserRole.OPERATOR,
                    session_epoch=0,
                ),
                publisher=lambda _operation_id, _task_id: None,
                request_id="advanced-operator-test",
                source_ip="127.0.0.1",
            )
            capabilities = service.capabilities()
            assert all(item.enabled for item in capabilities.items)
            assert (
                next(item for item in capabilities.items if item.feature == "FIREWALL_SDN").mode
                == "READ_ONLY"
            )

            blocked = await service.preview(
                AdvancedPreviewRequest(
                    feature=AdvancedFeature.MIGRATION,
                    action="LIVE",
                    workload_ids=[workloads[0].id],
                    options={"target_node": "pve-b"},
                )
            )
            assert "LOCAL_DISK_COMPATIBILITY_UNCONFIRMED" in blocked.blockers
            assert not blocked.executable

            bulk_request = AdvancedPreviewRequest(
                feature=AdvancedFeature.BULK,
                action="START",
                workload_ids=[item.id for item in workloads],
            )
            bulk_preview = await service.preview(bulk_request)
            assert bulk_preview.required_confirmation == "2 TARGETS"
            with pytest.raises(AppError) as denied:
                await operator_service.create(
                    AdvancedOperationCreate(
                        preview=bulk_request,
                        confirmation=bulk_preview.required_confirmation,
                    ),
                    idempotency_key="operator-cannot-execute",
                )
            assert denied.value.code == "FORBIDDEN"
            bulk, created = await service.create(
                AdvancedOperationCreate(
                    preview=bulk_request,
                    confirmation=bulk_preview.required_confirmation,
                ),
                idempotency_key="advanced-bulk-001",
            )
            assert created
            assert len(published) == 1

            snapshot_request = AdvancedPreviewRequest(
                feature=AdvancedFeature.SNAPSHOT,
                action="CREATE",
                workload_ids=[workloads[1].id],
                options={"snapshot_name": "before-change", "include_memory": False},
            )
            snapshot_preview = await service.preview(snapshot_request)
            with pytest.raises(AppError) as conflict:
                await service.create(
                    AdvancedOperationCreate(
                        preview=snapshot_request,
                        confirmation=snapshot_preview.required_confirmation,
                    ),
                    idempotency_key="advanced-conflict-001",
                )
            assert conflict.value.code == "OPERATION_CONFLICT"

            fake = FakeAdvancedApi()
            await AdvancedOperationRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                client=fake,
                sleep=lambda _: _no_sleep(),
            ).run(bulk.operation_id)
            completed = await service.get(bulk.operation_id)
            assert completed.status == OperationStatus.SUCCEEDED.value
            assert fake.power_targets == [501, 502]
            active_targets = await session.scalars(
                select(AdvancedOperationTarget).where(
                    AdvancedOperationTarget.operation_id == bulk.operation_id
                )
            )
            assert all(not item.active for item in active_targets)

            timeout_preview = await service.preview(
                AdvancedPreviewRequest(
                    feature=AdvancedFeature.SNAPSHOT,
                    action="CREATE",
                    workload_ids=[workloads[0].id],
                    options={"snapshot_name": "timeout-check", "include_memory": False},
                )
            )
            timeout_operation, _ = await service.create(
                AdvancedOperationCreate(
                    preview=AdvancedPreviewRequest(
                        feature=AdvancedFeature.SNAPSHOT,
                        action="CREATE",
                        workload_ids=[workloads[0].id],
                        options={"snapshot_name": "timeout-check", "include_memory": False},
                    ),
                    confirmation=timeout_preview.required_confirmation,
                ),
                idempotency_key="advanced-timeout-001",
            )
            await AdvancedOperationRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
                client=FakeAdvancedApi(timeout_snapshot=True),
            ).run(timeout_operation.operation_id)
            timed_out = await session.get(Operation, timeout_operation.operation_id)
            assert timed_out is not None
            assert timed_out.status == OperationStatus.NEEDS_ATTENTION.value
            assert timed_out.error_code == "PVE_TIMEOUT"
    finally:
        async with factory() as session:
            await session.execute(text("TRUNCATE users, organizations, clusters CASCADE"))
            await session.commit()
        await engine.dispose()


async def _no_sleep() -> None:
    return None
