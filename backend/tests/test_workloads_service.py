from secrets import token_urlsafe
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import UserRole
from app.security.access import Principal
from app.services.workloads import WorkloadService


async def test_workload_listing_only_queries_present_workloads_from_active_clusters() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.all.return_value = []
    session.execute.return_value = result
    service = WorkloadService(
        session=cast(AsyncSession, session),
        principal=Principal(
            user_id=uuid4(),
            email="operator@example.test",
            role=UserRole.OPERATOR,
            session_epoch=0,
        ),
        request_id=token_urlsafe(12),
    )

    assert await service.list_workloads(organization_id=None, cluster_id=None) == []

    statement = session.execute.await_args.args[0]
    sql = str(statement)
    assert "clusters.is_active IS true" in sql
    assert "workloads.is_present IS true" in sql
