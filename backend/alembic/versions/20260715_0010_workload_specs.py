"""Store normalized VM and CT specifications.

Revision ID: 20260715_0010
Revises: 20260715_0009
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_0010"
down_revision: str | None = "20260715_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workloads", sa.Column("cpu_cores", sa.Integer(), nullable=True))
    op.add_column("workloads", sa.Column("memory_bytes", sa.BigInteger(), nullable=True))
    op.add_column("workloads", sa.Column("disk_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("workloads", "disk_bytes")
    op.drop_column("workloads", "memory_bytes")
    op.drop_column("workloads", "cpu_cores")
