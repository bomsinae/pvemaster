from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.core.config import get_settings
from app.db import create_engine, create_session_factory
from app.models.provisioning import ProvisioningRequest, ProvisioningStatus
from app.worker import celery_app


def enqueue_provisioning_request(request_id: UUID, task_id: str) -> None:
    celery_app.send_task(
        "app.tasks.provisioning.execute_provisioning_request",
        args=[str(request_id)],
        task_id=task_id,
        queue="operations",
    )


@celery_app.task(name="app.tasks.provisioning.execute_provisioning_request")  # type: ignore[untyped-decorator]
def execute_provisioning_request(request_id: str) -> None:
    from asyncio import run

    from app.services.provisioning_runner import run_provisioning_request

    run(run_provisioning_request(UUID(request_id)))


async def recover_provisioning_requests() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    recovered = 0
    try:
        async with session_factory() as session:
            requests = await session.scalars(
                select(ProvisioningRequest).where(
                    or_(
                        ProvisioningRequest.status == ProvisioningStatus.QUEUED.value,
                        and_(
                            ProvisioningRequest.status == ProvisioningStatus.RUNNING.value,
                            or_(
                                ProvisioningRequest.runner_id.is_(None),
                                ProvisioningRequest.lease_expires_at.is_(None),
                                ProvisioningRequest.lease_expires_at <= datetime.now(UTC),
                            ),
                        ),
                    )
                )
            )
            for request in requests:
                enqueue_provisioning_request(request.id, request.celery_task_id)
                recovered += 1
    finally:
        await engine.dispose()
    return recovered
