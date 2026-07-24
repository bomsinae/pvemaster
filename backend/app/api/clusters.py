from typing import Annotated, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ErrorResponse
from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.cluster import (
    ClusterCreate,
    ClusterListResponse,
    ClusterRemovalCheckResponse,
    ClusterResourceOverviewListResponse,
    ClusterResponse,
    ClusterUpdate,
    ConnectionTestResponse,
    GuestListResponse,
    NodeListResponse,
    NodeMetricRange,
    NodeMetricSeriesResponse,
    StorageListResponse,
)
from app.schemas.inventory import SyncRequestResponse
from app.schemas.workload import WorkloadImportResponse
from app.security.credentials import CredentialCipher
from app.services.clusters import ClusterService
from app.services.reconciliation import InventoryPublisher, ReconciliationService

router = APIRouter(prefix="/api/v1/admin/clusters", tags=["admin-clusters"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


async def get_cluster_service(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ClusterService:
    return ClusterService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        cipher=cast(CredentialCipher, request.app.state.credential_cipher),
        transport=cast(
            httpx.AsyncBaseTransport | None,
            request.app.state.proxmox_transport,
        ),
        principal=principal,
    )


ServiceDependency = Annotated[ClusterService, Depends(get_cluster_service)]
NodePath = Annotated[
    str,
    Path(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
MetricRangeQuery = Annotated[NodeMetricRange, Query()]


@router.post(
    "",
    response_model=ClusterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def create_cluster(request: ClusterCreate, service: ServiceDependency) -> ClusterResponse:
    return await service.create(request)


@router.get("", response_model=ClusterListResponse)
async def list_clusters(service: ServiceDependency) -> ClusterListResponse:
    return ClusterListResponse(items=await service.list_clusters())


@router.get("/overview", response_model=ClusterResourceOverviewListResponse)
async def cluster_resource_overview(
    service: ServiceDependency,
) -> ClusterResourceOverviewListResponse:
    return ClusterResourceOverviewListResponse(items=await service.resource_overview())


@router.get("/{cluster_id}", response_model=ClusterResponse)
async def get_cluster(cluster_id: UUID, service: ServiceDependency) -> ClusterResponse:
    return await service.get(cluster_id)


@router.patch("/{cluster_id}", response_model=ClusterResponse)
async def update_cluster(
    cluster_id: UUID,
    request: ClusterUpdate,
    service: ServiceDependency,
) -> ClusterResponse:
    return await service.update(cluster_id, request)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cluster(cluster_id: UUID, service: ServiceDependency) -> Response:
    await service.delete(cluster_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{cluster_id}/removal-check", response_model=ClusterRemovalCheckResponse)
async def get_cluster_removal_check(
    cluster_id: UUID, service: ServiceDependency
) -> ClusterRemovalCheckResponse:
    return await service.removal_check(cluster_id)


@router.post("/{cluster_id}/test", response_model=ConnectionTestResponse)
@router.post(
    "/{cluster_id}/test-connection",
    response_model=ConnectionTestResponse,
    include_in_schema=False,
)
async def test_cluster(cluster_id: UUID, service: ServiceDependency) -> ConnectionTestResponse:
    return await service.test_connection(cluster_id)


@router.get("/{cluster_id}/nodes", response_model=NodeListResponse)
async def list_nodes(cluster_id: UUID, service: ServiceDependency) -> NodeListResponse:
    return NodeListResponse(items=await service.nodes(cluster_id))


@router.get(
    "/{cluster_id}/nodes/{node}/metrics",
    response_model=NodeMetricSeriesResponse,
)
async def get_node_metrics(
    cluster_id: UUID,
    service: ServiceDependency,
    node: NodePath,
    range: MetricRangeQuery = "hour",
) -> NodeMetricSeriesResponse:
    return await service.node_metrics(cluster_id, node=node, metric_range=range)


@router.get("/{cluster_id}/guests", response_model=GuestListResponse)
async def list_guests(cluster_id: UUID, service: ServiceDependency) -> GuestListResponse:
    return GuestListResponse(items=await service.guests(cluster_id))


@router.get("/{cluster_id}/storages", response_model=StorageListResponse)
async def list_storages(cluster_id: UUID, service: ServiceDependency) -> StorageListResponse:
    return StorageListResponse(items=await service.storages(cluster_id))


@router.post("/{cluster_id}/workloads/import", response_model=WorkloadImportResponse)
async def import_workloads(
    cluster_id: UUID,
    service: ServiceDependency,
) -> WorkloadImportResponse:
    return await service.import_workloads(cluster_id)


@router.post("/{cluster_id}/sync", response_model=SyncRequestResponse)
async def sync_cluster_inventory(
    cluster_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    workload_id: UUID | None = None,
) -> SyncRequestResponse:
    service = ReconciliationService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        principal=principal,
        publisher=cast(InventoryPublisher, request.app.state.inventory_publisher),
        request_id=request.state.request_id,
    )
    run = await service.request_sync(cluster_id, target_workload_id=workload_id)
    return SyncRequestResponse(operation_id=run.id, status=run.status)
