import asyncio
from typing import Protocol
from uuid import UUID

from app.core.config import get_settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.security.credentials import CredentialCipher
from app.services.inventory_sync import ScheduledInventorySyncRunner
from app.worker import celery_app

RETRYABLE_SYNC_ERRORS = {"CLUSTER_UNREACHABLE", "PVE_TIMEOUT", "PVE_UPSTREAM_ERROR"}


class TaskRequest(Protocol):
    retries: int


class RetryTask(Protocol):
    request: TaskRequest

    def retry(self, *, countdown: int) -> Exception: ...


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="app.tasks.inventory.sync_cluster_inventory",
    max_retries=3,
)
def sync_cluster_inventory(task: RetryTask, cluster_id: str) -> None:
    try:
        asyncio.run(_sync_cluster_inventory(UUID(cluster_id)))
    except AppError as exc:
        if exc.code not in RETRYABLE_SYNC_ERRORS:
            return
        raise task.retry(countdown=min(2**task.request.retries, 30)) from exc


async def _sync_cluster_inventory(cluster_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            runner = ScheduledInventorySyncRunner(
                session=session,
                settings=settings,
                cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
            )
            await runner.run(cluster_id)
    finally:
        await engine.dispose()
