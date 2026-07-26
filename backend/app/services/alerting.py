from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.alerting import (
    Alert,
    AlertEvent,
    MaintenanceWindow,
    NotificationChannel,
    NotificationDelivery,
    NotificationRule,
)
from app.models.auth import Organization, OrganizationMember, User, UserRole
from app.schemas.alerting import (
    AlertActionRequest,
    AlertEventResponse,
    AlertListResponse,
    AlertResponse,
    MaintenanceWindowRequest,
    NotificationChannelRequest,
    NotificationChannelResponse,
    NotificationRuleRequest,
)
from app.schemas.observability import OperationalAlert
from app.security.access import Principal, require_service_role
from app.security.notification_config import (
    EncryptedNotificationConfig,
    NotificationConfigCipher,
)
from app.security.webhooks import WebhookEndpointPolicy
from app.services.audit import add_audit_event
from app.services.customer_notifications import queue_customer_notification


class AlertingService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: NotificationConfigCipher,
        principal: Principal | None = None,
        request_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._principal = principal
        self._request_id = request_id
        self._transport = transport

    async def sync(self, current: list[OperationalAlert]) -> int:
        now = datetime.now(UTC)
        seen: set[str] = set()
        changed = 0
        for item in current:
            fingerprint = self._fingerprint(item)
            seen.add(fingerprint)
            alert = await self._session.scalar(
                select(Alert).where(Alert.fingerprint == fingerprint).with_for_update()
            )
            suppressed = await self._suppressed(item, now)
            event_type = "OPEN"
            if alert is None:
                alert = Alert(
                    type=item.code,
                    severity=item.severity.upper(),
                    fingerprint=fingerprint,
                    status="SILENCED" if suppressed else "OPEN",
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    organization_id=item.organization_id,
                    workload_id=item.workload_id,
                    message=item.message,
                    details={"value": item.value, "threshold": item.threshold},
                    first_seen_at=now,
                    last_seen_at=now,
                )
                self._session.add(alert)
                await self._session.flush()
            else:
                event_type = "REOPEN" if alert.status == "RESOLVED" else "REPEAT"
                still_manually_silenced = (
                    alert.status == "SILENCED"
                    and alert.silenced_until is not None
                    and alert.silenced_until > now
                )
                alert.status = (
                    "SILENCED"
                    if suppressed or still_manually_silenced
                    else "OPEN"
                    if alert.status in {"RESOLVED", "SILENCED"}
                    else alert.status
                )
                alert.severity = item.severity.upper()
                alert.message = item.message
                alert.last_seen_at = now
                alert.resolved_at = None
                alert.occurrence_count += 1
                alert.version += 1
            event = AlertEvent(
                alert_id=alert.id,
                event_type="SUPPRESSED" if suppressed else event_type,
                details={"fingerprint": fingerprint},
            )
            self._session.add(event)
            await self._session.flush()
            if not suppressed:
                await self._queue_deliveries(alert, event, now)
                if (
                    event_type in {"OPEN", "REOPEN"}
                    and alert.organization_id is not None
                    and item.code in {"VM_DOWN", "WORKLOAD_DOWN", "WORKLOAD_UNAVAILABLE"}
                ):
                    await queue_customer_notification(
                        self._session,
                        organization_id=alert.organization_id,
                        event_type="VM_DOWN",
                        event_key=f"vm-down:{alert.id}:{event_type}:{alert.version}",
                        subject="가상 머신 상태 확인 필요",
                        message="할당된 가상 머신의 장기 비가용 상태가 감지되었습니다.",
                    )
            changed += 1

        open_alerts = (
            await self._session.scalars(
                select(Alert).where(Alert.status != "RESOLVED").with_for_update()
            )
        ).all()
        for alert in open_alerts:
            if alert.fingerprint in seen:
                continue
            alert.status = "RESOLVED"
            alert.resolved_at = now
            alert.last_seen_at = now
            alert.version += 1
            self._session.add(AlertEvent(alert_id=alert.id, event_type="AUTO_RESOLVE"))
            changed += 1
        await self._session.commit()
        return changed

    async def list_alerts(self, *, status: str | None, severity: str | None) -> AlertListResponse:
        principal = self._require()
        filters = []
        if status:
            filters.append(Alert.status == status.upper())
        if severity:
            filters.append(Alert.severity == severity.upper())
        if principal.role == UserRole.CUSTOMER:
            organization_ids = (
                select(OrganizationMember.organization_id)
                .join(Organization, Organization.id == OrganizationMember.organization_id)
                .where(
                    OrganizationMember.user_id == principal.user_id,
                    Organization.is_active.is_(True),
                )
            )
            filters.append(Alert.organization_id.in_(organization_ids))
        rows = (
            await self._session.scalars(
                select(Alert).where(*filters).order_by(Alert.last_seen_at.desc()).limit(500)
            )
        ).all()
        return AlertListResponse(
            items=[await self._response(item) for item in rows],
            total=len(rows),
        )

    async def get_alert(self, alert_id: UUID) -> AlertResponse:
        alert = await self._owned_alert(alert_id)
        return await self._response(alert)

    async def action(
        self, alert_id: UUID, action: str, payload: AlertActionRequest
    ) -> AlertResponse:
        principal = self._require_admin()
        alert = await self._owned_alert(alert_id, lock=True)
        if alert.version != payload.version:
            raise AppError(409, "ALERT_VERSION_CONFLICT", "The alert changed; reload it.")
        now = datetime.now(UTC)
        normalized = action.upper()
        if normalized == "ACKNOWLEDGE":
            alert.status = "ACKNOWLEDGED"
            alert.acknowledged_at = now
        elif normalized == "ASSIGN":
            if payload.assigned_to_id is None:
                raise AppError(422, "ASSIGNEE_REQUIRED", "An assignee is required.")
            assignee = await self._session.get(User, payload.assigned_to_id)
            if assignee is None or not assignee.is_active or assignee.role == UserRole.CUSTOMER:
                raise AppError(422, "INVALID_ASSIGNEE", "Select an active administrator.")
            alert.assigned_to_id = assignee.id
        elif normalized == "SILENCE":
            if payload.silenced_until is None or payload.silenced_until <= now:
                raise AppError(422, "INVALID_SILENCE", "A future silence end is required.")
            alert.status = "SILENCED"
            alert.silenced_until = payload.silenced_until
        elif normalized == "RESOLVE":
            alert.status = "RESOLVED"
            alert.resolved_at = now
        else:
            raise AppError(404, "ALERT_ACTION_NOT_FOUND", "The alert action was not found.")
        alert.version += 1
        self._session.add(
            AlertEvent(
                alert_id=alert.id,
                event_type=normalized,
                actor_user_id=principal.user_id,
                note=payload.note,
                details={
                    "assigned_to_id": str(payload.assigned_to_id)
                    if payload.assigned_to_id
                    else None,
                    "silenced_until": payload.silenced_until.isoformat()
                    if payload.silenced_until
                    else None,
                },
            )
        )
        add_audit_event(
            self._session,
            action=f"ALERT_{normalized}",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            target_type="alert",
            target_id=alert.id,
        )
        await self._session.commit()
        return await self._response(alert)

    async def maintenance_windows(self) -> list[MaintenanceWindow]:
        self._require_admin()
        return list(
            await self._session.scalars(
                select(MaintenanceWindow).order_by(MaintenanceWindow.starts_at.desc())
            )
        )

    async def create_maintenance(self, payload: MaintenanceWindowRequest) -> MaintenanceWindow:
        principal = self._require_admin()
        item = MaintenanceWindow(
            **payload.model_dump(),
            created_by_id=principal.user_id,
        )
        self._session.add(item)
        await self._session.flush()
        await self._queue_maintenance_notifications(item)
        await self._session.commit()
        return item

    async def delete_maintenance(self, item_id: UUID) -> None:
        self._require_admin()
        item = await self._session.get(MaintenanceWindow, item_id)
        if item is None:
            raise AppError(404, "MAINTENANCE_WINDOW_NOT_FOUND", "The window was not found.")
        await self._session.delete(item)
        await self._session.commit()

    async def update_maintenance(
        self, item_id: UUID, payload: MaintenanceWindowRequest
    ) -> MaintenanceWindow:
        self._require_admin()
        item = await self._session.get(MaintenanceWindow, item_id)
        if item is None:
            raise AppError(404, "MAINTENANCE_WINDOW_NOT_FOUND", "The window was not found.")
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
        await self._queue_maintenance_notifications(item)
        await self._session.commit()
        return item

    async def _queue_maintenance_notifications(self, item: MaintenanceWindow) -> None:
        organization_ids: list[UUID] = []
        if item.organization_id is not None:
            organization_ids = [item.organization_id]
        elif item.target_type.lower() == "organization" and item.target_id:
            try:
                organization_ids = [UUID(item.target_id)]
            except ValueError:
                return
        elif item.target_type.upper() == "ALL":
            organization_ids = list(
                await self._session.scalars(
                    select(Organization.id).where(Organization.is_active.is_(True))
                )
            )
        for organization_id in organization_ids:
            await queue_customer_notification(
                self._session,
                organization_id=organization_id,
                event_type="MAINTENANCE",
                event_key=(
                    f"maintenance:{item.id}:{item.starts_at.isoformat()}:{item.ends_at.isoformat()}"
                ),
                subject="예정된 서비스 유지보수",
                message=(
                    f"{item.starts_at.isoformat()}부터 {item.ends_at.isoformat()}까지 "
                    "유지보수가 예정되어 있습니다."
                ),
            )

    async def channels(self) -> list[NotificationChannelResponse]:
        self._require_admin()
        rows = await self._session.scalars(
            select(NotificationChannel).order_by(NotificationChannel.created_at.desc())
        )
        return [NotificationChannelResponse.model_validate(item) for item in rows]

    async def create_channel(
        self, payload: NotificationChannelRequest
    ) -> NotificationChannelResponse:
        principal = self._require_admin()
        channel_id = uuid4()
        config: dict[str, object] = {}
        if payload.type == "WEBHOOK":
            url = str(payload.webhook_url)
            await self._webhook_policy().validate(url)
            config = {"url": url, "secret": payload.secret or ""}
        else:
            config = {"email": payload.email or ""}
        encrypted = self._cipher.encrypt(config, channel_id=channel_id)
        channel = NotificationChannel(
            id=channel_id,
            organization_id=payload.organization_id,
            name=payload.name,
            type=payload.type,
            is_enabled=payload.is_enabled,
            config_ciphertext=encrypted.ciphertext,
            config_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            created_by_id=principal.user_id,
        )
        self._session.add(channel)
        await self._session.commit()
        return NotificationChannelResponse.model_validate(channel)

    async def update_channel(
        self, channel_id: UUID, payload: NotificationChannelRequest
    ) -> NotificationChannelResponse:
        self._require_admin()
        channel = await self._session.get(NotificationChannel, channel_id, with_for_update=True)
        if channel is None:
            raise AppError(404, "NOTIFICATION_CHANNEL_NOT_FOUND", "The channel was not found.")
        config: dict[str, object]
        if payload.type == "WEBHOOK":
            url = str(payload.webhook_url)
            await self._webhook_policy().validate(url)
            config = {"url": url, "secret": payload.secret or ""}
        else:
            config = {"email": payload.email or ""}
        encrypted = self._cipher.encrypt(config, channel_id=channel.id)
        channel.organization_id = payload.organization_id
        channel.name = payload.name
        channel.type = payload.type
        channel.is_enabled = payload.is_enabled
        channel.config_ciphertext = encrypted.ciphertext
        channel.config_nonce = encrypted.nonce
        channel.key_version = encrypted.key_version
        await self._session.commit()
        return NotificationChannelResponse.model_validate(channel)

    async def delete_channel(self, channel_id: UUID) -> None:
        self._require_admin()
        channel = await self._session.get(NotificationChannel, channel_id)
        if channel is None:
            raise AppError(404, "NOTIFICATION_CHANNEL_NOT_FOUND", "The channel was not found.")
        await self._session.delete(channel)
        await self._session.commit()

    async def test_channel(self, channel_id: UUID) -> NotificationDelivery:
        self._require_admin()
        channel = await self._session.get(NotificationChannel, channel_id)
        if channel is None:
            raise AppError(404, "NOTIFICATION_CHANNEL_NOT_FOUND", "The channel was not found.")
        alert = Alert(
            type="CHANNEL_TEST",
            severity="INFO",
            fingerprint=f"test:{uuid4()}",
            status="RESOLVED",
            resource_type="notification",
            message="PVE Master notification channel test",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            resolved_at=datetime.now(UTC),
        )
        self._session.add(alert)
        await self._session.flush()
        event = AlertEvent(alert_id=alert.id, event_type="TEST")
        self._session.add(event)
        await self._session.flush()
        delivery = NotificationDelivery(
            alert_event_id=event.id,
            channel_id=channel.id,
            status="PENDING",
            next_attempt_at=datetime.now(UTC),
        )
        self._session.add(delivery)
        await self._session.commit()
        await self.deliver_due(delivery_ids=[delivery.id])
        return delivery

    async def rules(self) -> list[NotificationRule]:
        self._require_admin()
        return list(await self._session.scalars(select(NotificationRule)))

    async def create_rule(self, payload: NotificationRuleRequest) -> NotificationRule:
        self._require_admin()
        channel = await self._session.get(NotificationChannel, payload.channel_id)
        if channel is None or channel.organization_id != payload.organization_id:
            raise AppError(422, "INVALID_NOTIFICATION_CHANNEL", "The channel scope is invalid.")
        item = NotificationRule(**payload.model_dump())
        self._session.add(item)
        await self._session.commit()
        return item

    async def update_rule(
        self, rule_id: UUID, payload: NotificationRuleRequest
    ) -> NotificationRule:
        self._require_admin()
        item = await self._session.get(NotificationRule, rule_id)
        if item is None:
            raise AppError(404, "NOTIFICATION_RULE_NOT_FOUND", "The rule was not found.")
        channel = await self._session.get(NotificationChannel, payload.channel_id)
        if channel is None or channel.organization_id != payload.organization_id:
            raise AppError(422, "INVALID_NOTIFICATION_CHANNEL", "The channel scope is invalid.")
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
        await self._session.commit()
        return item

    async def delete_rule(self, rule_id: UUID) -> None:
        self._require_admin()
        item = await self._session.get(NotificationRule, rule_id)
        if item is None:
            raise AppError(404, "NOTIFICATION_RULE_NOT_FOUND", "The rule was not found.")
        await self._session.delete(item)
        await self._session.commit()

    async def deliver_due(self, delivery_ids: list[UUID] | None = None) -> int:
        now = datetime.now(UTC)
        filters = [
            NotificationDelivery.status.in_(["PENDING", "RETRY"]),
            NotificationDelivery.next_attempt_at <= now,
        ]
        if delivery_ids:
            filters.append(NotificationDelivery.id.in_(delivery_ids))
        deliveries = (
            await self._session.scalars(
                select(NotificationDelivery).where(*filters).with_for_update(skip_locked=True)
            )
        ).all()
        delivered = 0
        for delivery in deliveries:
            event = await self._session.get(AlertEvent, delivery.alert_event_id)
            alert = await self._session.get(Alert, event.alert_id) if event else None
            channel = await self._session.get(NotificationChannel, delivery.channel_id)
            if event is None or alert is None or channel is None or not channel.is_enabled:
                delivery.status = "FAILED"
                delivery.last_error_code = "DELIVERY_TARGET_MISSING"
                continue
            delivery.attempt_count += 1
            try:
                await self._send(channel, alert, event, delivery.id)
            except (AppError, httpx.HTTPError, TimeoutError) as exc:
                delivery.last_error_code = self._safe_error(exc)
                if delivery.attempt_count >= self._settings.notification_max_attempts:
                    delivery.status = "FAILED"
                else:
                    delivery.status = "RETRY"
                    delivery.next_attempt_at = now + timedelta(
                        seconds=min(3600, 2**delivery.attempt_count * 15)
                    )
            else:
                delivery.status = "DELIVERED"
                delivery.delivered_at = now
                delivery.last_error_code = None
                delivered += 1
        await self._session.commit()
        return delivered

    async def _send(
        self,
        channel: NotificationChannel,
        alert: Alert,
        event: AlertEvent,
        delivery_id: UUID,
    ) -> None:
        config = self._cipher.decrypt(
            EncryptedNotificationConfig(
                channel.config_ciphertext,
                channel.config_nonce,
                channel.key_version,
            ),
            channel_id=channel.id,
        )
        payload = {
            "delivery_id": str(delivery_id),
            "event": event.event_type,
            "alert": {
                "id": str(alert.id),
                "type": alert.type,
                "severity": alert.severity,
                "message": alert.message,
                "organization_id": str(alert.organization_id) if alert.organization_id else None,
                "workload_id": str(alert.workload_id) if alert.workload_id else None,
            },
        }
        if channel.type == "WEBHOOK":
            url = str(config.get("url", ""))
            await self._webhook_policy().validate(url)
            body = json.dumps(payload, separators=(",", ":")).encode()
            secret = str(config.get("secret", ""))
            headers = {
                "Content-Type": "application/json",
                "Idempotency-Key": str(delivery_id),
            }
            if secret:
                headers["X-PVE-Master-Signature"] = hmac.new(
                    secret.encode(), body, hashlib.sha256
                ).hexdigest()
            async with httpx.AsyncClient(
                timeout=self._settings.notification_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.post(url, content=body, headers=headers)
                response.raise_for_status()
            return
        if channel.type == "EMAIL":
            await self._send_email(
                recipient=str(config.get("email", "")),
                subject=f"[{alert.severity}] {alert.type}",
                body=f"{alert.message}\n\nAlert ID: {alert.id}\nEvent: {event.event_type}",
            )
            return
        raise AppError(503, "NOTIFICATION_CHANNEL_UNSUPPORTED", "The channel is unsupported.")

    async def _send_email(self, *, recipient: str, subject: str, body: str) -> None:
        settings = self._settings
        if not settings.smtp_host or not settings.smtp_from_address or "@" not in recipient:
            raise AppError(503, "EMAIL_DELIVERY_UNAVAILABLE", "Email delivery is not configured.")
        smtp_host = settings.smtp_host
        message = EmailMessage()
        message["From"] = settings.smtp_from_address
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        def send() -> None:
            if settings.smtp_use_tls:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    smtp_host,
                    settings.smtp_port,
                    timeout=settings.notification_timeout_seconds,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(
                    smtp_host,
                    settings.smtp_port,
                    timeout=settings.notification_timeout_seconds,
                )
                client.starttls(context=ssl.create_default_context())
            try:
                if settings.smtp_username and settings.smtp_password:
                    client.login(
                        settings.smtp_username,
                        settings.smtp_password.get_secret_value(),
                    )
                client.send_message(message)
            finally:
                client.quit()

        try:
            await asyncio.wait_for(
                asyncio.to_thread(send),
                timeout=settings.notification_timeout_seconds + 1,
            )
        except TimeoutError:
            raise
        except (OSError, smtplib.SMTPException) as exc:
            raise AppError(
                503,
                "EMAIL_DELIVERY_FAILED",
                "Email delivery failed.",
            ) from exc

    async def _queue_deliveries(self, alert: Alert, event: AlertEvent, now: datetime) -> None:
        rules = (
            await self._session.scalars(
                select(NotificationRule).where(
                    NotificationRule.is_enabled.is_(True),
                    (NotificationRule.organization_id == alert.organization_id)
                    if alert.organization_id
                    else NotificationRule.organization_id.is_(None),
                )
            )
        ).all()
        for rule in rules:
            if rule.event_types and alert.type not in rule.event_types:
                continue
            if rule.severities and alert.severity not in rule.severities:
                continue
            if event.event_type == "REPEAT":
                if rule.escalation_minutes is None:
                    continue
                if now < alert.first_seen_at + timedelta(minutes=rule.escalation_minutes):
                    continue
            quiet_start = rule.quiet_hours.get("start_hour_utc")
            quiet_end = rule.quiet_hours.get("end_hour_utc")
            if isinstance(quiet_start, int) and isinstance(quiet_end, int):
                in_quiet_hours = (
                    quiet_start <= now.hour < quiet_end
                    if quiet_start < quiet_end
                    else now.hour >= quiet_start or now.hour < quiet_end
                )
                if in_quiet_hours and alert.severity != "CRITICAL":
                    continue
            self._session.add(
                NotificationDelivery(
                    alert_event_id=event.id,
                    channel_id=rule.channel_id,
                    status="PENDING",
                    next_attempt_at=now,
                )
            )

    async def _suppressed(self, item: OperationalAlert, now: datetime) -> bool:
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(MaintenanceWindow)
                .where(
                    MaintenanceWindow.starts_at <= now,
                    MaintenanceWindow.ends_at > now,
                    MaintenanceWindow.suppress_notifications.is_(True),
                    (MaintenanceWindow.target_type == item.resource_type)
                    | (MaintenanceWindow.target_type == "ALL"),
                    (MaintenanceWindow.target_id == item.resource_id)
                    | MaintenanceWindow.target_id.is_(None),
                )
            )
            or 0
        ) > 0

    async def _owned_alert(self, alert_id: UUID, *, lock: bool = False) -> Alert:
        principal = self._require()
        statement = select(Alert).where(Alert.id == alert_id)
        if principal.role == UserRole.CUSTOMER:
            statement = statement.where(
                Alert.organization_id.in_(
                    select(OrganizationMember.organization_id)
                    .join(Organization, Organization.id == OrganizationMember.organization_id)
                    .where(
                        OrganizationMember.user_id == principal.user_id,
                        Organization.is_active.is_(True),
                    )
                )
            )
        if lock:
            statement = statement.with_for_update()
        alert = await self._session.scalar(statement)
        if alert is None:
            raise AppError(404, "ALERT_NOT_FOUND", "The alert was not found.")
        return alert

    async def _response(self, alert: Alert) -> AlertResponse:
        events = (
            await self._session.scalars(
                select(AlertEvent)
                .where(AlertEvent.alert_id == alert.id)
                .order_by(AlertEvent.created_at.desc())
                .limit(50)
            )
        ).all()
        return AlertResponse.model_validate(alert).model_copy(
            update={"events": [AlertEventResponse.model_validate(item) for item in events]}
        )

    def _require(self) -> Principal:
        if self._principal is None:
            raise AppError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
        return self._principal

    def _require_admin(self) -> Principal:
        principal = self._require()
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
        return principal

    def _webhook_policy(self) -> WebhookEndpointPolicy:
        return WebhookEndpointPolicy(
            allowed_hosts=self._settings.notification_webhook_allowed_hosts,
            allowed_networks=self._settings.notification_webhook_allowed_networks,
        )

    @staticmethod
    def _fingerprint(item: OperationalAlert) -> str:
        raw = f"{item.code}:{item.resource_type}:{item.resource_id or '-'}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, AppError):
            return exc.code
        if isinstance(exc, httpx.TimeoutException):
            return "NOTIFICATION_TIMEOUT"
        if isinstance(exc, httpx.HTTPStatusError):
            return f"NOTIFICATION_HTTP_{exc.response.status_code}"
        return "NOTIFICATION_DELIVERY_FAILED"
