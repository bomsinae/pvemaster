from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.core.config import Settings
from app.dependencies import PrincipalDependency, SessionDependency
from app.models.operation import OperationStatus
from app.schemas.backup import (
    BackupRequest,
    BackupRunListResponse,
    BackupRunResponse,
    BackupStorageCandidateListResponse,
    BackupTargetCreate,
    BackupTargetListResponse,
    BackupTargetResponse,
    BackupTargetUpdate,
    RestoreRequest,
    RestoreRunResponse,
)
from app.security.credentials import CredentialCipher
from app.services.backups import BackupPublisher, BackupService, RestorePublisher

router = APIRouter(tags=["admin-backups"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)]


def _service(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupService:
    return BackupService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        cipher=cast(CredentialCipher, request.app.state.credential_cipher),
        principal=principal,
        publisher=cast(BackupPublisher, request.app.state.backup_publisher),
        restore_publisher=cast(RestorePublisher, request.app.state.restore_publisher),
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
        transport=request.app.state.proxmox_transport,
    )


@router.get(
    "/api/v1/admin/clusters/{cluster_id}/backup-storages",
    response_model=BackupStorageCandidateListResponse,
)
async def discover_backup_storages(
    cluster_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupStorageCandidateListResponse:
    return BackupStorageCandidateListResponse(
        items=await _service(request, session, principal).discover_storages(cluster_id)
    )


@router.get("/api/v1/admin/backup-targets", response_model=BackupTargetListResponse)
async def list_backup_targets(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupTargetListResponse:
    return BackupTargetListResponse(
        items=await _service(request, session, principal).list_targets()
    )


@router.post(
    "/api/v1/admin/backup-targets",
    response_model=BackupTargetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_backup_target(
    payload: BackupTargetCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupTargetResponse:
    return await _service(request, session, principal).create_target(payload)


@router.patch(
    "/api/v1/admin/backup-targets/{target_id}",
    response_model=BackupTargetResponse,
)
async def update_backup_target(
    target_id: UUID,
    payload: BackupTargetUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupTargetResponse:
    return await _service(request, session, principal).update_target(target_id, payload)


@router.post(
    "/api/v1/admin/workloads/{workload_id}/backups",
    response_model=BackupRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_workload_backup(
    workload_id: UUID,
    payload: BackupRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupRunResponse:
    run, _ = await _service(request, session, principal).request_backup(
        workload_id, payload, idempotency_key
    )
    response.headers["Location"] = f"/api/v1/admin/backups/{run.id}"
    response.headers["Retry-After"] = "2"
    response.headers["Cache-Control"] = "no-store"
    return run


@router.get("/api/v1/admin/backups", response_model=BackupRunListResponse)
async def list_backups(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    cluster_id: UUID | None = None,
    workload_id: UUID | None = None,
    backup_status: Annotated[OperationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> BackupRunListResponse:
    return BackupRunListResponse(
        items=await _service(request, session, principal).list_runs(
            cluster_id=cluster_id,
            workload_id=workload_id,
            status=backup_status,
            limit=limit,
        )
    )


@router.get("/api/v1/admin/backups/{run_id}", response_model=BackupRunResponse)
async def get_backup(
    run_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupRunResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).get_run(run_id)


@router.post(
    "/api/v1/admin/backups/{run_id}/restores",
    response_model=RestoreRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_backup_restore(
    run_id: UUID,
    payload: RestoreRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> RestoreRunResponse:
    restore, _ = await _service(request, session, principal).request_restore(
        run_id, payload, idempotency_key
    )
    response.headers["Location"] = f"/api/v1/admin/restores/{restore.id}"
    response.headers["Retry-After"] = "2"
    response.headers["Cache-Control"] = "no-store"
    return restore


@router.get("/api/v1/admin/restores/{restore_id}", response_model=RestoreRunResponse)
async def get_restore(
    restore_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> RestoreRunResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).get_restore(restore_id)
