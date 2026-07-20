from dataclasses import dataclass
from uuid import UUID

from app.core.errors import AppError
from app.models.auth import UserRole


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    role: UserRole
    session_epoch: int


def require_service_role(principal: Principal, *roles: UserRole) -> None:
    if principal.role not in roles:
        raise AppError(
            status_code=403,
            code="FORBIDDEN",
            message="You do not have permission to perform this action.",
        )
