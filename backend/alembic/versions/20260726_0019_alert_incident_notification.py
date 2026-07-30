"""Add persistent alerts, notification delivery, and maintenance windows.

Revision ID: 20260726_0019
Revises: 20260726_0018
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0019"
down_revision: str | None = "20260726_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(120)),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("workload_id", sa.Uuid()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("assigned_to_id", sa.Uuid()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("silenced_until", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["assigned_to_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index(
        "ix_alerts_status_severity_seen",
        "alerts",
        ["status", "severity", "last_seen_at"],
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("actor_user_id", sa.Uuid()),
        sa.Column("note", sa.Text()),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_events_alert_created", "alert_events", ["alert_id", "created_at"])
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("config_nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("key_version", sa.String(20), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "notification_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("severities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("quiet_hours", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("escalation_minutes", sa.Integer()),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("alert_event_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["alert_event_id"], ["alert_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_event_id", "channel_id"),
    )
    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(120)),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suppress_notifications", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_windows_active",
        "maintenance_windows",
        ["starts_at", "ends_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_maintenance_windows_active", table_name="maintenance_windows")
    op.drop_table("maintenance_windows")
    op.drop_index("ix_notification_deliveries_due", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_rules")
    op.drop_table("notification_channels")
    op.drop_index("ix_alert_events_alert_created", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_index("ix_alerts_status_severity_seen", table_name="alerts")
    op.drop_table("alerts")
