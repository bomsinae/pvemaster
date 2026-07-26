from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import AppError
from app.models.auth import (
    Organization,
    OrganizationMember,
    OrganizationRole,
    RefreshToken,
    User,
    UserRole,
)
from app.models.operation import WorkloadAssignment
from app.models.provisioning import ProvisioningRequest
from app.schemas.auth import (
    AdminPasswordResetRequest,
    ChangePasswordRequest,
    OrganizationCreate,
    OrganizationMemberCreate,
    OrganizationMemberDetailResponse,
    OrganizationMemberResponse,
    OrganizationResponse,
    OrganizationUpdate,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from app.security.access import Principal, require_service_role
from app.security.passwords import PasswordManager
from app.services.audit import add_audit_event


class AccountService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        passwords: PasswordManager,
        request_id: str,
    ) -> None:
        self._session = session
        self._principal = principal
        self._passwords = passwords
        self._request_id = request_id

    async def me(self) -> UserResponse:
        user = await self._get_user(self._principal.user_id)
        return UserResponse.model_validate(user)

    async def change_password(self, request: ChangePasswordRequest) -> None:
        user = await self._get_user(self._principal.user_id, lock=True)
        if not self._passwords.verify(
            user.password_hash, request.current_password.get_secret_value()
        ):
            raise AppError(
                status_code=400,
                code="CURRENT_PASSWORD_INVALID",
                message="The current password is invalid.",
            )
        user.password_hash = self._passwords.hash(request.new_password.get_secret_value())
        user.version += 1
        token_filters = [
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        ]
        if request.revoke_all_sessions:
            user.session_epoch += 1
        else:
            token_filters.append(RefreshToken.family_id != self._principal.session_id)
        await self._session.execute(
            update(RefreshToken).where(*token_filters).values(revoked_at=datetime.now(UTC))
        )
        self._audit(
            "USER_PASSWORD_CHANGED",
            user.id,
            {"all_sessions_revoked": request.revoke_all_sessions},
        )
        await self._session.commit()

    async def create_user(self, request: UserCreate) -> UserResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        user = User(
            email=request.email,
            display_name=request.display_name.strip(),
            role=request.role.value,
            password_hash=self._passwords.hash(request.password.get_secret_value()),
            created_by_id=self._principal.user_id,
            is_active=True,
            version=1,
        )
        self._session.add(user)
        await self._flush_conflict("A user with this email already exists.")
        await self._session.refresh(user)
        self._audit("USER_CREATED", user.id)
        await self._session.commit()
        return UserResponse.model_validate(user)

    async def list_users(self) -> list[UserResponse]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        users = await self._session.scalars(
            select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc())
        )
        user_items = users.all()
        membership_rows = await self._session.execute(
            select(OrganizationMember.user_id, Organization.name)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .order_by(Organization.name.asc())
        )
        organization_names_by_user: dict[UUID, list[str]] = {}
        for user_id, organization_name in membership_rows.all():
            organization_names_by_user.setdefault(user_id, []).append(organization_name)
        return [
            UserResponse.model_validate(user).model_copy(
                update={"organization_names": organization_names_by_user.get(user.id, [])}
            )
            for user in user_items
        ]

    async def update_user(self, user_id: UUID, request: UserUpdate) -> UserResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        user = await self._get_user(user_id, lock=True)
        before = {
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
        }
        if request.version is not None and request.version != user.version:
            raise AppError(
                status_code=409,
                code="USER_VERSION_CONFLICT",
                message="The user was modified by another request.",
            )
        removing_super_admin = user.role == UserRole.SUPER_ADMIN.value and (
            request.role not in (None, UserRole.SUPER_ADMIN) or request.is_active is False
        )
        if removing_super_admin:
            count = await self._session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.SUPER_ADMIN.value, User.is_active.is_(True))
            )
            if count is None or count <= 1:
                raise AppError(
                    status_code=409,
                    code="LAST_SUPER_ADMIN",
                    message="The last active super administrator cannot be changed.",
                )
        if request.display_name is not None:
            user.display_name = request.display_name.strip()
        if request.role is not None:
            user.role = request.role.value
        if request.is_active is not None and request.is_active != user.is_active:
            user.is_active = request.is_active
            user.disabled_at = None if request.is_active else datetime.now(UTC)
            user.session_epoch += 1
            await self._session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
        user.version += 1
        after = {
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
        }
        if before["role"] != after["role"]:
            action = "USER_ROLE_CHANGED"
        elif before["is_active"] is True and after["is_active"] is False:
            action = "USER_DISABLED"
        else:
            action = "USER_UPDATED"
        add_audit_event(
            self._session,
            action=action,
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="user",
            target_id=user.id,
            before=before,
            after=after,
        )
        await self._session.commit()
        await self._session.refresh(user)
        return UserResponse.model_validate(user)

    async def delete_user(self, user_id: UUID, *, version: int) -> None:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        if user_id == self._principal.user_id:
            raise AppError(
                status_code=409,
                code="USER_SELF_DELETE",
                message="The current user cannot delete their own account.",
            )
        user = await self._get_user(user_id, lock=True)
        if version != user.version:
            raise AppError(
                status_code=409,
                code="USER_VERSION_CONFLICT",
                message="The user was modified by another request.",
            )
        if user.role == UserRole.SUPER_ADMIN.value and user.is_active:
            count = await self._session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role == UserRole.SUPER_ADMIN.value,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
            if count is None or count <= 1:
                raise AppError(
                    status_code=409,
                    code="LAST_SUPER_ADMIN",
                    message="The last active super administrator cannot be deleted.",
                )

        now = datetime.now(UTC)
        before = {
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
        }
        await self._session.execute(
            delete(OrganizationMember).where(OrganizationMember.user_id == user.id)
        )
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        user.email = f"deleted+{user.id}@deleted.invalid"
        user.display_name = "삭제된 사용자"
        user.role = UserRole.CUSTOMER.value
        user.is_active = False
        user.disabled_at = now
        user.deleted_at = now
        user.session_epoch += 1
        user.version += 1
        add_audit_event(
            self._session,
            action="USER_DELETED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            target_type="user",
            target_id=user.id,
            before=before,
            after={"is_active": False, "deleted": True},
        )
        await self._session.commit()

    async def reset_password(
        self,
        user_id: UUID,
        request: AdminPasswordResetRequest,
    ) -> None:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        user = await self._get_user(user_id, lock=True)
        user.password_hash = self._passwords.hash(request.new_password.get_secret_value())
        user.session_epoch += 1
        user.version += 1
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        self._audit("USER_PASSWORD_RESET", user.id)
        await self._session.commit()

    async def create_organization(self, request: OrganizationCreate) -> OrganizationResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        organization = Organization(
            name=request.name.strip(),
            created_by_id=self._principal.user_id,
            is_active=True,
            version=1,
        )
        self._session.add(organization)
        await self._flush_conflict("An organization with this name already exists.")
        await self._session.refresh(organization)
        self._audit("ORGANIZATION_CREATED", organization.id)
        await self._session.commit()
        return OrganizationResponse.model_validate(organization)

    async def list_organizations(
        self,
        *,
        q: str | None = None,
        status: Literal["active", "inactive", "all"] = "active",
        sort: Literal["newest", "oldest", "name"] = "newest",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[OrganizationResponse], int]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        conditions: list[ColumnElement[bool]] = []
        if status == "active":
            conditions.append(Organization.is_active.is_(True))
        elif status == "inactive":
            conditions.append(Organization.is_active.is_(False))
        normalized_query = q.strip() if q else ""
        if normalized_query:
            escaped_query = (
                normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            pattern = f"%{escaped_query}%"
            conditions.append(
                or_(
                    Organization.name.ilike(pattern, escape="\\"),
                    cast(Organization.id, String).ilike(pattern, escape="\\"),
                )
            )

        total = await self._session.scalar(
            select(func.count()).select_from(Organization).where(*conditions)
        )
        statement = select(Organization).where(*conditions)
        if sort == "name":
            statement = statement.order_by(Organization.name.asc(), Organization.id.asc())
        elif sort == "oldest":
            statement = statement.order_by(Organization.created_at.asc(), Organization.id.asc())
        else:
            statement = statement.order_by(Organization.created_at.desc(), Organization.id.asc())
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        organizations = await self._session.scalars(statement)
        return (
            [OrganizationResponse.model_validate(item) for item in organizations.all()],
            total or 0,
        )

    async def update_organization(
        self,
        organization_id: UUID,
        request: OrganizationUpdate,
    ) -> OrganizationResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        organization = await self._get_organization(
            organization_id, lock=True, include_inactive=True
        )
        if request.version != organization.version:
            raise AppError(
                status_code=409,
                code="ORGANIZATION_VERSION_CONFLICT",
                message="The organization was modified by another request.",
            )
        before = {"name": organization.name, "is_active": organization.is_active}
        if request.name is not None:
            organization.name = request.name.strip()
        if request.is_active is False and organization.is_active:
            raise AppError(
                status_code=422,
                code="ORGANIZATION_DEACTIVATION_REQUIRES_DELETE",
                message="Use the organization delete endpoint to validate and deactivate it.",
            )
        if request.is_active is True:
            organization.is_active = True
        organization.version += 1
        add_audit_event(
            self._session,
            action="ORGANIZATION_UPDATED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=organization.id,
            target_type="organization",
            target_id=organization.id,
            before=before,
            after={"name": organization.name, "is_active": organization.is_active},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="ORGANIZATION_NAME_CONFLICT",
                message="An organization with this name already exists.",
            ) from exc
        await self._session.refresh(organization)
        return OrganizationResponse.model_validate(organization)

    async def delete_organization(self, organization_id: UUID, *, version: int) -> None:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        organization = await self._get_organization(organization_id, lock=True)
        if version != organization.version:
            raise AppError(
                status_code=409,
                code="ORGANIZATION_VERSION_CONFLICT",
                message="The organization was modified by another request.",
            )
        member_count = await self._session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == organization.id)
        )
        assignment_count = await self._session.scalar(
            select(func.count())
            .select_from(WorkloadAssignment)
            .where(
                WorkloadAssignment.organization_id == organization.id,
                WorkloadAssignment.revoked_at.is_(None),
            )
        )
        provisioning_count = await self._session.scalar(
            select(func.count())
            .select_from(ProvisioningRequest)
            .where(
                ProvisioningRequest.organization_id == organization.id,
                ProvisioningRequest.status.in_(["QUEUED", "RUNNING", "MANUAL_REVIEW"]),
            )
        )
        if (member_count or 0) or (assignment_count or 0) or (provisioning_count or 0):
            raise AppError(
                status_code=409,
                code="ORGANIZATION_IN_USE",
                message=(
                    "Remove members, workload assignments, and active provisioning requests "
                    "before deleting the organization."
                ),
                details={
                    "members": int(member_count or 0),
                    "workload_assignments": int(assignment_count or 0),
                    "active_provisioning_requests": int(provisioning_count or 0),
                },
            )
        organization.is_active = False
        organization.version += 1
        add_audit_event(
            self._session,
            action="ORGANIZATION_DELETED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=organization.id,
            target_type="organization",
            target_id=organization.id,
            before={"name": organization.name, "is_active": True},
            after={"name": organization.name, "is_active": False},
        )
        await self._session.commit()

    async def add_member(
        self,
        organization_id: UUID,
        request: OrganizationMemberCreate,
    ) -> OrganizationMemberResponse:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        organization = await self._session.get(Organization, organization_id)
        user = await self._session.get(User, request.user_id)
        if organization is None or not organization.is_active:
            raise AppError(
                status_code=404,
                code="ORGANIZATION_NOT_FOUND",
                message="The organization was not found.",
            )
        if user is None or not user.is_active:
            raise AppError(
                status_code=404,
                code="USER_NOT_FOUND",
                message="The user was not found.",
            )
        membership = OrganizationMember(
            organization_id=organization.id,
            user_id=user.id,
            added_by_id=self._principal.user_id,
            organization_role=request.organization_role.value,
            status="ACTIVE",
            version=1,
        )
        self._session.add(membership)
        await self._flush_conflict("The user is already an organization member.")
        await self._session.refresh(membership)
        self._audit("ORGANIZATION_MEMBER_ADDED", membership.id)
        await self._session.commit()
        return OrganizationMemberResponse.model_validate(membership)

    async def list_members(self, organization_id: UUID) -> list[OrganizationMemberDetailResponse]:
        require_service_role(self._principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        await self._get_organization(organization_id, include_inactive=True)
        rows = await self._session.execute(
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.organization_id == organization_id)
            .order_by(User.display_name.asc(), User.email.asc())
        )
        return [
            OrganizationMemberDetailResponse(
                id=membership.id,
                organization_id=membership.organization_id,
                user_id=membership.user_id,
                organization_role=OrganizationRole(membership.organization_role),
                status=membership.status,
                expires_at=membership.expires_at,
                created_at=membership.created_at,
                version=membership.version,
                email=user.email,
                display_name=user.display_name,
                role=UserRole(user.role),
                is_active=user.is_active,
            )
            for membership, user in rows.all()
        ]

    async def remove_member(self, organization_id: UUID, user_id: UUID) -> None:
        require_service_role(self._principal, UserRole.SUPER_ADMIN)
        membership = await self._session.scalar(
            select(OrganizationMember)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
            )
            .with_for_update()
        )
        if membership is None:
            raise AppError(
                status_code=404,
                code="ORGANIZATION_MEMBER_NOT_FOUND",
                message="The organization member was not found.",
            )
        if membership.organization_role == OrganizationRole.ORG_OWNER.value:
            other_owner = await self._session.scalar(
                select(OrganizationMember.id)
                .where(
                    OrganizationMember.organization_id == organization_id,
                    OrganizationMember.id != membership.id,
                    OrganizationMember.organization_role == OrganizationRole.ORG_OWNER.value,
                    OrganizationMember.status == "ACTIVE",
                    OrganizationMember.expires_at.is_(None),
                )
                .with_for_update()
            )
            if other_owner is None:
                raise AppError(
                    status_code=409,
                    code="LAST_ORGANIZATION_OWNER",
                    message="The last organization owner cannot be removed.",
                )
        await self._session.delete(membership)
        add_audit_event(
            self._session,
            action="ORGANIZATION_MEMBER_REMOVED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=organization_id,
            target_type="organization_member",
            target_id=membership.id,
            before={"user_id": str(user_id)},
        )
        await self._session.commit()

    async def _get_user(self, user_id: UUID, *, lock: bool = False) -> User:
        query = select(User).where(User.id == user_id, User.deleted_at.is_(None))
        if lock:
            query = query.with_for_update()
        user = await self._session.scalar(query)
        if user is None:
            raise AppError(
                status_code=404,
                code="USER_NOT_FOUND",
                message="The user was not found.",
            )
        return user

    async def _get_organization(
        self,
        organization_id: UUID,
        *,
        lock: bool = False,
        include_inactive: bool = False,
    ) -> Organization:
        query = select(Organization).where(Organization.id == organization_id)
        if not include_inactive:
            query = query.where(Organization.is_active.is_(True))
        if lock:
            query = query.with_for_update()
        organization = await self._session.scalar(query)
        if organization is None:
            raise AppError(
                status_code=404,
                code="ORGANIZATION_NOT_FOUND",
                message="The organization was not found.",
            )
        return organization

    async def _flush_conflict(self, message: str) -> None:
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(status_code=409, code="RESOURCE_CONFLICT", message=message) from exc

    def _audit(
        self,
        action: str,
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
            target_type="account",
            target_id=target_id,
            details=details,
        )
