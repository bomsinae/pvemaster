import hmac
import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.auth import Organization, OrganizationMember, UserRole
from app.models.operation import Operation, OperationStatus, Workload, WorkloadAssignment
from app.models.self_service import (
    ApprovalStep,
    OrganizationServiceQuota,
    SecurityGroup,
    ServiceRequest,
    ServiceRequestStatus,
    ServiceRequestType,
    SshPublicKey,
    WorkloadSecurityGroup,
    WorkloadSshPublicKey,
)
from app.schemas.self_service import (
    ApprovalStepResponse,
    SecurityGroupCreate,
    SecurityGroupResponse,
    ServiceRequestCreate,
    ServiceRequestDecision,
    ServiceRequestExecution,
    ServiceRequestPreviewResponse,
    ServiceRequestResponse,
    SshPublicKeyCreate,
    SshPublicKeyResponse,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event
from app.services.organization_access import (
    WORKLOAD_OPERATE_ROLES,
    active_membership_conditions,
)
from app.services.quota import finish_quota_reservation, reserve_quota

DEFAULT_MAX_CPU = 64
DEFAULT_MAX_MEMORY = 512 * 1024**3
DEFAULT_MAX_DISK = 16 * 1024**4
DEFAULT_MAX_PENDING = 10
HIGH_RISK_TYPES = {
    ServiceRequestType.RESTORE_REQUEST,
    ServiceRequestType.REINSTALL,
}


class CustomerSelfService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._principal = principal
        self._request_id = request_id
        self._source_ip = source_ip
        require_service_role(principal, UserRole.CUSTOMER)

    async def list_keys(self) -> list[SshPublicKeyResponse]:
        items = await self._session.scalars(
            select(SshPublicKey)
            .join(
                OrganizationMember,
                OrganizationMember.organization_id == SshPublicKey.organization_id,
            )
            .join(Organization, Organization.id == SshPublicKey.organization_id)
            .where(
                SshPublicKey.owner_user_id == self._principal.user_id,
                SshPublicKey.revoked_at.is_(None),
                *active_membership_conditions(
                    user_id=self._principal.user_id,
                    organization_id=SshPublicKey.organization_id,
                    roles=WORKLOAD_OPERATE_ROLES,
                ),
                Organization.is_active.is_(True),
            )
            .order_by(SshPublicKey.created_at.desc())
        )
        return [self._key_response(item) for item in items]

    async def create_key(self, vm_id: UUID, payload: SshPublicKeyCreate) -> SshPublicKeyResponse:
        workload, _ = await self._owned_vm(vm_id)
        if workload.organization_id is None:
            raise AppError(404, "VM_NOT_FOUND", "The VM was not found.")
        encoded = payload.public_key.split()[1]
        fingerprint = f"SHA256:{sha256(encoded.encode()).hexdigest()}"
        item = SshPublicKey(
            id=uuid4(),
            owner_user_id=self._principal.user_id,
            organization_id=workload.organization_id,
            public_key=payload.public_key,
            fingerprint=fingerprint,
            label=payload.label.strip(),
        )
        self._session.add(item)
        self._audit(
            "SSH_PUBLIC_KEY_CREATED",
            "ssh_public_key",
            item.id,
            organization_id=workload.organization_id,
            details={"fingerprint": fingerprint, "label": item.label},
        )
        await self._commit("SSH_PUBLIC_KEY_CONFLICT", "This SSH public key already exists.")
        return self._key_response(item)

    async def revoke_key(self, key_id: UUID) -> None:
        key = await self._owned_key(key_id)
        active_request = await self._session.scalar(
            select(ServiceRequest.id).where(
                ServiceRequest.requested_by_id == self._principal.user_id,
                ServiceRequest.organization_id == key.organization_id,
                ServiceRequest.status.in_(
                    [
                        ServiceRequestStatus.PENDING_APPROVAL.value,
                        ServiceRequestStatus.APPROVED.value,
                        ServiceRequestStatus.IN_PROGRESS.value,
                        ServiceRequestStatus.NEEDS_ATTENTION.value,
                    ]
                ),
                ServiceRequest.input_snapshot["ssh_key_id"].as_string() == str(key.id),
            )
        )
        if active_request is not None:
            raise AppError(
                409,
                "SSH_KEY_IN_USE",
                "The SSH public key is referenced by an active service request.",
            )
        key.revoked_at = datetime.now(UTC)
        self._audit(
            "SSH_PUBLIC_KEY_REVOKED",
            "ssh_public_key",
            key.id,
            organization_id=key.organization_id,
            details={"fingerprint": key.fingerprint},
        )
        await self._session.commit()

    async def list_security_groups(self, vm_id: UUID) -> list[SecurityGroupResponse]:
        workload, _ = await self._owned_vm(vm_id)
        items = await self._session.scalars(
            select(SecurityGroup)
            .where(
                SecurityGroup.is_enabled.is_(True),
                or_(
                    SecurityGroup.is_global.is_(True),
                    SecurityGroup.organization_id == workload.organization_id,
                ),
            )
            .order_by(SecurityGroup.name)
        )
        return [self._group_response(item) for item in items]

    async def preview(
        self, vm_id: UUID, payload: ServiceRequestCreate
    ) -> ServiceRequestPreviewResponse:
        workload, _ = await self._owned_vm(vm_id)
        requested, impacts = await self._validated_input(workload, payload)
        return ServiceRequestPreviewResponse(
            request_type=payload.request_type,
            requires_step_up=payload.request_type in HIGH_RISK_TYPES,
            cancellable_until="APPROVAL",
            impacts=impacts,
            current={
                "cpu_cores": workload.cpu_cores,
                "memory_bytes": workload.memory_bytes,
                "disk_bytes": workload.disk_bytes,
                "power_state": workload.power_state,
            },
            requested=requested,
        )

    async def create_request(
        self,
        vm_id: UUID,
        payload: ServiceRequestCreate,
        idempotency_key: str,
    ) -> ServiceRequestResponse:
        workload, assignment = await self._owned_vm(vm_id)
        requested, impacts = await self._validated_input(workload, payload)
        key_hash = sha256(idempotency_key.encode()).digest()
        fingerprint = sha256(
            json.dumps(
                {
                    "workload_id": str(workload.id),
                    "request_type": payload.request_type.value,
                    "input": requested,
                },
                sort_keys=True,
            ).encode()
        ).digest()
        existing = await self._session.scalar(
            select(ServiceRequest).where(
                ServiceRequest.requested_by_id == self._principal.user_id,
                ServiceRequest.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise AppError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "The idempotency key was already used for another request.",
                )
            return await self._response(existing, customer_scope=True)

        quota = await self._quota(workload.organization_id)
        pending = await self._session.scalar(
            select(func.count())
            .select_from(ServiceRequest)
            .where(
                ServiceRequest.organization_id == workload.organization_id,
                ServiceRequest.status.in_(
                    [
                        ServiceRequestStatus.PENDING_APPROVAL.value,
                        ServiceRequestStatus.APPROVED.value,
                        ServiceRequestStatus.IN_PROGRESS.value,
                        ServiceRequestStatus.NEEDS_ATTENTION.value,
                    ]
                ),
            )
        )
        if int(pending or 0) >= quota["max_pending_requests"]:
            raise AppError(
                409,
                "ORGANIZATION_REQUEST_QUOTA_EXCEEDED",
                "The organization has too many active service requests.",
            )
        item = ServiceRequest(
            id=uuid4(),
            request_type=payload.request_type.value,
            requested_by_id=self._principal.user_id,
            organization_id=assignment.organization_id,
            workload_id=workload.id,
            assignment_id=assignment.id,
            input_snapshot=requested,
            impact_snapshot={"messages": impacts},
            status=ServiceRequestStatus.PENDING_APPROVAL.value,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            version=1,
        )
        self._session.add(item)
        try:
            await self._session.flush([item])
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "SERVICE_REQUEST_CONFLICT",
                "A matching active request already exists for this VM.",
            ) from exc
        self._session.add(
            ApprovalStep(
                service_request_id=item.id,
                step_order=1,
                approver_role=UserRole.SUPER_ADMIN.value,
            )
        )
        self._audit(
            "CUSTOMER_SERVICE_REQUEST_CREATED",
            "service_request",
            item.id,
            organization_id=item.organization_id,
            workload_id=item.workload_id,
            details={"request_type": item.request_type, "impacts": impacts},
        )
        await self._commit(
            "SERVICE_REQUEST_CONFLICT",
            "A matching active request already exists for this VM.",
        )
        return await self._response(item, customer_scope=True)

    async def list_requests(self) -> list[ServiceRequestResponse]:
        items = await self._session.scalars(
            select(ServiceRequest)
            .join(Organization, Organization.id == ServiceRequest.organization_id)
            .join(
                OrganizationMember,
                OrganizationMember.organization_id == ServiceRequest.organization_id,
            )
            .join(Workload, Workload.id == ServiceRequest.workload_id)
            .where(
                ServiceRequest.requested_by_id == self._principal.user_id,
                *active_membership_conditions(
                    user_id=self._principal.user_id,
                    organization_id=ServiceRequest.organization_id,
                    roles=WORKLOAD_OPERATE_ROLES,
                ),
                Organization.is_active.is_(True),
                Workload.organization_id == ServiceRequest.organization_id,
            )
            .order_by(ServiceRequest.requested_at.desc())
        )
        return [await self._response(item, customer_scope=True) for item in items]

    async def get_request(self, request_id: UUID) -> ServiceRequestResponse:
        item = await self._customer_request(request_id)
        return await self._response(item, customer_scope=True)

    async def cancel(self, request_id: UUID, version: int) -> ServiceRequestResponse:
        item = await self._customer_request(request_id, lock=True)
        if item.version != version:
            raise self._version_conflict()
        if item.status != ServiceRequestStatus.PENDING_APPROVAL.value:
            raise AppError(
                409,
                "SERVICE_REQUEST_NOT_CANCELLABLE",
                "Only a request awaiting approval can be cancelled.",
            )
        item.status = ServiceRequestStatus.CANCELLED.value
        item.finished_at = datetime.now(UTC)
        item.version += 1
        self._audit(
            "CUSTOMER_SERVICE_REQUEST_CANCELLED",
            "service_request",
            item.id,
            organization_id=item.organization_id,
            workload_id=item.workload_id,
        )
        await self._session.commit()
        return await self._response(item, customer_scope=True)

    async def _validated_input(
        self, workload: Workload, payload: ServiceRequestCreate
    ) -> tuple[dict[str, object], list[str]]:
        data = payload.input.model_dump(exclude_none=True, mode="json")
        request_type = payload.request_type
        allowed_fields = {
            ServiceRequestType.SSH_KEY_ADD: {"ssh_key_id", "reason"},
            ServiceRequestType.SSH_KEY_REPLACE: {"ssh_key_id", "reason"},
            ServiceRequestType.SSH_KEY_DELETE: {"ssh_key_id", "reason"},
            ServiceRequestType.METADATA_CHANGE: {"hostname", "description", "reason"},
            ServiceRequestType.RDNS_CHANGE: {"rdns", "reason"},
            ServiceRequestType.SECURITY_GROUP_APPLY: {"security_group_id", "reason"},
            ServiceRequestType.BACKUP_RUN: {"reason"},
            ServiceRequestType.RESTORE_REQUEST: {"backup_run_id", "confirmation", "reason"},
            ServiceRequestType.RESIZE: {
                "cpu_cores",
                "memory_bytes",
                "disk_bytes",
                "reason",
            },
            ServiceRequestType.REINSTALL: {"confirmation", "ssh_key_id", "reason"},
        }[request_type]
        if not set(data).issubset(allowed_fields) or (
            not data and request_type is not ServiceRequestType.BACKUP_RUN
        ):
            raise AppError(422, "SERVICE_REQUEST_INPUT_INVALID", "The request input is invalid.")
        required = {
            ServiceRequestType.SSH_KEY_ADD: {"ssh_key_id"},
            ServiceRequestType.SSH_KEY_REPLACE: {"ssh_key_id"},
            ServiceRequestType.SSH_KEY_DELETE: {"ssh_key_id"},
            ServiceRequestType.METADATA_CHANGE: set(),
            ServiceRequestType.RDNS_CHANGE: {"rdns"},
            ServiceRequestType.SECURITY_GROUP_APPLY: {"security_group_id"},
            ServiceRequestType.BACKUP_RUN: set(),
            ServiceRequestType.RESTORE_REQUEST: {"backup_run_id", "confirmation"},
            ServiceRequestType.RESIZE: set(),
            ServiceRequestType.REINSTALL: {"confirmation"},
        }[request_type]
        if (
            not required.issubset(data)
            or (
                request_type is ServiceRequestType.METADATA_CHANGE
                and not {"hostname", "description"}.intersection(data)
            )
            or (
                request_type is ServiceRequestType.RESIZE
                and not {"cpu_cores", "memory_bytes", "disk_bytes"}.intersection(data)
            )
        ):
            raise AppError(422, "SERVICE_REQUEST_INPUT_INVALID", "Required input is missing.")

        key_id = payload.input.ssh_key_id
        if key_id is not None:
            await self._owned_key(key_id, organization_id=workload.organization_id)
        group_id = payload.input.security_group_id
        if group_id is not None:
            group = await self._session.get(SecurityGroup, group_id)
            if (
                group is None
                or not group.is_enabled
                or not (group.is_global or group.organization_id == workload.organization_id)
            ):
                raise AppError(
                    404,
                    "SECURITY_GROUP_NOT_FOUND",
                    "The security group was not found.",
                )

        quota = await self._quota(workload.organization_id)
        if request_type is ServiceRequestType.RESIZE:
            if payload.input.cpu_cores is not None and (
                payload.input.cpu_cores < (workload.cpu_cores or 0)
                or payload.input.cpu_cores > quota["max_cpu_cores_per_vm"]
            ):
                raise AppError(409, "RESIZE_QUOTA_EXCEEDED", "The requested CPU is not allowed.")
            if payload.input.memory_bytes is not None and (
                payload.input.memory_bytes < (workload.memory_bytes or 0)
                or payload.input.memory_bytes > quota["max_memory_bytes_per_vm"]
            ):
                raise AppError(409, "RESIZE_QUOTA_EXCEEDED", "The requested memory is not allowed.")
            if payload.input.disk_bytes is not None and (
                payload.input.disk_bytes < (workload.disk_bytes or 0)
                or payload.input.disk_bytes > quota["max_disk_bytes_per_vm"]
            ):
                raise AppError(
                    409,
                    "DISK_SHRINK_FORBIDDEN",
                    "Disk shrinking or quota overflow is not allowed.",
                )
        if request_type in HIGH_RISK_TYPES:
            expected = f"REINSTALL {workload.name or workload.id}"
            if request_type is ServiceRequestType.RESTORE_REQUEST:
                expected = f"RESTORE {workload.name or workload.id}"
            if payload.input.confirmation != expected:
                raise AppError(
                    422,
                    "TYPED_CONFIRMATION_REQUIRED",
                    f"Type {expected} to confirm this request.",
                )

        impacts = self._impacts(request_type)
        return data, impacts

    @staticmethod
    def _impacts(request_type: ServiceRequestType) -> list[str]:
        impacts = {
            ServiceRequestType.SSH_KEY_ADD: ["로그인 가능한 공개키가 추가됩니다."],
            ServiceRequestType.SSH_KEY_REPLACE: ["기존 공개키 로그인이 중단될 수 있습니다."],
            ServiceRequestType.SSH_KEY_DELETE: ["해당 공개키 로그인이 중단됩니다."],
            ServiceRequestType.METADATA_CHANGE: ["VM identity 정보가 변경됩니다."],
            ServiceRequestType.RDNS_CHANGE: ["외부 DNS 반영에는 시간이 걸릴 수 있습니다."],
            ServiceRequestType.SECURITY_GROUP_APPLY: ["네트워크 연결이 차단될 수 있습니다."],
            ServiceRequestType.BACKUP_RUN: ["백업 동안 storage I/O가 증가할 수 있습니다."],
            ServiceRequestType.RESTORE_REQUEST: [
                "기존 VM을 덮어쓰지 않고 별도 복구 대상으로 처리합니다."
            ],
            ServiceRequestType.RESIZE: ["disk 축소는 허용되지 않으며 재부팅이 필요할 수 있습니다."],
            ServiceRequestType.REINSTALL: [
                "현재 VM의 데이터가 삭제될 수 있으며 승인 후 취소할 수 없습니다."
            ],
        }
        return impacts[request_type]

    async def _owned_vm(self, vm_id: UUID) -> tuple[Workload, WorkloadAssignment]:
        row = (
            await self._session.execute(
                select(Workload, WorkloadAssignment)
                .join(
                    WorkloadAssignment,
                    WorkloadAssignment.workload_id == Workload.id,
                )
                .join(
                    OrganizationMember,
                    OrganizationMember.organization_id == Workload.organization_id,
                )
                .join(Organization, Organization.id == Workload.organization_id)
                .where(
                    Workload.id == vm_id,
                    Workload.kind == "QEMU",
                    Workload.is_present.is_(True),
                    Workload.is_template.is_(False),
                    WorkloadAssignment.revoked_at.is_(None),
                    WorkloadAssignment.organization_id == Workload.organization_id,
                    *active_membership_conditions(
                        user_id=self._principal.user_id,
                        organization_id=Workload.organization_id,
                        roles=WORKLOAD_OPERATE_ROLES,
                    ),
                    Organization.is_active.is_(True),
                )
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "VM_NOT_FOUND", "The VM was not found.")
        return row[0], row[1]

    async def _customer_request(self, request_id: UUID, *, lock: bool = False) -> ServiceRequest:
        statement = (
            select(ServiceRequest)
            .join(Workload, Workload.id == ServiceRequest.workload_id)
            .join(Organization, Organization.id == ServiceRequest.organization_id)
            .join(
                OrganizationMember,
                OrganizationMember.organization_id == ServiceRequest.organization_id,
            )
            .where(
                ServiceRequest.id == request_id,
                ServiceRequest.requested_by_id == self._principal.user_id,
                Workload.organization_id == ServiceRequest.organization_id,
                *active_membership_conditions(
                    user_id=self._principal.user_id,
                    organization_id=ServiceRequest.organization_id,
                    roles=WORKLOAD_OPERATE_ROLES,
                ),
                Organization.is_active.is_(True),
            )
        )
        if lock:
            statement = statement.with_for_update()
        item = await self._session.scalar(statement)
        if item is None:
            raise AppError(404, "SERVICE_REQUEST_NOT_FOUND", "The request was not found.")
        return item

    async def _owned_key(
        self, key_id: UUID, *, organization_id: UUID | None = None
    ) -> SshPublicKey:
        filters = [
            SshPublicKey.id == key_id,
            SshPublicKey.owner_user_id == self._principal.user_id,
            SshPublicKey.revoked_at.is_(None),
            *active_membership_conditions(
                user_id=self._principal.user_id,
                organization_id=SshPublicKey.organization_id,
                roles=WORKLOAD_OPERATE_ROLES,
            ),
            Organization.is_active.is_(True),
        ]
        if organization_id is not None:
            filters.append(SshPublicKey.organization_id == organization_id)
        item = await self._session.scalar(
            select(SshPublicKey)
            .join(
                OrganizationMember,
                OrganizationMember.organization_id == SshPublicKey.organization_id,
            )
            .join(Organization, Organization.id == SshPublicKey.organization_id)
            .where(*filters)
        )
        if item is None:
            raise AppError(404, "SSH_PUBLIC_KEY_NOT_FOUND", "The SSH public key was not found.")
        return item

    async def _quota(self, organization_id: UUID | None) -> dict[str, int]:
        if organization_id is None:
            raise AppError(404, "VM_NOT_FOUND", "The VM was not found.")
        quota = await self._session.get(OrganizationServiceQuota, organization_id)
        return {
            "max_cpu_cores_per_vm": quota.max_cpu_cores_per_vm if quota else DEFAULT_MAX_CPU,
            "max_memory_bytes_per_vm": quota.max_memory_bytes_per_vm
            if quota
            else DEFAULT_MAX_MEMORY,
            "max_disk_bytes_per_vm": quota.max_disk_bytes_per_vm if quota else DEFAULT_MAX_DISK,
            "max_pending_requests": quota.max_pending_requests if quota else DEFAULT_MAX_PENDING,
        }

    async def _response(
        self, item: ServiceRequest, *, customer_scope: bool
    ) -> ServiceRequestResponse:
        workload = await self._session.get(Workload, item.workload_id)
        organization = await self._session.get(Organization, item.organization_id)
        if workload is None or organization is None:
            raise AppError(404, "SERVICE_REQUEST_NOT_FOUND", "The request was not found.")
        steps = await self._session.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.service_request_id == item.id)
            .order_by(ApprovalStep.step_order)
        )
        safe_input = dict(item.input_snapshot)
        if customer_scope:
            safe_input.pop("backup_run_id", None)
        return ServiceRequestResponse(
            id=item.id,
            request_type=ServiceRequestType(item.request_type),
            vm_id=item.workload_id,
            vm_name=workload.name or "VM",
            organization_name=organization.name,
            input=safe_input,
            impact=item.impact_snapshot,
            status=ServiceRequestStatus(item.status),
            operation_id=item.operation_id,
            error_code=item.error_code,
            result_summary=item.result_summary,
            requested_at=item.requested_at,
            started_at=item.started_at,
            finished_at=item.finished_at,
            version=item.version,
            approvals=[
                ApprovalStepResponse(
                    order=step.step_order,
                    approver_role=step.approver_role,
                    decision=step.decision,
                    reason=step.reason,
                    decided_at=step.decided_at,
                )
                for step in steps
            ],
        )

    def _key_response(self, item: SshPublicKey) -> SshPublicKeyResponse:
        return SshPublicKeyResponse(
            id=item.id,
            label=item.label,
            fingerprint=item.fingerprint,
            public_key=item.public_key,
            created_at=item.created_at,
        )

    @staticmethod
    def _group_response(item: SecurityGroup) -> SecurityGroupResponse:
        return SecurityGroupResponse.model_validate(
            {
                "id": item.id,
                "organization_id": item.organization_id,
                "name": item.name,
                "description": item.description,
                "rules": item.rules,
                "is_global": item.is_global,
                "is_enabled": item.is_enabled,
                "version": item.version,
            }
        )

    def _audit(
        self,
        action: str,
        target_type: str,
        target_id: UUID,
        *,
        organization_id: UUID | None = None,
        workload_id: UUID | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        add_audit_event(
            self._session,
            action=action,
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=organization_id,
            workload_id=workload_id,
            source_ip=self._source_ip,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )

    async def _commit(self, code: str, message: str) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            known_constraints = {
                "uq_service_requests_active_type",
                "service_requests_requested_by_id_idempotency_key_hash_key",
                "ssh_public_keys_owner_user_id_organization_id_fingerprint_key",
            }
            if any(name in str(exc.orig) for name in known_constraints):
                raise AppError(409, code, message) from exc
            raise

    @staticmethod
    def _version_conflict() -> AppError:
        return AppError(
            409,
            "SERVICE_REQUEST_VERSION_CONFLICT",
            "The service request changed; reload it.",
        )


class AdminSelfService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._principal = principal
        self._request_id = request_id
        self._source_ip = source_ip
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)

    async def list_requests(self) -> list[ServiceRequestResponse]:
        items = await self._session.scalars(
            select(ServiceRequest).order_by(ServiceRequest.requested_at.desc()).limit(200)
        )
        return [await self._response(item) for item in items]

    async def get_request(self, request_id: UUID) -> ServiceRequestResponse:
        return await self._response(await self._request(request_id))

    async def approve(
        self, request_id: UUID, payload: ServiceRequestDecision
    ) -> ServiceRequestResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        item = await self._request(request_id, lock=True)
        self._require_version_status(item, payload.version, ServiceRequestStatus.PENDING_APPROVAL)
        workload, assignment = await self._current_owner(item)
        if payload.approved_input is not None:
            validator = CustomerSelfService(
                session=self._session,
                principal=await self._customer_principal(item.requested_by_id),
                request_id=self._request_id,
                source_ip=self._source_ip,
            )
            validated, impacts = await validator._validated_input(  # noqa: SLF001
                workload,
                ServiceRequestCreate(
                    request_type=ServiceRequestType(item.request_type),
                    input=payload.approved_input,
                ),
            )
            item.input_snapshot = validated
            item.impact_snapshot = {"messages": impacts, "modified_on_approval": True}
        await self._reserve_resize(item, workload)
        operation = Operation(
            operation_type="SERVICE_REQUEST",
            action=item.request_type.lower()[:16],
            status=OperationStatus.NEEDS_ATTENTION.value,
            requested_by_id=item.requested_by_id,
            source_ip=self._source_ip,
            organization_id=item.organization_id,
            cluster_id=workload.cluster_id,
            workload_id=workload.id,
            idempotency_key_hash=sha256(f"service-request:{item.id}".encode()).digest(),
            request_fingerprint=sha256(
                f"{item.id}:{item.request_type}:{item.input_snapshot}".encode()
            ).digest(),
            celery_task_id=str(uuid4()),
            result={
                "service_request_id": str(item.id),
                "request_type": item.request_type,
                "manual_execution_required": True,
            },
            error_code="APPROVED_AWAITING_EXECUTION",
            error_summary="Approved service request awaits controlled execution.",
            retryable=False,
            attempt_count=0,
            version=1,
        )
        self._session.add(operation)
        await self._session.flush()
        item.assignment_id = assignment.id
        item.operation_id = operation.id
        item.status = ServiceRequestStatus.APPROVED.value
        item.version += 1
        step = await self._approval(item.id)
        step.decision = "APPROVED"
        step.reason = payload.reason
        step.decided_by_id = self._principal.user_id
        step.decided_at = datetime.now(UTC)
        self._audit(
            "SERVICE_REQUEST_APPROVED",
            item,
            {"modified": payload.approved_input is not None},
        )
        await self._session.commit()
        return await self._response(item)

    async def reject(
        self, request_id: UUID, payload: ServiceRequestDecision
    ) -> ServiceRequestResponse:
        item = await self._request(request_id, lock=True)
        self._require_version_status(item, payload.version, ServiceRequestStatus.PENDING_APPROVAL)
        item.status = ServiceRequestStatus.REJECTED.value
        item.result_summary = payload.reason
        item.finished_at = datetime.now(UTC)
        item.version += 1
        step = await self._approval(item.id)
        step.decision = "REJECTED"
        step.reason = payload.reason
        step.decided_by_id = self._principal.user_id
        step.decided_at = datetime.now(UTC)
        self._audit("SERVICE_REQUEST_REJECTED", item)
        await self._session.commit()
        return await self._response(item)

    async def execute(
        self, request_id: UUID, payload: ServiceRequestExecution
    ) -> ServiceRequestResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        item = await self._request(request_id, lock=True)
        if item.version != payload.version:
            raise CustomerSelfService._version_conflict()
        operation = (
            await self._session.get(Operation, item.operation_id)
            if item.operation_id is not None
            else None
        )
        if operation is None:
            raise AppError(409, "SERVICE_REQUEST_NOT_APPROVED", "Approve the request first.")
        if payload.outcome == "START":
            if item.status not in {
                ServiceRequestStatus.APPROVED.value,
                ServiceRequestStatus.NEEDS_ATTENTION.value,
            }:
                raise AppError(409, "SERVICE_REQUEST_STATE_CONFLICT", "Execution cannot start.")
            workload, _ = await self._current_owner(item)
            await self._reserve_resize(item, workload)
            conflict = await self._session.scalar(
                select(Operation.id).where(
                    Operation.workload_id == item.workload_id,
                    Operation.id != operation.id,
                    Operation.status.in_(
                        [
                            OperationStatus.QUEUED.value,
                            OperationStatus.RUNNING.value,
                            OperationStatus.CANCEL_REQUESTED.value,
                        ]
                    ),
                )
            )
            if conflict is not None:
                raise AppError(409, "OPERATION_CONFLICT", "Another VM operation is active.")
            item.status = ServiceRequestStatus.IN_PROGRESS.value
            item.started_at = datetime.now(UTC)
            item.error_code = None
            operation.status = OperationStatus.RUNNING.value
            operation.started_at = item.started_at
            operation.error_code = None
            operation.error_summary = None
            operation.attempt_count += 1
            action = "SERVICE_REQUEST_EXECUTION_STARTED"
        else:
            if item.status != ServiceRequestStatus.IN_PROGRESS.value:
                raise AppError(409, "SERVICE_REQUEST_STATE_CONFLICT", "Execution is not running.")
            now = datetime.now(UTC)
            item.finished_at = now
            operation.finished_at = now
            if payload.outcome == "SUCCEEDED":
                item.status = ServiceRequestStatus.SUCCEEDED.value
                item.error_code = None
                operation.status = OperationStatus.SUCCEEDED.value
                operation.error_code = None
                operation.error_summary = None
                await self._apply_success(item)
                await finish_quota_reservation(
                    self._session,
                    status="CONSUMED",
                    service_request_id=item.id,
                )
                action = "SERVICE_REQUEST_EXECUTION_SUCCEEDED"
            else:
                item.status = ServiceRequestStatus.NEEDS_ATTENTION.value
                item.error_code = "SERVICE_REQUEST_EXECUTION_FAILED"
                operation.status = OperationStatus.NEEDS_ATTENTION.value
                operation.error_code = item.error_code
                operation.error_summary = payload.summary
                await finish_quota_reservation(
                    self._session,
                    status="RELEASED",
                    service_request_id=item.id,
                )
                action = "SERVICE_REQUEST_EXECUTION_FAILED"
        item.result_summary = payload.summary
        item.version += 1
        operation.version += 1
        operation.result = {**operation.result, "summary": payload.summary}
        self._audit(action, item)
        await self._session.commit()
        return await self._response(item)

    async def create_security_group(self, payload: SecurityGroupCreate) -> SecurityGroupResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        if payload.organization_id is not None:
            organization = await self._session.get(Organization, payload.organization_id)
            if organization is None:
                raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
        item = SecurityGroup(
            id=uuid4(),
            organization_id=payload.organization_id,
            name=payload.name.strip(),
            description=payload.description,
            rules=[rule.model_dump(mode="json") for rule in payload.rules],
            is_global=payload.is_global,
            is_enabled=True,
            created_by_id=self._principal.user_id,
        )
        self._session.add(item)
        add_audit_event(
            self._session,
            action="SECURITY_GROUP_CREATED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=item.organization_id,
            target_type="security_group",
            target_id=item.id,
            details={"name": item.name, "is_global": item.is_global},
            source_ip=self._source_ip,
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "SECURITY_GROUP_CONFLICT",
                "The security group already exists.",
            ) from exc
        return CustomerSelfService._group_response(item)

    async def list_security_groups(self) -> list[SecurityGroupResponse]:
        items = await self._session.scalars(select(SecurityGroup).order_by(SecurityGroup.name))
        return [CustomerSelfService._group_response(item) for item in items]

    async def _apply_success(self, item: ServiceRequest) -> None:
        request_type = ServiceRequestType(item.request_type)
        if request_type is ServiceRequestType.RESIZE:
            workload = await self._session.get(Workload, item.workload_id)
            if workload is None:
                raise AppError(409, "VM_NOT_FOUND", "The VM was not found.")
            for key in ("cpu_cores", "memory_bytes", "disk_bytes"):
                if key in item.input_snapshot:
                    setattr(
                        workload,
                        key,
                        self._snapshot_int(item.input_snapshot, key, 0),
                    )
            workload.version += 1
        key_value = item.input_snapshot.get("ssh_key_id")
        if request_type in {
            ServiceRequestType.SSH_KEY_ADD,
            ServiceRequestType.SSH_KEY_REPLACE,
        } and isinstance(key_value, str):
            if request_type is ServiceRequestType.SSH_KEY_REPLACE:
                await self._session.execute(
                    delete(WorkloadSshPublicKey).where(
                        WorkloadSshPublicKey.workload_id == item.workload_id
                    )
                )
            self._session.add(
                WorkloadSshPublicKey(
                    workload_id=item.workload_id,
                    ssh_public_key_id=UUID(key_value),
                )
            )
        elif request_type is ServiceRequestType.SSH_KEY_DELETE and isinstance(key_value, str):
            await self._session.execute(
                delete(WorkloadSshPublicKey).where(
                    WorkloadSshPublicKey.workload_id == item.workload_id,
                    WorkloadSshPublicKey.ssh_public_key_id == UUID(key_value),
                )
            )
        group_value = item.input_snapshot.get("security_group_id")
        if request_type is ServiceRequestType.SECURITY_GROUP_APPLY and isinstance(group_value, str):
            await self._session.execute(
                delete(WorkloadSecurityGroup).where(
                    WorkloadSecurityGroup.workload_id == item.workload_id
                )
            )
            self._session.add(
                WorkloadSecurityGroup(
                    workload_id=item.workload_id,
                    security_group_id=UUID(group_value),
                    applied_by_request_id=item.id,
                )
            )

    async def _reserve_resize(self, item: ServiceRequest, workload: Workload) -> None:
        if item.request_type != ServiceRequestType.RESIZE.value:
            return
        await reserve_quota(
            self._session,
            item.organization_id,
            service_request_id=item.id,
            vcpu=max(
                0,
                self._snapshot_int(item.input_snapshot, "cpu_cores", workload.cpu_cores or 0)
                - (workload.cpu_cores or 0),
            ),
            memory_bytes=max(
                0,
                self._snapshot_int(
                    item.input_snapshot,
                    "memory_bytes",
                    workload.memory_bytes or 0,
                )
                - (workload.memory_bytes or 0),
            ),
            disk_bytes=max(
                0,
                self._snapshot_int(item.input_snapshot, "disk_bytes", workload.disk_bytes or 0)
                - (workload.disk_bytes or 0),
            ),
        )

    @staticmethod
    def _snapshot_int(snapshot: dict[str, object], key: str, default: int) -> int:
        value = snapshot.get(key, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise AppError(
            500,
            "SERVICE_REQUEST_SNAPSHOT_INVALID",
            "The approved request snapshot is invalid.",
        )

    async def _current_owner(self, item: ServiceRequest) -> tuple[Workload, WorkloadAssignment]:
        row = (
            await self._session.execute(
                select(Workload, WorkloadAssignment)
                .join(
                    WorkloadAssignment,
                    WorkloadAssignment.workload_id == Workload.id,
                )
                .where(
                    Workload.id == item.workload_id,
                    Workload.organization_id == item.organization_id,
                    Workload.is_present.is_(True),
                    WorkloadAssignment.organization_id == item.organization_id,
                    WorkloadAssignment.revoked_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise AppError(
                409,
                "SERVICE_REQUEST_OWNERSHIP_CHANGED",
                "VM ownership changed after the request was created.",
            )
        return row[0], row[1]

    async def _customer_principal(self, user_id: UUID) -> Principal:
        return Principal(
            user_id=user_id,
            email="",
            role=UserRole.CUSTOMER,
            session_epoch=0,
            session_id=None,
        )

    async def _request(self, request_id: UUID, *, lock: bool = False) -> ServiceRequest:
        statement = select(ServiceRequest).where(ServiceRequest.id == request_id)
        if lock:
            statement = statement.with_for_update()
        item = await self._session.scalar(statement)
        if item is None:
            raise AppError(404, "SERVICE_REQUEST_NOT_FOUND", "The request was not found.")
        return item

    async def _approval(self, request_id: UUID) -> ApprovalStep:
        step = await self._session.scalar(
            select(ApprovalStep).where(
                ApprovalStep.service_request_id == request_id,
                ApprovalStep.step_order == 1,
            )
        )
        if step is None:
            raise AppError(500, "APPROVAL_STATE_INVALID", "The approval state is invalid.")
        return step

    @staticmethod
    def _require_version_status(
        item: ServiceRequest, version: int, status: ServiceRequestStatus
    ) -> None:
        if item.version != version:
            raise CustomerSelfService._version_conflict()
        if item.status != status.value:
            raise AppError(409, "SERVICE_REQUEST_STATE_CONFLICT", "The request state changed.")

    async def _response(self, item: ServiceRequest) -> ServiceRequestResponse:
        adapter = CustomerSelfService(
            session=self._session,
            principal=Principal(
                user_id=item.requested_by_id,
                email="",
                role=UserRole.CUSTOMER,
                session_epoch=0,
                session_id=None,
            ),
            request_id=self._request_id,
            source_ip=self._source_ip,
        )
        return await adapter._response(item, customer_scope=False)  # noqa: SLF001

    def _audit(
        self,
        action: str,
        item: ServiceRequest,
        details: dict[str, object] | None = None,
    ) -> None:
        add_audit_event(
            self._session,
            action=action,
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=item.organization_id,
            workload_id=item.workload_id,
            operation_id=item.operation_id,
            source_ip=self._source_ip,
            target_type="service_request",
            target_id=item.id,
            details=details or {"request_type": item.request_type},
        )
