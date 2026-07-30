from uuid import UUID

from app.worker import celery_app


def enqueue_advanced_operation(operation_id: UUID, task_id: str) -> None:
    celery_app.send_task(
        "app.tasks.advanced_operations.execute",
        args=[str(operation_id)],
        task_id=task_id,
        queue="operations",
    )


@celery_app.task(name="app.tasks.advanced_operations.execute")  # type: ignore[untyped-decorator]
def execute_advanced_operation(operation_id: str) -> None:
    from asyncio import run

    from app.services.advanced_operation_runner import run_advanced_operation
    from app.tasks.inventory import request_operation_target_sync

    async def execute() -> None:
        parsed_id = UUID(operation_id)
        await run_advanced_operation(parsed_id)
        await request_operation_target_sync(parsed_id)

    run(execute())
