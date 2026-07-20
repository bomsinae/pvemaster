from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_context
from app.core.middleware import source_ip_context, user_agent_context
from app.models.auth import AuditLog, UserRole

_SENSITIVE_PARTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "private_key",
    "ca_bundle",
)
_MASK = "[REDACTED]"


def sanitize_audit_value(value: object, *, key: str = "") -> object:
    """Return an allow-to-store copy with secret-bearing fields masked recursively."""
    normalized_key = key.casefold().replace("-", "_")
    if any(part in normalized_key for part in _SENSITIVE_PARTS):
        return _MASK
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_audit_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def add_audit_event(
    session: AsyncSession,
    *,
    action: str,
    outcome: str,
    request_id: str | None,
    actor_user_id: UUID | None = None,
    actor_role: UserRole | None = None,
    organization_id: UUID | None = None,
    workload_id: UUID | None = None,
    operation_id: UUID | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
    pve_upid: str | None = None,
    target_type: str | None = None,
    target_id: UUID | str | None = None,
    details: dict[str, object] | None = None,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    error_code: str | None = None,
) -> None:
    safe_before = sanitize_audit_value(before) if before is not None else None
    combined_after = after if after is not None else details
    safe_after = sanitize_audit_value(combined_after) if combined_after is not None else None
    if not isinstance(safe_before, (dict, type(None))) or not isinstance(
        safe_after, (dict, type(None))
    ):
        raise TypeError("audit before/after values must be mappings")
    session.add(
        AuditLog(
            action=action,
            outcome=outcome,
            actor_user_id=actor_user_id,
            actor_role=actor_role.value if actor_role else None,
            organization_id=organization_id,
            workload_id=workload_id,
            operation_id=operation_id,
            source_ip=source_ip or source_ip_context.get(),
            user_agent=(user_agent or user_agent_context.get() or "")[:512] or None,
            pve_upid=pve_upid,
            resource_type=target_type,
            resource_id=str(target_id) if target_id is not None else None,
            request_id=request_id or request_id_context.get(),
            before=safe_before,
            after=safe_after,
            result=outcome,
            error_code=error_code,
        )
    )
