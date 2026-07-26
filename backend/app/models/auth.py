from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class UserRole(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    OPERATOR = "OPERATOR"
    CUSTOMER = "CUSTOMER"


class OrganizationRole(StrEnum):
    ORG_OWNER = "ORG_OWNER"
    ORG_ADMIN = "ORG_ADMIN"
    ORG_OPERATOR = "ORG_OPERATOR"
    ORG_VIEWER = "ORG_VIEWER"
    BILLING_VIEWER = "BILLING_VIEWER"


class MfaMethodType(StrEnum):
    TOTP = "TOTP"
    WEBAUTHN = "WEBAUTHN"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('SUPER_ADMIN', 'OPERATOR', 'CUSTOMER')",
            name="ck_users_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        CheckConstraint(
            "organization_role IN "
            "('ORG_OWNER','ORG_ADMIN','ORG_OPERATOR','ORG_VIEWER','BILLING_VIEWER')",
            name="ck_organization_members_role",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED')",
            name="ck_organization_members_status",
        ),
        Index(
            "ix_organization_members_active",
            "organization_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    added_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    organization_role: Mapped[str] = mapped_column(
        String(24), nullable=False, default=OrganizationRole.ORG_OPERATOR.value
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_active", "user_id", "revoked_at", "expires_at"),
        Index("ix_refresh_tokens_family", "family_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("refresh_tokens.id"))
    replaced_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("refresh_tokens.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reuse_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_label: Mapped[str | None] = mapped_column(String(120))
    created_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assurance_level: Mapped[str] = mapped_column(String(16), nullable=False, default="PASSWORD")


class MfaMethod(Base):
    __tablename__ = "mfa_methods"
    __table_args__ = (
        CheckConstraint("type IN ('TOTP','WEBAUTHN')", name="ck_mfa_methods_type"),
        Index("ix_mfa_methods_user_active", "user_id", "disabled_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
    key_version: Mapped[str | None] = mapped_column(String(20))
    credential_id: Mapped[bytes | None] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MfaChallenge(Base):
    __tablename__ = "mfa_challenges"
    __table_args__ = (Index("ix_mfa_challenges_user_expires", "user_id", "expires_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    challenge: Mapped[bytes | None] = mapped_column(LargeBinary)
    context: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    __table_args__ = (Index("ix_recovery_codes_user_unused", "user_id", "used_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityPolicy(Base):
    __tablename__ = "security_policies"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    admin_mfa_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LoginThrottle(Base):
    __tablename__ = "login_throttles"

    key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_role: Mapped[str | None] = mapped_column(String(20))
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    workload_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workloads.id", ondelete="SET NULL")
    )
    operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operations.id", ondelete="SET NULL")
    )
    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    pve_upid: Mapped[str | None] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(100))
    before: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after: Mapped[dict[str, object] | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
