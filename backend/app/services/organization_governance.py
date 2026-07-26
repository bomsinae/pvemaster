import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.auth import (
    AuditLog,
    Organization,
    OrganizationMember,
    OrganizationRole,
    User,
    UserRole,
)
from app.models.organization_governance import (
    ApprovalPolicy,
    OrganizationInvitation,
    OrganizationQuota,
    QuotaUsageSnapshot,
)
from app.schemas.organization_governance import (
    ApprovalPolicyResponse,
    ApprovalPolicyUpdate,
    OrganizationActivityResponse,
    OrganizationInvitationCreate,
    OrganizationInvitationResponse,
    OrganizationMembershipResponse,
    OrganizationQuotaResponse,
    OrganizationQuotaUpdate,
    OrganizationRoleUpdate,
    QuotaValues,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event
from app.services.quota import quota_state

ROLE_PERMISSIONS: dict[OrganizationRole, frozenset[str]] = {
    OrganizationRole.ORG_OWNER: frozenset(
        {
            "MEMBER_READ",
            "MEMBER_INVITE",
            "MEMBER_ROLE_WRITE",
            "MEMBER_REMOVE",
            "QUOTA_READ",
            "ACTIVITY_READ",
            "APPROVAL_POLICY_READ",
            "SERVICE_REQUEST_CREATE",
        }
    ),
    OrganizationRole.ORG_ADMIN: frozenset(
        {
            "MEMBER_READ",
            "MEMBER_INVITE",
            "MEMBER_ROLE_WRITE",
            "MEMBER_REMOVE",
            "QUOTA_READ",
            "ACTIVITY_READ",
            "APPROVAL_POLICY_READ",
            "SERVICE_REQUEST_CREATE",
        }
    ),
    OrganizationRole.ORG_OPERATOR: frozenset(
        {
            "MEMBER_READ",
            "QUOTA_READ",
            "ACTIVITY_READ",
            "APPROVAL_POLICY_READ",
            "SERVICE_REQUEST_CREATE",
        }
    ),
    OrganizationRole.ORG_VIEWER: frozenset(
        {"MEMBER_READ", "QUOTA_READ", "ACTIVITY_READ", "APPROVAL_POLICY_READ"}
    ),
    OrganizationRole.BILLING_VIEWER: frozenset({"QUOTA_READ"}),
}


class OrganizationGovernanceService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        request_id: str,
        source_ip: str | None,
    ) -> None:
        self._session = session
        self._principal = principal
        self._request_id = request_id
        self._source_ip = source_ip

    async def list_my_organizations(self) -> list[OrganizationMembershipResponse]:
        require_service_role(self._principal, UserRole.CUSTOMER)
        rows = await self._session.execute(
            select(OrganizationMember, Organization, User)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .join(User, User.id == OrganizationMember.user_id)
            .where(
                OrganizationMember.user_id == self._principal.user_id,
                OrganizationMember.status == "ACTIVE",
                or_(
                    OrganizationMember.expires_at.is_(None),
                    OrganizationMember.expires_at > datetime.now(UTC),
                ),
                Organization.is_active.is_(True),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(Organization.name)
        )
        return [self._member_response(*row) for row in rows.all()]

    async def list_members(
        self, organization_id: UUID
    ) -> list[OrganizationMembershipResponse]:
        await self._require_permission(organization_id, "MEMBER_READ")
        rows = await self._session.execute(
            select(OrganizationMember, Organization, User)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .join(User, User.id == OrganizationMember.user_id)
            .where(
                OrganizationMember.organization_id == organization_id,
                User.deleted_at.is_(None),
            )
            .order_by(User.display_name, User.email)
        )
        return [self._member_response(*row) for row in rows.all()]

    async def invite(
        self, organization_id: UUID, payload: OrganizationInvitationCreate
    ) -> OrganizationInvitationResponse:
        actor = await self._require_permission(organization_id, "MEMBER_INVITE")
        actor_role = OrganizationRole(actor.organization_role)
        if (
            payload.organization_role is OrganizationRole.ORG_OWNER
            and actor_role is not OrganizationRole.ORG_OWNER
        ):
            raise self._forbidden()
        now = datetime.now(UTC)
        expired = await self._session.scalars(
            select(OrganizationInvitation)
            .where(
                OrganizationInvitation.organization_id == organization_id,
                OrganizationInvitation.email == payload.email,
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.revoked_at.is_(None),
                OrganizationInvitation.expires_at <= now,
            )
            .with_for_update()
        )
        for invitation in expired:
            invitation.revoked_at = now
        existing_user = await self._session.scalar(
            select(User.id).where(
                User.email == payload.email,
                User.deleted_at.is_(None),
            )
        )
        if existing_user is not None:
            existing_member = await self._session.scalar(
                select(OrganizationMember.id).where(
                    OrganizationMember.organization_id == organization_id,
                    OrganizationMember.user_id == existing_user,
                )
            )
            if existing_member is not None:
                raise AppError(
                    409,
                    "ORGANIZATION_MEMBER_EXISTS",
                    "The user is already an organization member.",
                )
        token = token_urlsafe(32)
        item = OrganizationInvitation(
            id=uuid4(),
            organization_id=organization_id,
            email=payload.email,
            token_hash=sha256(token.encode()).digest(),
            organization_role=payload.organization_role.value,
            invited_by_id=self._principal.user_id,
            expires_at=now + timedelta(hours=payload.expires_in_hours),
        )
        self._session.add(item)
        self._audit(
            "ORGANIZATION_INVITATION_CREATED",
            organization_id,
            "organization_invitation",
            item.id,
            {"role": item.organization_role, "email_domain": payload.email.rpartition("@")[2]},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                409,
                "ORGANIZATION_INVITATION_EXISTS",
                "A pending invitation already exists.",
            ) from exc
        await self._session.refresh(item)
        return self._invitation_response(item, token=token)

    async def list_invitations(
        self, organization_id: UUID
    ) -> list[OrganizationInvitationResponse]:
        await self._require_permission(organization_id, "MEMBER_READ")
        items = await self._session.scalars(
            select(OrganizationInvitation)
            .where(OrganizationInvitation.organization_id == organization_id)
            .order_by(OrganizationInvitation.created_at.desc())
            .limit(100)
        )
        return [self._invitation_response(item) for item in items]

    async def revoke_invitation(self, organization_id: UUID, invitation_id: UUID) -> None:
        await self._require_permission(organization_id, "MEMBER_INVITE")
        item = await self._session.scalar(
            select(OrganizationInvitation)
            .where(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.organization_id == organization_id,
            )
            .with_for_update()
        )
        if item is None:
            raise AppError(404, "INVITATION_NOT_FOUND", "The invitation was not found.")
        if item.accepted_at is not None or item.revoked_at is not None:
            raise AppError(409, "INVITATION_NOT_PENDING", "The invitation is not pending.")
        item.revoked_at = datetime.now(UTC)
        self._audit(
            "ORGANIZATION_INVITATION_REVOKED",
            organization_id,
            "organization_invitation",
            item.id,
        )
        await self._session.commit()

    async def accept_invitation(self, token: str) -> OrganizationMembershipResponse:
        require_service_role(self._principal, UserRole.CUSTOMER)
        token_hash = sha256(token.encode()).digest()
        item = await self._session.scalar(
            select(OrganizationInvitation)
            .where(OrganizationInvitation.token_hash == token_hash)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            item is None
            or not hmac.compare_digest(item.token_hash, token_hash)
            or item.accepted_at is not None
            or item.revoked_at is not None
        ):
            raise AppError(404, "INVITATION_NOT_FOUND", "The invitation was not found.")
        if item.expires_at <= now:
            item.revoked_at = now
            await self._session.commit()
            raise AppError(410, "INVITATION_EXPIRED", "The invitation expired.")
        user = await self._session.get(User, self._principal.user_id)
        organization = await self._session.get(Organization, item.organization_id)
        if (
            user is None
            or not user.is_active
            or user.deleted_at is not None
            or organization is None
            or not organization.is_active
        ):
            raise AppError(404, "INVITATION_NOT_FOUND", "The invitation was not found.")
        if user.email != item.email:
            raise AppError(
                403,
                "INVITATION_EMAIL_MISMATCH",
                "Sign in with the invited email address.",
            )
        membership = await self._session.scalar(
            select(OrganizationMember)
            .where(
                OrganizationMember.organization_id == item.organization_id,
                OrganizationMember.user_id == user.id,
            )
            .with_for_update()
        )
        if membership is None:
            membership = OrganizationMember(
                id=uuid4(),
                organization_id=item.organization_id,
                user_id=user.id,
                added_by_id=item.invited_by_id,
                organization_role=item.organization_role,
                status="ACTIVE",
                version=1,
            )
            self._session.add(membership)
        else:
            membership.organization_role = item.organization_role
            membership.status = "ACTIVE"
            membership.expires_at = None
            membership.version += 1
        item.accepted_at = now
        item.accepted_by_id = user.id
        self._audit(
            "ORGANIZATION_INVITATION_ACCEPTED",
            item.organization_id,
            "organization_member",
            membership.id,
            {"role": membership.organization_role},
        )
        await self._session.commit()
        await self._session.refresh(membership)
        return self._member_response(membership, organization, user)

    async def update_member(
        self,
        organization_id: UUID,
        membership_id: UUID,
        payload: OrganizationRoleUpdate,
    ) -> OrganizationMembershipResponse:
        actor = await self._require_permission(organization_id, "MEMBER_ROLE_WRITE")
        item = await self._member(organization_id, membership_id, lock=True)
        if item.version != payload.version:
            raise AppError(409, "VERSION_CONFLICT", "The organization member changed.")
        actor_role = OrganizationRole(actor.organization_role)
        current_role = OrganizationRole(item.organization_role)
        if actor_role is not OrganizationRole.ORG_OWNER and (
            current_role is OrganizationRole.ORG_OWNER
            or payload.organization_role
            in {OrganizationRole.ORG_OWNER, OrganizationRole.ORG_ADMIN}
        ):
            raise self._forbidden()
        if payload.organization_role is OrganizationRole.ORG_OWNER and (
            payload.status == "SUSPENDED" or payload.expires_at is not None
        ):
            raise AppError(
                422,
                "ORGANIZATION_OWNER_MUST_BE_ACTIVE",
                "An organization owner cannot expire or be suspended.",
            )
        losing_owner = current_role is OrganizationRole.ORG_OWNER and (
            payload.organization_role is not OrganizationRole.ORG_OWNER
            or payload.status == "SUSPENDED"
            or payload.expires_at is not None
        )
        if losing_owner:
            await self._ensure_another_owner(organization_id, item.id)
        before = {
            "role": item.organization_role,
            "status": item.status,
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        }
        item.organization_role = payload.organization_role.value
        if payload.status is not None:
            item.status = payload.status
        item.expires_at = payload.expires_at
        item.version += 1
        self._audit(
            "ORGANIZATION_MEMBER_UPDATED",
            organization_id,
            "organization_member",
            item.id,
            {"before": before, "role": item.organization_role, "status": item.status},
        )
        await self._session.commit()
        return await self._member_response_by_id(item.id)

    async def remove_member(self, organization_id: UUID, membership_id: UUID) -> None:
        actor = await self._require_permission(organization_id, "MEMBER_REMOVE")
        item = await self._member(organization_id, membership_id, lock=True)
        actor_role = OrganizationRole(actor.organization_role)
        if (
            OrganizationRole(item.organization_role) is OrganizationRole.ORG_OWNER
            and actor_role is not OrganizationRole.ORG_OWNER
        ):
            raise self._forbidden()
        if item.organization_role == OrganizationRole.ORG_OWNER.value:
            await self._ensure_another_owner(organization_id, item.id)
        await self._session.delete(item)
        self._audit(
            "ORGANIZATION_MEMBER_REMOVED_BY_ORGANIZATION",
            organization_id,
            "organization_member",
            item.id,
            {"user_id": str(item.user_id)},
        )
        await self._session.commit()

    async def quota(self, organization_id: UUID) -> OrganizationQuotaResponse:
        await self._require_permission(organization_id, "QUOTA_READ")
        return await self._quota_response(organization_id, persist_snapshot=True)

    async def approval_policies(
        self, organization_id: UUID
    ) -> list[ApprovalPolicyResponse]:
        await self._require_permission(organization_id, "APPROVAL_POLICY_READ")
        items = await self._session.scalars(
            select(ApprovalPolicy)
            .where(ApprovalPolicy.organization_id == organization_id)
            .order_by(ApprovalPolicy.request_type)
        )
        return [ApprovalPolicyResponse.model_validate(item) for item in items]

    async def activity(
        self, organization_id: UUID, *, limit: int
    ) -> list[OrganizationActivityResponse]:
        await self._require_permission(organization_id, "ACTIVITY_READ")
        items = await self._session.scalars(
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return [
            OrganizationActivityResponse(
                id=item.id,
                created_at=item.created_at,
                action=item.action,
                outcome=item.outcome,
                actor_user_id=item.actor_user_id,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                summary={"error_code": item.error_code} if item.error_code else None,
            )
            for item in items
        ]

    async def _require_permission(
        self, organization_id: UUID, permission: str
    ) -> OrganizationMember:
        require_service_role(self._principal, UserRole.CUSTOMER)
        now = datetime.now(UTC)
        item = await self._session.scalar(
            select(OrganizationMember)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .join(User, User.id == OrganizationMember.user_id)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == self._principal.user_id,
                OrganizationMember.status == "ACTIVE",
                or_(
                    OrganizationMember.expires_at.is_(None),
                    OrganizationMember.expires_at > now,
                ),
                Organization.is_active.is_(True),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if item is None:
            raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
        role = OrganizationRole(item.organization_role)
        if permission not in ROLE_PERMISSIONS[role]:
            raise self._forbidden()
        return item

    async def _member(
        self, organization_id: UUID, membership_id: UUID, *, lock: bool
    ) -> OrganizationMember:
        statement = select(OrganizationMember).where(
            OrganizationMember.id == membership_id,
            OrganizationMember.organization_id == organization_id,
        )
        if lock:
            statement = statement.with_for_update()
        item = await self._session.scalar(statement)
        if item is None:
            raise AppError(404, "ORGANIZATION_MEMBER_NOT_FOUND", "The member was not found.")
        return item

    async def _ensure_another_owner(
        self, organization_id: UUID, excluded_membership_id: UUID
    ) -> None:
        owners = await self._session.scalars(
            select(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.id != excluded_membership_id,
                OrganizationMember.organization_role == OrganizationRole.ORG_OWNER.value,
                OrganizationMember.status == "ACTIVE",
                OrganizationMember.expires_at.is_(None),
            )
            .with_for_update()
        )
        if owners.first() is None:
            raise AppError(
                409,
                "LAST_ORGANIZATION_OWNER",
                "The last organization owner cannot be removed or demoted.",
            )

    async def _member_response_by_id(
        self, membership_id: UUID
    ) -> OrganizationMembershipResponse:
        row = (
            await self._session.execute(
                select(OrganizationMember, Organization, User)
                .join(Organization, Organization.id == OrganizationMember.organization_id)
                .join(User, User.id == OrganizationMember.user_id)
                .where(OrganizationMember.id == membership_id)
            )
        ).one()
        return self._member_response(*row)

    @staticmethod
    def _member_response(
        membership: OrganizationMember, organization: Organization, user: User
    ) -> OrganizationMembershipResponse:
        role = OrganizationRole(membership.organization_role)
        return OrganizationMembershipResponse(
            id=membership.id,
            organization_id=organization.id,
            organization_name=organization.name,
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            organization_role=role,
            status=membership.status,
            expires_at=membership.expires_at,
            created_at=membership.created_at,
            version=membership.version,
            permissions=sorted(ROLE_PERMISSIONS[role]),
        )

    @staticmethod
    def _invitation_response(
        item: OrganizationInvitation, *, token: str | None = None
    ) -> OrganizationInvitationResponse:
        return OrganizationInvitationResponse(
            id=item.id,
            organization_id=item.organization_id,
            email=item.email,
            organization_role=OrganizationRole(item.organization_role),
            expires_at=item.expires_at,
            accepted_at=item.accepted_at,
            revoked_at=item.revoked_at,
            created_at=item.created_at,
            accept_token=token,
        )

    async def _quota_response(
        self, organization_id: UUID, *, persist_snapshot: bool
    ) -> OrganizationQuotaResponse:
        quota = await self._session.scalar(
            select(OrganizationQuota)
            .where(OrganizationQuota.organization_id == organization_id)
            .execution_options(populate_existing=True)
        )
        limits, usage, reserved = await quota_state(
            self._session, organization_id, lock=False
        )
        captured_at = datetime.now(UTC)
        if persist_snapshot:
            self._session.add(
                QuotaUsageSnapshot(
                    organization_id=organization_id,
                    used_vcpu=usage["vcpu"],
                    used_memory_bytes=usage["memory_bytes"],
                    used_disk_bytes=usage["disk_bytes"],
                    used_vms=usage["vms"],
                    used_ips=usage["ips"],
                    used_backup_bytes=usage["backup_bytes"],
                    captured_at=captured_at,
                )
            )
            await self._session.commit()
        remaining = {
            key: max(0, limits[key] - usage[key] - reserved[key]) for key in limits
        }
        return OrganizationQuotaResponse(
            organization_id=organization_id,
            limits=QuotaValues(**limits),
            usage=QuotaValues(**usage),
            reserved=QuotaValues(**reserved),
            remaining=QuotaValues(**remaining),
            version=quota.version if quota else 0,
            updated_at=quota.updated_at if quota else None,
            captured_at=captured_at,
        )

    def _audit(
        self,
        action: str,
        organization_id: UUID,
        target_type: str,
        target_id: UUID,
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
            source_ip=self._source_ip,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )

    @staticmethod
    def _forbidden() -> AppError:
        return AppError(
            403,
            "ORGANIZATION_PERMISSION_DENIED",
            "You do not have permission for this organization action.",
        )


class AdminOrganizationGovernanceService(OrganizationGovernanceService):
    async def set_quota(
        self, organization_id: UUID, payload: OrganizationQuotaUpdate
    ) -> OrganizationQuotaResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        organization = await self._session.get(Organization, organization_id)
        if organization is None:
            raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
        _, usage, reserved = await quota_state(
            self._session, organization_id, lock=True
        )
        requested_limits = {
            "vcpu": payload.max_vcpu,
            "memory_bytes": payload.max_memory_bytes,
            "disk_bytes": payload.max_disk_bytes,
            "vms": payload.max_vms,
            "ips": payload.max_ips,
            "backup_bytes": payload.max_backup_bytes,
        }
        below_commitment = [
            key
            for key, value in requested_limits.items()
            if value < usage[key] + reserved[key]
        ]
        if below_commitment:
            raise AppError(
                409,
                "ORGANIZATION_QUOTA_BELOW_COMMITMENT",
                "Quota limits cannot be lower than current usage and reservations.",
                details={"resources": below_commitment},
            )
        item = await self._session.scalar(
            select(OrganizationQuota)
            .where(OrganizationQuota.organization_id == organization_id)
            .with_for_update()
        )
        if item is None:
            if payload.version is not None:
                raise AppError(409, "VERSION_CONFLICT", "The quota policy changed.")
            item = OrganizationQuota(
                organization_id=organization_id,
                updated_by_id=self._principal.user_id,
                version=1,
                **payload.model_dump(exclude={"version"}),
            )
            self._session.add(item)
        else:
            if payload.version != item.version:
                raise AppError(409, "VERSION_CONFLICT", "The quota policy changed.")
            for key, value in payload.model_dump(exclude={"version"}).items():
                setattr(item, key, value)
            item.updated_by_id = self._principal.user_id
            item.version += 1
        self._audit(
            "ORGANIZATION_QUOTA_UPDATED",
            organization_id,
            "organization_quota",
            organization_id,
            {"version": item.version},
        )
        await self._session.commit()
        return await self._quota_response(organization_id, persist_snapshot=True)

    async def get_quota(self, organization_id: UUID) -> OrganizationQuotaResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        organization = await self._session.get(Organization, organization_id)
        if organization is None:
            raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
        return await self._quota_response(organization_id, persist_snapshot=False)

    async def set_approval_policy(
        self, organization_id: UUID, payload: ApprovalPolicyUpdate
    ) -> ApprovalPolicyResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        organization = await self._session.get(Organization, organization_id)
        if organization is None:
            raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
        item = await self._session.scalar(
            select(ApprovalPolicy)
            .where(
                ApprovalPolicy.organization_id == organization_id,
                ApprovalPolicy.request_type == payload.request_type,
            )
            .with_for_update()
        )
        if item is None:
            if payload.version is not None:
                raise AppError(409, "VERSION_CONFLICT", "The approval policy changed.")
            item = ApprovalPolicy(
                organization_id=organization_id,
                request_type=payload.request_type,
                requires_approval=payload.requires_approval,
                minimum_role=payload.minimum_role.value,
                updated_by_id=self._principal.user_id,
                version=1,
            )
            self._session.add(item)
        else:
            if payload.version != item.version:
                raise AppError(409, "VERSION_CONFLICT", "The approval policy changed.")
            item.requires_approval = payload.requires_approval
            item.minimum_role = payload.minimum_role.value
            item.updated_by_id = self._principal.user_id
            item.version += 1
        self._audit(
            "ORGANIZATION_APPROVAL_POLICY_UPDATED",
            organization_id,
            "approval_policy",
            item.id,
            {"request_type": item.request_type, "version": item.version},
        )
        await self._session.commit()
        await self._session.refresh(item)
        return ApprovalPolicyResponse.model_validate(item)

    async def list_approval_policies_admin(
        self, organization_id: UUID
    ) -> list[ApprovalPolicyResponse]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        items = await self._session.scalars(
            select(ApprovalPolicy)
            .where(ApprovalPolicy.organization_id == organization_id)
            .order_by(ApprovalPolicy.request_type)
        )
        return [ApprovalPolicyResponse.model_validate(item) for item in items]
