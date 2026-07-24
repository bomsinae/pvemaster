from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.inventory import (
    FindingAcknowledgeRequest,
    FindingResolveRequest,
    InventoryFreshnessListResponse,
    ReconciliationFindingListResponse,
    ReconciliationFindingResponse,
    SyncRequestResponse,
    SyncRunListResponse,
    SyncRunResponse,
)
from app.services.reconciliation import InventoryPublisher, ReconciliationService

router = APIRouter(prefix="/api/v1/admin/inventory", tags=["admin-inventory"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
FindingStatusQuery = Annotated[
    Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"] | None,
    Query(),
]
FindingSeverityQuery = Annotated[
    Literal["INFO", "WARNING", "CRITICAL"] | None,
    Query(),
]


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> ReconciliationService:
    return ReconciliationService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        principal=principal,
        publisher=cast(InventoryPublisher, request.app.state.inventory_publisher),
        request_id=request.state.request_id,
    )


@router.get("/sync-runs", response_model=SyncRunListResponse)
async def list_sync_runs(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    cluster_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SyncRunListResponse:
    items = await _service(request, session, principal).list_runs(
        cluster_id=cluster_id,
        limit=limit,
    )
    return SyncRunListResponse(items=items)


@router.get("/sync-runs/{run_id}", response_model=SyncRunResponse)
async def get_sync_run(
    run_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SyncRunResponse:
    return await _service(request, session, principal).get_run(run_id)


@router.get("/freshness", response_model=InventoryFreshnessListResponse)
async def list_freshness(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> InventoryFreshnessListResponse:
    return InventoryFreshnessListResponse(
        items=await _service(request, session, principal).freshness()
    )


@router.get(
    "/reconciliation/findings",
    response_model=ReconciliationFindingListResponse,
)
async def list_findings(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    cluster_id: UUID | None = None,
    status: FindingStatusQuery = None,
    severity: FindingSeverityQuery = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ReconciliationFindingListResponse:
    return ReconciliationFindingListResponse(
        items=await _service(request, session, principal).list_findings(
            cluster_id=cluster_id,
            status=status,
            severity=severity,
            limit=limit,
        )
    )


@router.get(
    "/reconciliation/findings/{finding_id}",
    response_model=ReconciliationFindingResponse,
)
async def get_finding(
    finding_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ReconciliationFindingResponse:
    return await _service(request, session, principal).get_finding(finding_id)


@router.post(
    "/reconciliation/findings/{finding_id}/acknowledge",
    response_model=ReconciliationFindingResponse,
)
async def acknowledge_finding(
    finding_id: UUID,
    payload: FindingAcknowledgeRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ReconciliationFindingResponse:
    return await _service(request, session, principal).acknowledge(
        finding_id,
        assigned_to_id=payload.assigned_to_id,
    )


@router.post(
    "/reconciliation/findings/{finding_id}/resolve",
    response_model=ReconciliationFindingResponse,
)
async def resolve_finding(
    finding_id: UUID,
    payload: FindingResolveRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ReconciliationFindingResponse:
    return await _service(request, session, principal).resolve(
        finding_id,
        resolution_note=payload.resolution_note,
    )


@router.post("/reconciliation/run", response_model=SyncRequestResponse)
async def request_reconciliation(
    cluster_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SyncRequestResponse:
    run = await _service(request, session, principal).request_sync(
        cluster_id,
        triggered_by="reconciliation",
    )
    return SyncRequestResponse(operation_id=run.id, status=run.status)
