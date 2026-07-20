import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import delete, text
from sqlalchemy.engine import CursorResult

from app.core.config import get_settings
from app.db import create_engine, create_session_factory
from app.models.auth import AuditLog
from app.worker import celery_app


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.tasks.maintenance.purge_expired_audit_logs"
)
def purge_expired_audit_logs() -> int:
    return asyncio.run(_purge_expired_audit_logs())


async def _purge_expired_audit_logs() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
            result = await session.execute(
                delete(AuditLog).where(
                    AuditLog.created_at
                    < datetime.now(UTC) - timedelta(days=settings.audit_retention_days)
                )
            )
            await session.commit()
            return cast(CursorResult[object], result).rowcount or 0
    finally:
        await engine.dispose()
