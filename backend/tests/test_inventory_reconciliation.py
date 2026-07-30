from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import UserRole
from app.models.operation import Workload
from app.security.access import Principal
from app.services.customer_portal import CustomerPortalService
from app.services.reconciliation import ReconciliationService


async def test_customer_cannot_construct_reconciliation_service(settings: Settings) -> None:
    with pytest.raises(AppError) as error:
        ReconciliationService(
            session=cast(AsyncSession, AsyncMock(spec=AsyncSession)),
            settings=settings,
            principal=Principal(
                user_id=uuid4(),
                email="customer@example.test",
                role=UserRole.CUSTOMER,
                session_epoch=0,
            ),
            publisher=lambda _run_id: None,
            request_id=token_urlsafe(12),
        )

    assert error.value.status_code == 403
    assert error.value.code == "FORBIDDEN"


def test_customer_power_action_is_blocked_when_inventory_is_stale(settings: Settings) -> None:
    service = CustomerPortalService(
        session=cast(AsyncSession, AsyncMock(spec=AsyncSession)),
        settings=settings,
        principal=Principal(
            user_id=uuid4(),
            email="customer@example.test",
            role=UserRole.CUSTOMER,
            session_epoch=0,
        ),
        publisher=lambda _operation_id, _task_id: None,
        request_id=token_urlsafe(12),
        source_ip="192.0.2.1",
    )
    workload = Workload(
        id=uuid4(),
        cluster_id=uuid4(),
        vmid=501,
        node="pve-a",
        kind="QEMU",
        name="stale-vm",
        power_state="RUNNING",
        is_template=False,
        is_present=True,
        observed_at=datetime.now(UTC)
        - timedelta(seconds=settings.inventory_stale_after_seconds + 1),
    )

    with pytest.raises(AppError) as error:
        service._require_fresh_inventory(workload, sync_interval_seconds=60)

    assert error.value.status_code == 503
    assert error.value.code == "INVENTORY_STALE"


def test_customer_stale_threshold_respects_cluster_sync_interval(settings: Settings) -> None:
    service = CustomerPortalService(
        session=cast(AsyncSession, AsyncMock(spec=AsyncSession)),
        settings=settings,
        principal=Principal(
            user_id=uuid4(),
            email="customer@example.test",
            role=UserRole.CUSTOMER,
            session_epoch=0,
        ),
        publisher=lambda _operation_id, _task_id: None,
        request_id=token_urlsafe(12),
        source_ip="192.0.2.1",
    )
    workload = Workload(
        id=uuid4(),
        cluster_id=uuid4(),
        vmid=502,
        node="pve-a",
        kind="QEMU",
        name="slow-sync-vm",
        power_state="RUNNING",
        is_template=False,
        is_present=True,
        observed_at=datetime.now(UTC) - timedelta(seconds=600),
    )

    assert not service._is_stale(workload, sync_interval_seconds=3600)


def test_inventory_and_reconciliation_routes_are_registered(app: object) -> None:
    paths = app.openapi()["paths"]  # type: ignore[attr-defined]
    assert "/api/v1/admin/clusters/{cluster_id}/sync" in paths
    assert "/api/v1/admin/inventory/sync-runs" in paths
    assert "/api/v1/admin/inventory/sync-runs/{run_id}" in paths
    assert "/api/v1/admin/inventory/freshness" in paths
    assert "/api/v1/admin/inventory/reconciliation/findings" in paths
    assert "/api/v1/admin/inventory/reconciliation/findings/{finding_id}/acknowledge" in paths
    assert "/api/v1/admin/inventory/reconciliation/findings/{finding_id}/resolve" in paths
