"""Add template operating system type and protected Windows initial password.

Revision ID: 20260731_0026
Revises: 20260726_0025
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260731_0026"
down_revision: str | None = "20260726_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("os_type", sa.String(length=16), server_default="LINUX", nullable=False),
    )
    op.create_check_constraint(
        "ck_templates_os_type",
        "templates",
        "os_type IN ('LINUX','WINDOWS')",
    )
    op.create_check_constraint(
        "ck_templates_os_type_legacy_flag",
        "templates",
        "(os_type = 'LINUX' AND linux_only) "
        "OR (os_type = 'WINDOWS' AND NOT linux_only)",
    )
    op.add_column(
        "provisioning_requests",
        sa.Column("initial_password_ciphertext", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "provisioning_requests",
        sa.Column("initial_password_nonce", sa.LargeBinary(length=12), nullable=True),
    )
    op.add_column(
        "provisioning_requests",
        sa.Column("initial_password_key_version", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "provisioning_requests",
        sa.Column(
            "initial_password_cleared_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("provisioning_requests", "initial_password_cleared_at")
    op.drop_column("provisioning_requests", "initial_password_key_version")
    op.drop_column("provisioning_requests", "initial_password_nonce")
    op.drop_column("provisioning_requests", "initial_password_ciphertext")
    op.drop_constraint("ck_templates_os_type_legacy_flag", "templates", type_="check")
    op.drop_constraint("ck_templates_os_type", "templates", type_="check")
    op.drop_column("templates", "os_type")
