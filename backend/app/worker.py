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
    include=[
        "app.tasks.backup",
        "app.tasks.inventory",
        "app.tasks.power",
        "app.tasks.provisioning",
        "app.tasks.maintenance",
        "app.tasks.scheduler",
    ],
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
    result_expires=86400,
    task_routes={
        "app.tasks.backup.*": {"queue": "operations"},
        "app.tasks.power.*": {"queue": "operations"},
        "app.tasks.provisioning.*": {"queue": "operations"},
        "app.tasks.inventory.*": {"queue": "inventory"},
        "app.tasks.maintenance.*": {"queue": "maintenance"},
        "app.tasks.scheduler.*": {"queue": "maintenance"},
    },
    beat_schedule={
        "dispatch-operation-outbox": {
            "task": "app.tasks.scheduler.dispatch_operation_outbox",
            "schedule": 5.0,
        },
        "watchdog-operations": {
            "task": "app.tasks.scheduler.watchdog_operations",
            "schedule": 30.0,
        },
        "watchdog-provisioning": {
            "task": "app.tasks.scheduler.watchdog_provisioning",
            "schedule": 30.0,
        },
        "dispatch-inventory-sync": {
            "task": "app.tasks.scheduler.dispatch_inventory_sync",
            "schedule": 60.0,
        },
        "release-ip-quarantine": {
            "task": "app.tasks.scheduler.release_ip_quarantine",
            "schedule": 60.0,
        },
        "check-control-plane-state": {
            "task": "app.tasks.scheduler.check_control_plane_state",
            "schedule": 300.0,
        },
        "reconcile-backup-metadata": {
            "task": "app.tasks.scheduler.reconcile_backup_metadata",
            "schedule": 300.0,
        },
        "dispatch-backup-policies": {
            "task": "app.tasks.scheduler.dispatch_backup_policies",
            "schedule": 60.0,
        },
        "reconcile-backup-verifications": {
            "task": "app.tasks.scheduler.reconcile_backup_verifications",
            "schedule": 300.0,
        },
        "run-data-retention": {
            "task": "app.tasks.scheduler.run_retention",
            "schedule": 86400.0,
        },
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
def schedule_recovery_watchdogs(**_: object) -> None:
    try:
        _record_worker_heartbeat(str(_.get("sender", "worker")))
    except Exception:
        pass

    celery_app.send_task(
        "app.tasks.scheduler.dispatch_operation_outbox",
        queue="maintenance",
    )
    celery_app.send_task(
        "app.tasks.scheduler.watchdog_operations",
        queue="maintenance",
    )
    celery_app.send_task(
        "app.tasks.scheduler.watchdog_provisioning",
        queue="maintenance",
    )
