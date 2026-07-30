from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.db import create_engine, create_session_factory
from app.models.operation import Operation, OperationStatus
from app.worker import celery_app


def enqueue_power_operation(operation_id: UUID, task_id: str) -> None:
    celery_app.send_task(
        "app.tasks.power.execute_power_operation",
        args=[str(operation_id)],
        task_id=task_id,
        queue="operations",
    )


@celery_app.task(name="app.tasks.power.execute_power_operation")  # type: ignore[untyped-decorator]
def execute_power_operation(operation_id: str) -> None:
    from asyncio import run

    from app.services.power_runner import run_power_operation
    from app.tasks.inventory import request_operation_target_sync

    async def execute() -> None:
        parsed_id = UUID(operation_id)
        await run_power_operation(parsed_id)
        await request_operation_target_sync(parsed_id)

    run(execute())


async def recover_power_operations() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    recovered = 0
    try:
        async with session_factory() as session:
            operations = await session.scalars(
                select(Operation).where(
                    Operation.operation_type != "WORKLOAD_BACKUP",
                    ~Operation.operation_type.startswith("ADVANCED_"),
                    Operation.status.in_(
                        [OperationStatus.QUEUED.value, OperationStatus.RUNNING.value]
                    ),
                )
            )
            for operation in operations.all():
                enqueue_power_operation(operation.id, operation.celery_task_id)
                recovered += 1
    finally:
        await engine.dispose()
    return recovered
