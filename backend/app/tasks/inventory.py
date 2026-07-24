import asyncio
from typing import Protocol
from uuid import UUID

from app.core.config import get_settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.models.operation import Operation
from app.models.provisioning import ProvisioningRequest
from app.security.credentials import CredentialCipher
from app.services.inventory_sync import ScheduledInventorySyncRunner
from app.services.reconciliation import create_sync_run
from app.worker import celery_app

RETRYABLE_SYNC_ERRORS = {
    "CLUSTER_UNREACHABLE",
    "PVE_RATE_LIMITED",
    "PVE_TIMEOUT",
    "PVE_UPSTREAM_ERROR",
}


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
def sync_cluster_inventory(task: RetryTask, run_id: str) -> None:
    try:
        asyncio.run(_sync_cluster_inventory(UUID(run_id)))
    except AppError as exc:
        if exc.code not in RETRYABLE_SYNC_ERRORS:
            return
        raise task.retry(countdown=min(2**task.request.retries, 30)) from exc


def enqueue_inventory_sync(run_id: UUID) -> None:
    celery_app.send_task(
        "app.tasks.inventory.sync_cluster_inventory",
        args=[str(run_id)],
        queue="inventory",
    )


async def _sync_cluster_inventory(run_id: UUID) -> None:
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
            run = await runner.run(run_id)
            if (
                run is not None
                and run.error_code in RETRYABLE_SYNC_ERRORS
                and run.status in {"FAILED", "PARTIAL"}
            ):
                raise AppError(
                    503,
                    run.error_code,
                    "The inventory sync will be retried.",
                )
    finally:
        await engine.dispose()


async def request_operation_target_sync(operation_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            operation = await session.get(Operation, operation_id)
            if operation is None:
                return
            target_workload_id = (
                None if operation.operation_type == "WORKLOAD_RESTORE" else operation.workload_id
            )
            run, _created = await create_sync_run(
                session,
                cluster_id=operation.cluster_id,
                target_workload_id=target_workload_id,
                triggered_by="operation",
            )
            if run.status == "QUEUED":
                enqueue_inventory_sync(run.id)
    finally:
        await engine.dispose()


async def request_provisioning_target_sync(request_id: UUID) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            request = await session.get(ProvisioningRequest, request_id)
            if request is None or request.workload_id is None:
                return
            run, _created = await create_sync_run(
                session,
                cluster_id=request.target_cluster_id,
                target_workload_id=request.workload_id,
                triggered_by="operation",
            )
            if run.status == "QUEUED":
                enqueue_inventory_sync(run.id)
    finally:
        await engine.dispose()
