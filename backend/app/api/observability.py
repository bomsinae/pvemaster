from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.observability import (
    AuditLogListResponse,
    AuditLogResponse,
    OperationsStatusResponse,
)
from app.services.observability import ObservabilityService

router = APIRouter(tags=["operations-observability"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency | None = None,
) -> ObservabilityService:
    return ObservabilityService(
        session=session,
        redis=cast(Redis, request.app.state.redis),
        settings=cast(Settings, request.app.state.settings),
        principal=principal,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, session: SessionDependency) -> Response:
    body = await _service(request, session).prometheus_metrics()
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/api/v1/admin/operations/status", response_model=OperationsStatusResponse)
async def operations_status(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationsStatusResponse:
    return await _service(request, session, principal).status()


@router.get("/api/v1/admin/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    action: str | None = Query(default=None, max_length=80),
    actor_user_id: UUID | None = None,
    organization_id: UUID | None = None,
    result: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AuditLogListResponse:
    return await _service(request, session, principal).audit_logs(
        action=action,
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        result=result,
        limit=limit,
        offset=offset,
    )


@router.get("/api/v1/admin/audit-logs/{audit_id}", response_model=AuditLogResponse)
async def get_audit_log(
    audit_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AuditLogResponse:
    return await _service(request, session, principal).audit_log(audit_id)
