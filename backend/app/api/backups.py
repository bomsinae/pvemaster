from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.core.config import Settings
from app.dependencies import PrincipalDependency, SessionDependency
from app.models.operation import OperationStatus
from app.schemas.backup import (
    BackupMetadataReconcileResponse,
    BackupPolicyCreate,
    BackupPolicyListResponse,
    BackupPolicyPreviewResponse,
    BackupPolicyResponse,
    BackupPolicySkipRequest,
    BackupPolicyUpdate,
    BackupRequest,
    BackupRunListResponse,
    BackupRunResponse,
    BackupStorageCandidateListResponse,
    BackupTargetCreate,
    BackupTargetListResponse,
    BackupTargetResponse,
    BackupTargetUpdate,
    BackupVerificationListResponse,
    BackupVerificationRequest,
    BackupVerificationResponse,
    RestoreRequest,
    RestoreRunResponse,
)
from app.security.credentials import CredentialCipher
from app.security.step_up import require_step_up
from app.services.backup_policies import BackupPolicyService
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


def _policy_service(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupPolicyService:
    return BackupPolicyService(
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
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> RestoreRunResponse:
    await require_step_up(
        request=request,
        session=session,
        principal=principal,
        action="BACKUP_RESTORE",
        step_up_token=x_step_up_token,
    )
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


@router.get("/api/v1/admin/backup-policies", response_model=BackupPolicyListResponse)
async def list_backup_policies(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupPolicyListResponse:
    return BackupPolicyListResponse(
        items=await _policy_service(request, session, principal).list_policies()
    )


@router.post(
    "/api/v1/admin/backup-policies",
    response_model=BackupPolicyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_backup_policy(
    payload: BackupPolicyCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> BackupPolicyResponse:
    await require_step_up(
        request=request,
        session=session,
        principal=principal,
        action="BACKUP_POLICY_CHANGE",
        step_up_token=x_step_up_token,
    )
    return await _policy_service(request, session, principal).create_policy(payload)


@router.get(
    "/api/v1/admin/backup-policies/{policy_id}",
    response_model=BackupPolicyResponse,
)
async def get_backup_policy(
    policy_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupPolicyResponse:
    return await _policy_service(request, session, principal).get_policy(policy_id)


@router.put(
    "/api/v1/admin/backup-policies/{policy_id}",
    response_model=BackupPolicyResponse,
)
async def update_backup_policy(
    policy_id: UUID,
    payload: BackupPolicyUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> BackupPolicyResponse:
    await require_step_up(
        request=request,
        session=session,
        principal=principal,
        action="BACKUP_POLICY_CHANGE",
        step_up_token=x_step_up_token,
    )
    return await _policy_service(request, session, principal).update_policy(policy_id, payload)


@router.delete(
    "/api/v1/admin/backup-policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_backup_policy(
    policy_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> Response:
    await require_step_up(
        request=request,
        session=session,
        principal=principal,
        action="BACKUP_POLICY_CHANGE",
        step_up_token=x_step_up_token,
    )
    await _policy_service(request, session, principal).delete_policy(policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v1/admin/backup-policies/{policy_id}/preview",
    response_model=BackupPolicyPreviewResponse,
)
async def preview_backup_policy(
    policy_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupPolicyPreviewResponse:
    return await _policy_service(request, session, principal).preview(policy_id)


@router.post(
    "/api/v1/admin/backup-policies/{policy_id}/run-now",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_backup_policy_now(
    policy_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> dict[str, int]:
    return {
        "dispatched_count": await _policy_service(request, session, principal).run_now(policy_id)
    }


@router.post(
    "/api/v1/admin/backup-policies/{policy_id}/skip",
    response_model=BackupPolicyResponse,
)
async def skip_backup_policy(
    policy_id: UUID,
    payload: BackupPolicySkipRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> BackupPolicyResponse:
    await require_step_up(
        request=request,
        session=session,
        principal=principal,
        action="BACKUP_POLICY_CHANGE",
        step_up_token=x_step_up_token,
    )
    return await _policy_service(request, session, principal).skip_next(policy_id, payload.version)


@router.post(
    "/api/v1/admin/backup-metadata/reconcile",
    response_model=BackupMetadataReconcileResponse,
)
async def reconcile_backup_metadata(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> BackupMetadataReconcileResponse:
    count = await _policy_service(request, session, principal).reconcile_metadata()
    return BackupMetadataReconcileResponse(processed_count=count)


@router.get(
    "/api/v1/admin/backup-verifications",
    response_model=BackupVerificationListResponse,
)
async def list_backup_verifications(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    backup_run_id: UUID | None = None,
) -> BackupVerificationListResponse:
    return BackupVerificationListResponse(
        items=await _policy_service(request, session, principal).list_verifications(
            run_id=backup_run_id
        )
    )


@router.post(
    "/api/v1/admin/backups/{run_id}/verifications",
    response_model=BackupVerificationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_backup_verification(
    run_id: UUID,
    payload: BackupVerificationRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> BackupVerificationResponse:
    if payload.verification_type == "RESTORE_DRILL":
        await require_step_up(
            request=request,
            session=session,
            principal=principal,
            action="BACKUP_RESTORE",
            step_up_token=x_step_up_token,
        )
    return await _policy_service(request, session, principal).request_verification(
        run_id,
        payload,
        idempotency_key,
    )
