from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.db import create_engine, create_session_factory
from app.models.operation import Operation, OperationStatus
from app.worker import celery_app


def enqueue_backup_operation(operation_id: UUID, task_id: str) -> None:
    celery_app.send_task(
        "app.tasks.backup.execute_backup_operation",
        args=[str(operation_id)],
        task_id=task_id,
        queue="operations",
    )


def enqueue_restore_operation(operation_id: UUID, task_id: str) -> None:
    celery_app.send_task(
        "app.tasks.backup.execute_restore_operation",
        args=[str(operation_id)],
        task_id=task_id,
        queue="operations",
    )


@celery_app.task(name="app.tasks.backup.execute_backup_operation")  # type: ignore[untyped-decorator]
def execute_backup_operation(operation_id: str) -> None:
    from asyncio import run

    from app.services.backup_runner import run_backup_operation

    run(run_backup_operation(UUID(operation_id)))


@celery_app.task(name="app.tasks.backup.execute_restore_operation")  # type: ignore[untyped-decorator]
def execute_restore_operation(operation_id: str) -> None:
    from asyncio import run

    from app.services.restore_runner import run_restore_operation

    run(run_restore_operation(UUID(operation_id)))


async def recover_backup_operations() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    recovered = 0
    try:
        async with session_factory() as session:
            operations = await session.scalars(
                select(Operation).where(
                    Operation.operation_type == "WORKLOAD_BACKUP",
                    Operation.status.in_(
                        [OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]
                    ),
                )
            )
            for operation in operations.all():
                enqueue_backup_operation(operation.id, operation.celery_task_id)
                recovered += 1
            restores = await session.scalars(
                select(Operation).where(
                    Operation.operation_type == "WORKLOAD_RESTORE",
                    Operation.status.in_(
                        [OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]
                    ),
                )
            )
            for operation in restores.all():
                enqueue_restore_operation(operation.id, operation.celery_task_id)
                recovered += 1
    finally:
        await engine.dispose()
    return recovered
