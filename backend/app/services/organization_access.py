from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.models.auth import OrganizationMember

WORKLOAD_READ_ROLES = ("ORG_OWNER", "ORG_ADMIN", "ORG_OPERATOR", "ORG_VIEWER")
WORKLOAD_OPERATE_ROLES = ("ORG_OWNER", "ORG_ADMIN", "ORG_OPERATOR")


def active_membership_conditions(
    *,
    user_id: UUID | InstrumentedAttribute[UUID],
    organization_id: UUID | None | InstrumentedAttribute[UUID | None],
    roles: tuple[str, ...] | None = None,
) -> tuple[ColumnElement[bool], ...]:
    """Return the shared active, non-expired organization membership boundary."""
    conditions: tuple[ColumnElement[bool], ...] = (
        OrganizationMember.user_id == user_id,
        OrganizationMember.organization_id == organization_id,
        OrganizationMember.status == "ACTIVE",
        or_(
            OrganizationMember.expires_at.is_(None),
            OrganizationMember.expires_at > func.now(),
        ),
    )
    if roles is not None:
        conditions += (OrganizationMember.organization_role.in_(roles),)
    return conditions
