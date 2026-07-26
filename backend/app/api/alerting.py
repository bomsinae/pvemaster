from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.alerting import (
    AlertActionRequest,
    AlertListResponse,
    AlertResponse,
    MaintenanceWindowRequest,
    MaintenanceWindowResponse,
    NotificationChannelRequest,
    NotificationChannelResponse,
    NotificationDeliveryResponse,
    NotificationRuleRequest,
    NotificationRuleResponse,
)
from app.security.notification_config import NotificationConfigCipher
from app.services.alerting import AlertingService

router = APIRouter(tags=["alerting"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> AlertingService:
    return AlertingService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        cipher=cast(NotificationConfigCipher, request.app.state.notification_cipher),
        principal=principal,
        request_id=request.state.request_id,
    )


@router.get("/api/v1/admin/alerts", response_model=AlertListResponse)
@router.get("/api/v1/customer/alerts", response_model=AlertListResponse)
async def list_alerts(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    alert_status: str | None = Query(default=None, alias="status", max_length=20),
    severity: str | None = Query(default=None, max_length=16),
) -> AlertListResponse:
    return await _service(request, session, principal).list_alerts(
        status=alert_status,
        severity=severity,
    )


@router.get("/api/v1/admin/alerts/{alert_id}", response_model=AlertResponse)
@router.get("/api/v1/customer/alerts/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AlertResponse:
    return await _service(request, session, principal).get_alert(alert_id)


@router.post("/api/v1/admin/alerts/{alert_id}/{action}", response_model=AlertResponse)
async def act_on_alert(
    alert_id: UUID,
    action: str,
    payload: AlertActionRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AlertResponse:
    return await _service(request, session, principal).action(alert_id, action, payload)


@router.get(
    "/api/v1/admin/maintenance-windows",
    response_model=list[MaintenanceWindowResponse],
)
async def maintenance_windows(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[MaintenanceWindowResponse]:
    items = await _service(request, session, principal).maintenance_windows()
    return [MaintenanceWindowResponse.model_validate(item) for item in items]


@router.post(
    "/api/v1/admin/maintenance-windows",
    response_model=MaintenanceWindowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_maintenance_window(
    payload: MaintenanceWindowRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> MaintenanceWindowResponse:
    return MaintenanceWindowResponse.model_validate(
        await _service(request, session, principal).create_maintenance(payload)
    )


@router.delete(
    "/api/v1/admin/maintenance-windows/{window_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_maintenance_window(
    window_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _service(request, session, principal).delete_maintenance(window_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/api/v1/admin/maintenance-windows/{window_id}",
    response_model=MaintenanceWindowResponse,
)
async def update_maintenance_window(
    window_id: UUID,
    payload: MaintenanceWindowRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> MaintenanceWindowResponse:
    return MaintenanceWindowResponse.model_validate(
        await _service(request, session, principal).update_maintenance(window_id, payload)
    )


@router.get(
    "/api/v1/admin/notification-channels",
    response_model=list[NotificationChannelResponse],
)
async def notification_channels(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[NotificationChannelResponse]:
    return await _service(request, session, principal).channels()


@router.post(
    "/api/v1/admin/notification-channels",
    response_model=NotificationChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_channel(
    payload: NotificationChannelRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> NotificationChannelResponse:
    return await _service(request, session, principal).create_channel(payload)


@router.put(
    "/api/v1/admin/notification-channels/{channel_id}",
    response_model=NotificationChannelResponse,
)
async def update_notification_channel(
    channel_id: UUID,
    payload: NotificationChannelRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> NotificationChannelResponse:
    return await _service(request, session, principal).update_channel(channel_id, payload)


@router.delete(
    "/api/v1/admin/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_channel(
    channel_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _service(request, session, principal).delete_channel(channel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v1/admin/notification-channels/{channel_id}/test",
    response_model=NotificationDeliveryResponse,
)
async def test_notification_channel(
    channel_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> NotificationDeliveryResponse:
    return NotificationDeliveryResponse.model_validate(
        await _service(request, session, principal).test_channel(channel_id)
    )


@router.get(
    "/api/v1/admin/notification-rules",
    response_model=list[NotificationRuleResponse],
)
async def notification_rules(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[NotificationRuleResponse]:
    return [
        NotificationRuleResponse.model_validate(item)
        for item in await _service(request, session, principal).rules()
    ]


@router.post(
    "/api/v1/admin/notification-rules",
    response_model=NotificationRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_rule(
    payload: NotificationRuleRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> NotificationRuleResponse:
    return NotificationRuleResponse.model_validate(
        await _service(request, session, principal).create_rule(payload)
    )


@router.put(
    "/api/v1/admin/notification-rules/{rule_id}",
    response_model=NotificationRuleResponse,
)
async def update_notification_rule(
    rule_id: UUID,
    payload: NotificationRuleRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> NotificationRuleResponse:
    return NotificationRuleResponse.model_validate(
        await _service(request, session, principal).update_rule(rule_id, payload)
    )


@router.delete(
    "/api/v1/admin/notification-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_rule(
    rule_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _service(request, session, principal).delete_rule(rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
