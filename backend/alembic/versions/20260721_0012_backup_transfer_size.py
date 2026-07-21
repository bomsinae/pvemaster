"""Track bytes newly transferred by PBS backup runs.

Revision ID: 20260721_0012
Revises: 20260721_0011
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260721_0012"
down_revision: str | None = "20260721_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backup_runs", sa.Column("transferred_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("backup_runs", "transferred_bytes")
