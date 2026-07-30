from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.workload import (
    WorkloadAssignmentListResponse,
    WorkloadAssignmentResponse,
    WorkloadAssignRequest,
    WorkloadListResponse,
    WorkloadResponse,
)
from app.services.workloads import WorkloadService

router = APIRouter(prefix="/api/v1/admin/workloads", tags=["admin-workloads"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> WorkloadService:
    return WorkloadService(
        session=session,
        principal=principal,
        request_id=request.state.request_id,
        inventory_stale_after_seconds=request.app.state.settings.inventory_stale_after_seconds,
    )


@router.get("", response_model=WorkloadListResponse)
async def list_workloads(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    organization_id: UUID | None = None,
    cluster_id: UUID | None = None,
    is_present: bool = True,
) -> WorkloadListResponse:
    return WorkloadListResponse(
        items=await _service(request, session, principal).list_workloads(
            organization_id=organization_id,
            cluster_id=cluster_id,
            is_present=is_present,
        )
    )


@router.get("/{workload_id}", response_model=WorkloadResponse)
async def get_workload(
    workload_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> WorkloadResponse:
    return await _service(request, session, principal).get(workload_id)


@router.post("/{workload_id}/assign", response_model=WorkloadAssignmentResponse)
async def assign_workload(
    workload_id: UUID,
    payload: WorkloadAssignRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> WorkloadAssignmentResponse:
    return await _service(request, session, principal).assign(workload_id, payload.organization_id)


@router.delete("/{workload_id}/assignment", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_workload(
    workload_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    reason: Annotated[str | None, Query(max_length=500)] = None,
) -> Response:
    await _service(request, session, principal).unassign(workload_id, reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{workload_id}/assignments",
    response_model=WorkloadAssignmentListResponse,
)
async def list_workload_assignments(
    workload_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> WorkloadAssignmentListResponse:
    return WorkloadAssignmentListResponse(
        items=await _service(request, session, principal).assignment_history(workload_id)
    )
