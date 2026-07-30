"""Add MFA methods, challenges, recovery codes, and session metadata.

Revision ID: 20260726_0018
Revises: 20260724_0017
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0018"
down_revision: str | None = "20260724_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("device_label", sa.String(120)))
    op.add_column("refresh_tokens", sa.Column("created_ip", sa.String(64)))
    op.add_column("refresh_tokens", sa.Column("user_agent", sa.String(512)))
    op.add_column("refresh_tokens", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.add_column(
        "refresh_tokens",
        sa.Column("mfa_authenticated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column(
            "assurance_level",
            sa.String(16),
            nullable=False,
            server_default="PASSWORD",
        ),
    )

    op.create_table(
        "mfa_methods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary()),
        sa.Column("secret_nonce", sa.LargeBinary(12)),
        sa.Column("key_version", sa.String(20)),
        sa.Column("credential_id", sa.LargeBinary(), unique=True),
        sa.Column("public_key", sa.LargeBinary()),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transports", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("type IN ('TOTP','WEBAUTHN')", name="ck_mfa_methods_type"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mfa_methods_user_active", "mfa_methods", ["user_id", "disabled_at"])

    op.create_table(
        "mfa_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("challenge", sa.LargeBinary()),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mfa_challenges_user_expires",
        "mfa_challenges",
        ["user_id", "expires_at"],
    )

    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(32), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recovery_codes_user_unused",
        "recovery_codes",
        ["user_id", "used_at"],
    )
    op.create_table(
        "security_policies",
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column(
            "admin_mfa_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("updated_by_id", sa.Uuid()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )
    op.execute(
        sa.text("INSERT INTO security_policies (key, admin_mfa_required) VALUES ('default', false)")
    )


def downgrade() -> None:
    op.drop_table("security_policies")
    op.drop_index("ix_recovery_codes_user_unused", table_name="recovery_codes")
    op.drop_table("recovery_codes")
    op.drop_index("ix_mfa_challenges_user_expires", table_name="mfa_challenges")
    op.drop_table("mfa_challenges")
    op.drop_index("ix_mfa_methods_user_active", table_name="mfa_methods")
    op.drop_table("mfa_methods")
    for column in (
        "assurance_level",
        "mfa_authenticated_at",
        "last_seen_at",
        "user_agent",
        "created_ip",
        "device_label",
    ):
        op.drop_column("refresh_tokens", column)
