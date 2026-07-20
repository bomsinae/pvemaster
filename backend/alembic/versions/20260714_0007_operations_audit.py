"""Expand immutable audit records for operations.

Revision ID: 20260714_0007
Revises: 20260714_0006
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_0007"
down_revision: str | None = "20260714_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("audit_logs", "occurred_at", new_column_name="created_at")
    op.alter_column("audit_logs", "target_type", new_column_name="resource_type")
    op.alter_column("audit_logs", "target_id", new_column_name="resource_id")
    op.add_column("audit_logs", sa.Column("user_agent", sa.String(length=512)))
    op.add_column("audit_logs", sa.Column("before", sa.JSON()))
    op.add_column("audit_logs", sa.Column("after", sa.JSON()))
    op.add_column("audit_logs", sa.Column("result", sa.String(length=20)))
    op.add_column("audit_logs", sa.Column("error_code", sa.String(length=64)))
    op.execute("UPDATE audit_logs SET result = outcome, after = details")
    op.alter_column("audit_logs", "result", nullable=False)
    op.drop_column("audit_logs", "details")
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_action_created", "audit_logs", ["action", "created_at"])
    op.execute(
        """
        CREATE FUNCTION prevent_audit_log_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE'
             AND current_setting('app.audit_retention', true) = 'on' THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'audit_logs are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
          BEFORE UPDATE OR DELETE ON audit_logs
          FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation()")
    op.drop_index("ix_audit_logs_action_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.add_column(
        "audit_logs",
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.execute("UPDATE audit_logs SET details = COALESCE(after, '{}')")
    op.drop_column("audit_logs", "error_code")
    op.drop_column("audit_logs", "result")
    op.drop_column("audit_logs", "after")
    op.drop_column("audit_logs", "before")
    op.drop_column("audit_logs", "user_agent")
    op.alter_column("audit_logs", "resource_id", new_column_name="target_id")
    op.alter_column("audit_logs", "resource_type", new_column_name="target_type")
    op.alter_column("audit_logs", "created_at", new_column_name="occurred_at")
