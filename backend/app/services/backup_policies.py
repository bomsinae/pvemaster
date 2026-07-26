import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import Organization, User, UserRole
from app.models.backup import (
    BackupPolicy,
    BackupPolicyAssignment,
    BackupRun,
    BackupTarget,
    BackupVerification,
    RestoreRun,
)
from app.models.operation import OperationStatus, Workload
from app.schemas.backup import (
    BackupPolicyAssignmentRequest,
    BackupPolicyAssignmentResponse,
    BackupPolicyCreate,
    BackupPolicyPreviewItem,
    BackupPolicyPreviewResponse,
    BackupPolicyResponse,
    BackupPolicyUpdate,
    BackupRequest,
    BackupVerificationRequest,
    BackupVerificationResponse,
    RestoreRequest,
)
from app.security.access import Principal, require_service_role
from app.security.credentials import CredentialCipher
from app.services.audit import add_audit_event
from app.services.backup_metadata import BackupMetadataReconciler
from app.services.backup_schedule import next_occurrence
from app.services.backups import BackupPublisher, BackupService, RestorePublisher

logger = logging.getLogger(__name__)


class BackupPolicyService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        principal: Principal | None = None,
        publisher: BackupPublisher,
        restore_publisher: RestorePublisher,
        request_id: str = "scheduler",
        source_ip: str = "scheduler",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._principal = principal
        self._publisher = publisher
        self._restore_publisher = restore_publisher
        self._request_id = request_id
        self._source_ip = source_ip
        self._transport = transport

    async def list_policies(self) -> list[BackupPolicyResponse]:
        self._require_admin()
        ids = (
            await self._session.scalars(
                select(BackupPolicy.id).order_by(BackupPolicy.name, BackupPolicy.id)
            )
        ).all()
        return [await self._response(policy_id) for policy_id in ids]

    async def get_policy(self, policy_id: UUID) -> BackupPolicyResponse:
        self._require_admin()
        return await self._response(policy_id)

    async def create_policy(self, payload: BackupPolicyCreate) -> BackupPolicyResponse:
        principal = self._require_super_admin()
        await self._validate_payload(payload.backup_target_id, payload.assignments)
        now = datetime.now(UTC)
        policy = BackupPolicy(
            name=payload.name.strip(),
            backup_target_id=payload.backup_target_id,
            schedule=payload.schedule,
            timezone=payload.timezone,
            mode=payload.mode,
            retention_reference=payload.retention_reference,
            verification_interval_days=payload.verification_interval_days,
            is_enabled=payload.is_enabled,
            next_run_at=next_occurrence(payload.schedule, payload.timezone, now),
            created_by_id=principal.user_id,
        )
        self._session.add(policy)
        await self._session.flush()
        self._add_assignments(policy.id, payload.assignments)
        self._audit("BACKUP_POLICY_CREATED", policy.id)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(409, "BACKUP_POLICY_CONFLICT", "The backup policy conflicts.") from exc
        return await self._response(policy.id)

    async def update_policy(
        self, policy_id: UUID, payload: BackupPolicyUpdate
    ) -> BackupPolicyResponse:
        self._require_super_admin()
        policy = await self._policy(policy_id, lock=True)
        if policy.version != payload.version:
            raise AppError(409, "BACKUP_POLICY_VERSION_CONFLICT", "The policy changed; reload it.")
        await self._validate_payload(payload.backup_target_id, payload.assignments)
        schedule_changed = (
            policy.schedule != payload.schedule or policy.timezone != payload.timezone
        )
        policy.name = payload.name.strip()
        policy.backup_target_id = payload.backup_target_id
        policy.schedule = payload.schedule
        policy.timezone = payload.timezone
        policy.mode = payload.mode
        policy.retention_reference = payload.retention_reference
        policy.verification_interval_days = payload.verification_interval_days
        policy.is_enabled = payload.is_enabled
        policy.version += 1
        if schedule_changed:
            policy.next_run_at = next_occurrence(
                payload.schedule, payload.timezone, datetime.now(UTC)
            )
            policy.skip_next_at = None
        await self._session.execute(
            delete(BackupPolicyAssignment).where(
                BackupPolicyAssignment.policy_id == policy.id
            )
        )
        self._add_assignments(policy.id, payload.assignments)
        self._audit("BACKUP_POLICY_UPDATED", policy.id)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(409, "BACKUP_POLICY_CONFLICT", "The backup policy conflicts.") from exc
        return await self._response(policy.id)

    async def delete_policy(self, policy_id: UUID) -> None:
        self._require_super_admin()
        policy = await self._policy(policy_id, lock=True)
        self._audit("BACKUP_POLICY_DELETED", policy.id)
        await self._session.delete(policy)
        await self._session.commit()

    async def preview(self, policy_id: UUID) -> BackupPolicyPreviewResponse:
        self._require_admin()
        policy = await self._policy(policy_id)
        return BackupPolicyPreviewResponse(
            policy_id=policy.id,
            next_run_at=policy.next_run_at,
            items=await self._preview_items(policy),
        )

    async def skip_next(self, policy_id: UUID, version: int) -> BackupPolicyResponse:
        self._require_super_admin()
        policy = await self._policy(policy_id, lock=True)
        if policy.version != version:
            raise AppError(409, "BACKUP_POLICY_VERSION_CONFLICT", "The policy changed; reload it.")
        policy.skip_next_at = policy.next_run_at
        policy.version += 1
        self._audit("BACKUP_POLICY_SKIPPED", policy.id)
        await self._session.commit()
        return await self._response(policy.id)

    async def run_now(self, policy_id: UUID) -> int:
        principal = self._require_admin()
        policy = await self._policy(policy_id)
        return await self._dispatch_policy(
            policy,
            scheduled_for=datetime.now(UTC),
            actor=principal,
            trigger_type="RUN_NOW",
        )

    async def dispatch_due(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        policies = (
            await self._session.scalars(
                select(BackupPolicy)
                .where(
                    BackupPolicy.is_enabled.is_(True),
                    BackupPolicy.next_run_at <= current,
                )
                .order_by(BackupPolicy.next_run_at)
                .with_for_update(skip_locked=True)
            )
        ).all()
        dispatched = 0
        for policy in policies:
            scheduled_for = policy.next_run_at
            policy.last_dispatched_at = current
            policy.next_run_at = next_occurrence(
                policy.schedule,
                policy.timezone,
                scheduled_for,
            )
            skipped = policy.skip_next_at is not None and policy.skip_next_at == scheduled_for
            if skipped:
                policy.skip_next_at = None
                policy.version += 1
                await self._session.commit()
                continue
            actor = await self._session.get(User, policy.created_by_id)
            if actor is None or not actor.is_active or actor.role != UserRole.SUPER_ADMIN.value:
                logger.warning(
                    "Backup policy owner is not authorized",
                    extra={"backup_policy_id": str(policy.id)},
                )
                await self._session.commit()
                continue
            principal = Principal(
                user_id=actor.id,
                email=actor.email,
                role=UserRole.SUPER_ADMIN,
                session_epoch=actor.session_epoch,
            )
            dispatched += await self._dispatch_policy(
                policy,
                scheduled_for=scheduled_for,
                actor=principal,
                trigger_type="SCHEDULED",
            )
        return dispatched

    async def reconcile_metadata(self) -> int:
        self._require_admin()
        return await BackupMetadataReconciler(
            session=self._session,
            settings=self._settings,
            cipher=self._cipher,
        ).reconcile()

    async def request_verification(
        self,
        run_id: UUID,
        payload: BackupVerificationRequest,
        idempotency_key: str,
    ) -> BackupVerificationResponse:
        principal = self._require_super_admin()
        run = await self._restorable_run(run_id)
        now = datetime.now(UTC)
        verification = BackupVerification(
            backup_run_id=run.id,
            verification_type=payload.verification_type,
            status="RUNNING",
            snapshot_volume_id=run.snapshot_volume_id or "",
            started_at=now,
            requested_by_id=principal.user_id,
        )
        self._session.add(verification)
        await self._session.flush()
        verification_id = verification.id
        await self._session.commit()
        if payload.verification_type == "METADATA":
            available = await BackupMetadataReconciler(
                session=self._session,
                settings=self._settings,
                cipher=self._cipher,
            ).verify(run.id)
            loaded_verification = await self._session.get(
                BackupVerification, verification_id
            )
            assert loaded_verification is not None
            verification = loaded_verification
            verification.status = "SUCCEEDED" if available else "FAILED"
            verification.error_code = None if available else "SNAPSHOT_NOT_FOUND"
            verification.result_summary = (
                "Snapshot metadata matched the observed PBS content."
                if available
                else "The snapshot was not present in the observed PBS content."
            )
            verification.finished_at = datetime.now(UTC)
            await self._session.commit()
        else:
            restore_payload = RestoreRequest(
                target_node=payload.target_node or "",
                target_vmid=payload.target_vmid or 0,
                target_name=payload.target_name or "",
            )
            backup_service = self._backup_service(principal)
            try:
                restore, _ = await backup_service.request_restore(
                    run.id,
                    restore_payload,
                    idempotency_key,
                )
            except Exception as exc:
                await self._session.rollback()
                failed_verification = await self._session.get(
                    BackupVerification, verification_id
                )
                assert failed_verification is not None
                verification = failed_verification
                verification.status = "FAILED"
                verification.error_code = (
                    exc.code if isinstance(exc, AppError) else "RESTORE_DRILL_FAILED"
                )
                verification.finished_at = datetime.now(UTC)
                await self._session.commit()
                raise
            stored_verification = await self._session.get(
                BackupVerification, verification_id
            )
            assert stored_verification is not None
            verification = stored_verification
            verification.restore_run_id = restore.id
            await self._session.commit()
        self._audit("BACKUP_VERIFICATION_REQUESTED", verification.id)
        await self._session.commit()
        return self._verification_response(verification)

    async def list_verifications(
        self, *, run_id: UUID | None = None
    ) -> list[BackupVerificationResponse]:
        self._require_admin()
        statement = select(BackupVerification)
        if run_id is not None:
            statement = statement.where(BackupVerification.backup_run_id == run_id)
        rows = (
            await self._session.scalars(
                statement.order_by(BackupVerification.created_at.desc()).limit(500)
            )
        ).all()
        return [self._verification_response(item) for item in rows]

    async def reconcile_verifications(self) -> int:
        rows = (
            await self._session.scalars(
                select(BackupVerification).where(
                    BackupVerification.status == "RUNNING",
                    BackupVerification.restore_run_id.is_not(None),
                )
            )
        ).all()
        changed = 0
        for verification in rows:
            restore = await self._session.get(RestoreRun, verification.restore_run_id)
            if restore is None or restore.status in {
                OperationStatus.QUEUED.value,
                OperationStatus.RUNNING.value,
            }:
                continue
            verification.status = (
                "SUCCEEDED"
                if restore.status == OperationStatus.SUCCEEDED.value
                else "FAILED"
            )
            verification.error_code = (
                None if verification.status == "SUCCEEDED" else f"RESTORE_{restore.status}"
            )
            verification.result_summary = (
                "The isolated restore drill completed."
                if verification.status == "SUCCEEDED"
                else "The isolated restore drill did not complete successfully."
            )
            verification.finished_at = datetime.now(UTC)
            changed += 1
        if changed:
            await self._session.commit()
        return changed

    async def mark_due_verifications(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        policies = (
            await self._session.scalars(
                select(BackupPolicy).where(BackupPolicy.is_enabled.is_(True))
            )
        ).all()
        created = 0
        for policy in policies:
            latest_run = await self._session.scalar(
                select(BackupRun)
                .join(
                    BackupPolicyAssignment,
                    BackupPolicyAssignment.id == BackupRun.policy_assignment_id,
                )
                .where(
                    BackupPolicyAssignment.policy_id == policy.id,
                    BackupRun.status == OperationStatus.SUCCEEDED.value,
                    BackupRun.snapshot_volume_id.is_not(None),
                )
                .order_by(BackupRun.finished_at.desc())
            )
            if latest_run is None or latest_run.snapshot_volume_id is None:
                continue
            latest_verification = await self._session.scalar(
                select(func.max(BackupVerification.created_at)).where(
                    BackupVerification.backup_run_id == latest_run.id
                )
            )
            due_at = (latest_verification or latest_run.finished_at or latest_run.created_at) + (
                timedelta(days=policy.verification_interval_days)
            )
            if due_at > current:
                continue
            existing = await self._session.scalar(
                select(BackupVerification.id).where(
                    BackupVerification.backup_run_id == latest_run.id,
                    BackupVerification.status == "DUE",
                )
            )
            if existing is not None:
                continue
            self._session.add(
                BackupVerification(
                    backup_run_id=latest_run.id,
                    verification_type="RESTORE_DRILL",
                    status="DUE",
                    snapshot_volume_id=latest_run.snapshot_volume_id,
                    due_at=due_at,
                )
            )
            created += 1
        if created:
            await self._session.commit()
        return created

    async def _dispatch_policy(
        self,
        policy: BackupPolicy,
        *,
        scheduled_for: datetime,
        actor: Principal,
        trigger_type: str,
    ) -> int:
        items = await self._preview_items(policy)
        backup_service = self._backup_service(actor)
        dispatched = 0
        for item in items:
            if not item.eligible:
                continue
            key = (
                f"policy:{policy.id}:{item.assignment_id}:{item.workload_id}:"
                f"{scheduled_for.isoformat()}"
            )
            try:
                run, created = await backup_service.request_backup(
                    item.workload_id,
                    BackupRequest(backup_target_id=policy.backup_target_id),
                    key,
                )
            except AppError as exc:
                if exc.code == "OPERATION_CONFLICT":
                    continue
                logger.warning(
                    "Backup policy dispatch failed",
                    extra={"backup_policy_id": str(policy.id), "error_code": exc.code},
                )
                continue
            if created:
                stored = await self._session.get(BackupRun, run.id)
                assert stored is not None
                stored.policy_assignment_id = item.assignment_id
                stored.scheduled_for = scheduled_for
                stored.trigger_type = trigger_type
                await self._session.commit()
                dispatched += 1
        return dispatched

    async def _preview_items(self, policy: BackupPolicy) -> list[BackupPolicyPreviewItem]:
        assignments = (
            await self._session.scalars(
                select(BackupPolicyAssignment).where(
                    BackupPolicyAssignment.policy_id == policy.id
                )
            )
        ).all()
        items: list[BackupPolicyPreviewItem] = []
        for assignment in assignments:
            workload_query = select(Workload).where(
                Workload.is_present.is_(True),
                Workload.is_template.is_(False),
                Workload.kind.in_(["QEMU", "LXC"]),
            )
            if assignment.workload_id is not None:
                workload_query = workload_query.where(Workload.id == assignment.workload_id)
            else:
                workload_query = workload_query.where(
                    Workload.organization_id == assignment.organization_id
                )
            workloads = (await self._session.scalars(workload_query)).all()
            for workload in workloads:
                recent_success = await self._session.scalar(
                    select(func.max(BackupRun.finished_at)).where(
                        BackupRun.workload_id == workload.id,
                        BackupRun.status == OperationStatus.SUCCEEDED.value,
                    )
                )
                eligible = workload.cluster_id == (
                    await self._target_cluster_id(policy.backup_target_id)
                )
                items.append(
                    BackupPolicyPreviewItem(
                        assignment_id=assignment.id,
                        organization_id=assignment.organization_id,
                        workload_id=workload.id,
                        workload_name=workload.name,
                        kind=workload.kind,
                        cluster_id=workload.cluster_id,
                        eligible=eligible,
                        reason=None if eligible else "BACKUP_TARGET_CLUSTER_MISMATCH",
                        recent_success_at=recent_success,
                    )
                )
        return items

    async def _response(self, policy_id: UUID) -> BackupPolicyResponse:
        row = (
            await self._session.execute(
                select(BackupPolicy, BackupTarget)
                .join(BackupTarget, BackupTarget.id == BackupPolicy.backup_target_id)
                .where(BackupPolicy.id == policy_id)
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "BACKUP_POLICY_NOT_FOUND", "The backup policy was not found.")
        policy, target = row
        assignments = (
            await self._session.scalars(
                select(BackupPolicyAssignment).where(
                    BackupPolicyAssignment.policy_id == policy.id
                )
            )
        ).all()
        assignment_responses: list[BackupPolicyAssignmentResponse] = []
        for assignment in assignments:
            organization = (
                await self._session.get(Organization, assignment.organization_id)
                if assignment.organization_id
                else None
            )
            workload = (
                await self._session.get(Workload, assignment.workload_id)
                if assignment.workload_id
                else None
            )
            assignment_responses.append(
                BackupPolicyAssignmentResponse(
                    id=assignment.id,
                    organization_id=assignment.organization_id,
                    organization_name=organization.name if organization else None,
                    workload_id=assignment.workload_id,
                    workload_name=workload.name if workload else None,
                )
            )
        assignment_ids = [item.id for item in assignments]
        recent_success = None
        statuses: list[str] = []
        if assignment_ids:
            recent_success = await self._session.scalar(
                select(func.max(BackupRun.finished_at)).where(
                    BackupRun.policy_assignment_id.in_(assignment_ids),
                    BackupRun.status == OperationStatus.SUCCEEDED.value,
                )
            )
            statuses = list(
                (
                    await self._session.scalars(
                        select(BackupRun.status)
                        .where(BackupRun.policy_assignment_id.in_(assignment_ids))
                        .order_by(BackupRun.created_at.desc())
                        .limit(100)
                    )
                ).all()
            )
        consecutive_failures = 0
        for status in statuses:
            if status == OperationStatus.SUCCEEDED.value:
                break
            if status in {OperationStatus.FAILED.value, OperationStatus.TIMEOUT.value}:
                consecutive_failures += 1
        return BackupPolicyResponse(
            id=policy.id,
            name=policy.name,
            backup_target_id=policy.backup_target_id,
            backup_target_name=target.storage_id,
            schedule=policy.schedule,
            timezone=policy.timezone,
            mode=policy.mode,
            retention_reference=policy.retention_reference,
            verification_interval_days=policy.verification_interval_days,
            is_enabled=policy.is_enabled,
            next_run_at=policy.next_run_at,
            last_dispatched_at=policy.last_dispatched_at,
            skip_next_at=policy.skip_next_at,
            recent_success_at=recent_success,
            consecutive_failures=consecutive_failures,
            assignments=assignment_responses,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
            version=policy.version,
        )

    async def _validate_payload(
        self,
        target_id: UUID,
        assignments: list[BackupPolicyAssignmentRequest],
    ) -> None:
        target = await self._session.scalar(
            select(BackupTarget).where(
                BackupTarget.id == target_id,
                BackupTarget.is_enabled.is_(True),
            )
        )
        if target is None:
            raise AppError(404, "BACKUP_TARGET_NOT_FOUND", "The backup target was not found.")
        seen: set[tuple[str, UUID]] = set()
        for assignment in assignments:
            if assignment.organization_id is not None:
                key = ("organization", assignment.organization_id)
                organization = await self._session.scalar(
                    select(Organization).where(
                        Organization.id == assignment.organization_id,
                        Organization.is_active.is_(True),
                    )
                )
                if organization is None:
                    raise AppError(
                        404,
                        "ORGANIZATION_NOT_FOUND",
                        "The policy organization was not found.",
                    )
            else:
                assert assignment.workload_id is not None
                key = ("workload", assignment.workload_id)
                workload = await self._session.get(Workload, assignment.workload_id)
                if workload is None:
                    raise AppError(
                        404,
                        "VM_NOT_FOUND",
                        "The policy workload was not found.",
                    )
            if key in seen:
                raise AppError(
                    422,
                    "DUPLICATE_POLICY_ASSIGNMENT",
                    "The policy assignment is duplicated.",
                )
            seen.add(key)

    def _add_assignments(
        self,
        policy_id: UUID,
        assignments: list[BackupPolicyAssignmentRequest],
    ) -> None:
        for assignment in assignments:
            self._session.add(
                BackupPolicyAssignment(
                    policy_id=policy_id,
                    organization_id=assignment.organization_id,
                    workload_id=assignment.workload_id,
                )
            )

    async def _policy(self, policy_id: UUID, *, lock: bool = False) -> BackupPolicy:
        statement = select(BackupPolicy).where(BackupPolicy.id == policy_id)
        if lock:
            statement = statement.with_for_update()
        policy = await self._session.scalar(statement)
        if policy is None:
            raise AppError(404, "BACKUP_POLICY_NOT_FOUND", "The backup policy was not found.")
        return policy

    async def _restorable_run(self, run_id: UUID) -> BackupRun:
        run = await self._session.scalar(
            select(BackupRun).where(
                BackupRun.id == run_id,
                BackupRun.status == OperationStatus.SUCCEEDED.value,
                BackupRun.snapshot_volume_id.is_not(None),
            )
        )
        if run is None:
            raise AppError(
                409,
                "BACKUP_NOT_VERIFIABLE",
                "A successful backup snapshot is required.",
            )
        return run

    async def _target_cluster_id(self, target_id: UUID) -> UUID:
        cluster_id = await self._session.scalar(
            select(BackupTarget.cluster_id).where(BackupTarget.id == target_id)
        )
        if cluster_id is None:
            raise AppError(404, "BACKUP_TARGET_NOT_FOUND", "The backup target was not found.")
        return cluster_id

    def _backup_service(self, principal: Principal) -> BackupService:
        return BackupService(
            session=self._session,
            settings=self._settings,
            cipher=self._cipher,
            principal=principal,
            publisher=self._publisher,
            restore_publisher=self._restore_publisher,
            request_id=self._request_id,
            source_ip=self._source_ip,
            transport=self._transport,
        )

    def _audit(self, action: str, target_id: UUID) -> None:
        principal = self._require_super_admin()
        add_audit_event(
            self._session,
            action=action,
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            source_ip=self._source_ip,
            target_type="backup_policy",
            target_id=target_id,
        )

    def _require_admin(self) -> Principal:
        if self._principal is None:
            raise AppError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        return self._principal

    def _require_super_admin(self) -> Principal:
        if self._principal is None:
            raise AppError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        return self._principal

    @staticmethod
    def _verification_response(item: BackupVerification) -> BackupVerificationResponse:
        return BackupVerificationResponse(
            id=item.id,
            backup_run_id=item.backup_run_id,
            restore_run_id=item.restore_run_id,
            verification_type=item.verification_type,
            status=item.status,
            snapshot_volume_id=item.snapshot_volume_id,
            due_at=item.due_at,
            started_at=item.started_at,
            finished_at=item.finished_at,
            error_code=item.error_code,
            result_summary=item.result_summary,
            created_at=item.created_at,
        )
