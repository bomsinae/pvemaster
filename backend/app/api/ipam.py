from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.ipam import (
    IpAddressListResponse,
    IpAddressResponse,
    IpAllocationRequest,
    IpAllocationResponse,
    IpPoolCreate,
    IpPoolListResponse,
    IpPoolResponse,
    IpPoolUpdate,
    IpReleaseRequest,
    IpReservationRequest,
)
from app.services.ipam import IpamService

router = APIRouter(prefix="/api/v1/admin", tags=["ipam"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _service(
    request: Request, session: AsyncSession, principal: PrincipalDependency
) -> IpamService:
    return IpamService(
        session=session,
        principal=principal,
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
    )


@router.post("/ip-pools", response_model=IpPoolResponse, status_code=status.HTTP_201_CREATED)
async def create_ip_pool(
    payload: IpPoolCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpPoolResponse:
    return await _service(request, session, principal).create_pool(payload)


@router.get("/ip-pools", response_model=IpPoolListResponse)
async def list_ip_pools(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpPoolListResponse:
    return IpPoolListResponse(items=await _service(request, session, principal).list_pools())


@router.get("/ip-pools/{pool_id}", response_model=IpPoolResponse)
async def get_ip_pool(
    pool_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpPoolResponse:
    return await _service(request, session, principal).get_pool(pool_id)


@router.patch("/ip-pools/{pool_id}", response_model=IpPoolResponse)
async def update_ip_pool(
    pool_id: UUID,
    payload: IpPoolUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpPoolResponse:
    return await _service(request, session, principal).update_pool(pool_id, payload)


@router.delete("/ip-pools/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ip_pool(
    pool_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    version: int = Query(ge=1),
) -> Response:
    await _service(request, session, principal).delete_pool(pool_id, version=version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/ip-pools/{pool_id}/addresses", response_model=IpAddressListResponse)
async def list_ip_addresses(
    pool_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpAddressListResponse:
    return IpAddressListResponse(
        items=await _service(request, session, principal).list_addresses(pool_id)
    )


@router.post(
    "/ip-pools/{pool_id}/reservations",
    response_model=IpAddressResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reserve_ip_address(
    pool_id: UUID,
    payload: IpReservationRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpAddressResponse:
    return await _service(request, session, principal).reserve_address(
        pool_id, payload.address, payload.reason
    )


@router.post(
    "/ip-pools/{pool_id}/allocations",
    response_model=IpAllocationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def allocate_ip_address(
    pool_id: UUID,
    payload: IpAllocationRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpAllocationResponse:
    return await _service(request, session, principal).allocate(
        pool_id, payload.workload_id, payload.address
    )


@router.delete("/ip-allocations/{allocation_id}", response_model=IpAllocationResponse)
async def release_ip_allocation(
    allocation_id: UUID,
    payload: IpReleaseRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpAllocationResponse:
    return await _service(request, session, principal).release(allocation_id, payload.reason)


@router.post("/ip-addresses/{address_id}/approve-release", response_model=IpAddressResponse)
async def approve_ip_release(
    address_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> IpAddressResponse:
    return await _service(request, session, principal).approve_release(address_id)
