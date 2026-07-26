from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.accounts import router as accounts_router
from app.api.advanced_operations import router as advanced_operations_router
from app.api.alerting import router as alerting_router
from app.api.auth import router as auth_router
from app.api.backups import router as backups_router
from app.api.clusters import router as clusters_router
from app.api.console import router as console_router
from app.api.customer import router as customer_router
from app.api.health import router as health_router
from app.api.inventory import router as inventory_router
from app.api.ipam import router as ipam_router
from app.api.observability import router as observability_router
from app.api.operation_center import router as operation_center_router
from app.api.operations import router as operations_router
from app.api.organization_governance import admin_router as admin_governance_router
from app.api.organization_governance import customer_router as customer_governance_router
from app.api.provisioning import router as provisioning_router
from app.api.self_service import admin_router as admin_self_service_router
from app.api.self_service import customer_router as customer_self_service_router
from app.api.workloads import router as workloads_router
from app.core.config import Settings, get_settings
from app.core.errors import AppError, app_error_handler, request_validation_error_handler
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.db import create_engine, create_session_factory
from app.health import check_readiness
from app.security.credentials import CredentialCipher
from app.security.mfa import MfaSecretCipher
from app.security.notification_config import NotificationConfigCipher
from app.security.passwords import PasswordManager
from app.security.tokens import TokenManager


def enqueue_power_operation(operation_id: UUID, task_id: str) -> None:
    from app.tasks.power import enqueue_power_operation as publish

    publish(operation_id, task_id)


def enqueue_backup_operation(operation_id: UUID, task_id: str) -> None:
    from app.tasks.backup import enqueue_backup_operation as publish

    publish(operation_id, task_id)


def enqueue_restore_operation(operation_id: UUID, task_id: str) -> None:
    from app.tasks.backup import enqueue_restore_operation as publish

    publish(operation_id, task_id)


def enqueue_provisioning_request(request_id: UUID, task_id: str) -> None:
    from app.tasks.provisioning import enqueue_provisioning_request as publish

    publish(request_id, task_id)


def enqueue_inventory_sync(run_id: UUID) -> None:
    from app.tasks.inventory import enqueue_inventory_sync as publish

    publish(run_id)


def enqueue_advanced_operation(operation_id: UUID, task_id: str) -> None:
    from app.tasks.advanced_operations import enqueue_advanced_operation as publish

    publish(operation_id, task_id)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()

    app = FastAPI(title=app_settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.db_engine = create_engine(app_settings)
    app.state.db_session_factory = create_session_factory(app.state.db_engine)
    app.state.redis = Redis.from_url(
        app_settings.redis_url.get_secret_value(),
        decode_responses=True,
    )
    app.state.readiness_check = check_readiness
    app.state.credential_cipher = CredentialCipher(app_settings.app_secret_key.get_secret_value())
    app.state.mfa_cipher = MfaSecretCipher(app_settings.app_secret_key.get_secret_value())
    app.state.notification_cipher = NotificationConfigCipher(
        app_settings.app_secret_key.get_secret_value()
    )
    app.state.password_manager = PasswordManager()
    app.state.token_manager = TokenManager(app_settings)
    app.state.proxmox_transport = None
    app.state.operation_publisher = enqueue_power_operation
    app.state.backup_publisher = enqueue_backup_operation
    app.state.restore_publisher = enqueue_restore_operation
    app.state.provisioning_publisher = enqueue_provisioning_request
    app.state.inventory_publisher = enqueue_inventory_sync
    app.state.advanced_operation_publisher = enqueue_advanced_operation

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).rstrip("/") for origin in app_settings.cors_origins],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-CSRF-Token",
            "X-Request-ID",
            "X-Step-Up-Token",
        ],
    )
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,  # type: ignore[arg-type]
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(alerting_router)
    app.include_router(advanced_operations_router)
    app.include_router(backups_router)
    app.include_router(accounts_router)
    app.include_router(clusters_router)
    app.include_router(console_router)
    app.include_router(customer_router)
    app.include_router(customer_governance_router)
    app.include_router(customer_self_service_router)
    app.include_router(operations_router)
    app.include_router(observability_router)
    app.include_router(operation_center_router)
    app.include_router(ipam_router)
    app.include_router(inventory_router)
    app.include_router(provisioning_router)
    app.include_router(admin_self_service_router)
    app.include_router(admin_governance_router)
    app.include_router(workloads_router)
    return app
