from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import AuditLog, UserRole
from app.schemas.observability import QueueStatus, WorkerStatus
from app.security.access import Principal
from app.services.audit import add_audit_event, sanitize_audit_value
from app.services.observability import ObservabilityService


class CapturingSession:
    def __init__(self) -> None:
        self.item: AuditLog | None = None

    def add(self, item: AuditLog) -> None:
        self.item = item


class ScalarSequenceSession:
    def __init__(self, values: list[int]) -> None:
        self.values = values

    async def scalar(self, _statement: object) -> int:
        return self.values.pop(0)


class InventoryAlertService(ObservabilityService):
    async def _ip_pool_counts(self) -> list[tuple[UUID, str, int]]:
        return []


def test_audit_values_mask_sensitive_fields_recursively() -> None:
    raw = {
        "name": "cluster-a",
        "password": "do-not-store",
        "nested": {
            "token_secret": "do-not-store",
            "Authorization": "Bearer do-not-store",
            "safe": "visible",
        },
    }

    assert sanitize_audit_value(raw) == {
        "name": "cluster-a",
        "password": "[REDACTED]",
        "nested": {
            "token_secret": "[REDACTED]",
            "Authorization": "[REDACTED]",
            "safe": "visible",
        },
    }


def test_add_audit_event_stores_required_fields_without_secrets() -> None:
    session = CapturingSession()
    actor_id = uuid4()
    add_audit_event(
        cast(AsyncSession, session),
        action="CLUSTER_API_TOKEN_CHANGED",
        outcome="SUCCEEDED",
        request_id="audit-request",
        actor_user_id=actor_id,
        actor_role=UserRole.SUPER_ADMIN,
        source_ip="192.0.2.10",
        user_agent="test-agent",
        target_type="cluster",
        target_id=uuid4(),
        before={"token_secret": "old-secret"},
        after={"token_secret": "new-secret", "name": "cluster-a"},
    )

    item = session.item
    assert item is not None
    assert item.actor_user_id == actor_id
    assert item.actor_role == UserRole.SUPER_ADMIN.value
    assert item.source_ip == "192.0.2.10"
    assert item.user_agent == "test-agent"
    assert item.request_id == "audit-request"
    assert item.result == "SUCCEEDED"
    assert item.before == {"token_secret": "[REDACTED]"}
    assert item.after == {"token_secret": "[REDACTED]", "name": "cluster-a"}
    assert "old-secret" not in str(item.before)
    assert "new-secret" not in str(item.after)


def test_audit_response_includes_safe_actor_and_workload_display_values() -> None:
    item = AuditLog(
        id=uuid4(),
        action="POWER_START",
        outcome="SUCCEEDED",
        result="SUCCEEDED",
        created_at=datetime.now(UTC),
    )

    response = ObservabilityService._audit_response(
        item,
        "Kim Customer",
        "customer@example.test",
        "web-01",
        101,
        "QEMU",
        "pve-a",
        "seoul-pve",
    )

    assert response.actor_display_name == "Kim Customer"
    assert response.actor_email == "customer@example.test"
    assert response.workload_name == "web-01"
    assert response.workload_vmid == 101
    assert response.workload_cluster_name == "seoul-pve"


def test_observability_routes_are_registered(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/health/live" in paths
    assert "/api/v1/admin/operations/status" in paths
    assert "/api/v1/admin/audit-logs" in paths
    assert "/api/v1/admin/audit-logs/{audit_id}" in paths
    assert not any(
        method in paths["/api/v1/admin/audit-logs"] for method in ("post", "patch", "delete")
    )


async def test_customer_cannot_access_operational_status(settings: Settings) -> None:
    service = ObservabilityService(
        session=cast(AsyncSession, object()),
        redis=cast(Redis, object()),
        settings=settings,
        principal=Principal(
            user_id=uuid4(),
            email="customer@example.test",
            role=UserRole.CUSTOMER,
            session_epoch=0,
        ),
    )

    with pytest.raises(AppError) as captured:
        await service.status()
    assert captured.value.status_code == 403


async def test_operational_status_only_queries_active_clusters(settings: Settings) -> None:
    session = AsyncMock(spec=AsyncSession)
    scalar_result = MagicMock()
    scalar_result.all.return_value = []
    session.scalars.return_value = scalar_result
    service = ObservabilityService(
        session=cast(AsyncSession, session),
        redis=cast(Redis, object()),
        settings=settings,
    )

    assert await service._cluster_statuses() == []

    statement = session.scalars.await_args.args[0]
    assert "clusters.is_active IS true" in str(statement)


async def test_unassigned_workloads_are_inventory_not_operational_alerts(
    settings: Settings,
) -> None:
    inventory_service = InventoryAlertService(
        session=cast(AsyncSession, ScalarSequenceSession([5, 2])),
        redis=cast(Redis, object()),
        settings=settings,
    )
    alert_service = InventoryAlertService(
        session=cast(AsyncSession, ScalarSequenceSession([0, 3])),
        redis=cast(Redis, object()),
        settings=settings,
    )

    inventory = await inventory_service._workload_inventory()
    alerts = await alert_service._alerts(
        WorkerStatus(alive=True, workers=["worker-1"], stale_after_seconds=60),
        QueueStatus(total=0, queues={}, backlog_threshold=100),
        [],
    )

    assert inventory.total == 5
    assert inventory.assigned == 2
    assert inventory.unassigned == 3
    assert not any(alert.code == "ORPHANED_VM_FOUND" for alert in alerts)


def test_sparse_ip_pool_availability_counts_cidr_without_double_counting() -> None:
    available = ObservabilityService._available_address_count(
        cidr="192.0.2.0/29",
        gateway="192.0.2.1",
        exclusions=[("192.0.2.2", "192.0.2.3")],
        unavailable=["192.0.2.3", "192.0.2.4", "192.0.2.5"],
    )
    assert available == 1

    ipv6_available = ObservabilityService._available_address_count(
        cidr="2001:db8::/126",
        gateway="2001:db8::1",
        exclusions=[("2001:db8::2", "2001:db8::2")],
        unavailable=["2001:db8::2"],
    )
    assert ipv6_available == 1
