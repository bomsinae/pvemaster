from celery import Celery
from celery.signals import heartbeat_sent, worker_ready
from redis import Redis

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery(
    "pvemaster",
    broker=settings.redis_url.get_secret_value(),
    backend=settings.redis_url.get_secret_value(),
    include=["app.tasks.power", "app.tasks.provisioning", "app.tasks.maintenance"],
)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.tasks.power.*": {"queue": "operations"},
        "app.tasks.provisioning.*": {"queue": "operations"},
        "app.tasks.maintenance.*": {"queue": "maintenance"},
    },
)


def _record_worker_heartbeat(hostname: str) -> None:
    client = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    try:
        client.setex(
            f"pvemaster:worker:heartbeat:{hostname}",
            settings.worker_heartbeat_ttl_seconds,
            "alive",
        )
    finally:
        client.close()


@heartbeat_sent.connect  # type: ignore[untyped-decorator]
def record_worker_heartbeat(sender: object, **_: object) -> None:
    hostname = str(getattr(sender, "hostname", "unknown"))
    try:
        _record_worker_heartbeat(hostname)
    except Exception:
        return


@worker_ready.connect  # type: ignore[untyped-decorator]
def recover_incomplete_power_operations(**_: object) -> None:
    from asyncio import run

    from app.tasks.power import recover_power_operations
    from app.tasks.provisioning import recover_provisioning_requests

    try:
        _record_worker_heartbeat(str(_.get("sender", "worker")))
    except Exception:
        pass

    run(recover_power_operations())
    run(recover_provisioning_requests())
