import asyncio
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import Organization, OrganizationMember, User, UserRole
from app.models.customer_notifications import (
    CustomerNotificationDelivery,
    CustomerNotificationPreference,
    OrganizationNotificationPolicy,
)
from app.schemas.customer import (
    CustomerNotificationEvent,
    CustomerNotificationPreferenceListResponse,
    CustomerNotificationPreferenceResponse,
    CustomerNotificationPreferenceUpdate,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event
from app.services.organization_access import active_membership_conditions

EVENT_TYPES: tuple[CustomerNotificationEvent, ...] = (
    "VM_DOWN",
    "OPERATION_COMPLETED",
    "BACKUP_FAILED",
    "MAINTENANCE",
)
DEFAULTS: dict[CustomerNotificationEvent, bool] = {
    "VM_DOWN": True,
    "OPERATION_COMPLETED": True,
    "BACKUP_FAILED": True,
    "MAINTENANCE": True,
}
EmailSender = Callable[[str, str, str], Awaitable[None]]


class CustomerNotificationPreferenceService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        request_id: str,
    ) -> None:
        self._session = session
        self._principal = principal
        self._request_id = request_id
        require_service_role(principal, UserRole.CUSTOMER)

    async def list(self) -> CustomerNotificationPreferenceListResponse:
        memberships = (
            await self._session.execute(
                select(Organization.id, Organization.name)
                .join(
                    OrganizationMember,
                    OrganizationMember.organization_id == Organization.id,
                )
                .where(
                    *active_membership_conditions(
                        user_id=self._principal.user_id,
                        organization_id=Organization.id,
                    ),
                    Organization.is_active.is_(True),
                )
                .order_by(Organization.name)
            )
        ).all()
        organization_ids = [row.id for row in memberships]
        preferences = (
            await self._session.scalars(
                select(CustomerNotificationPreference).where(
                    CustomerNotificationPreference.user_id == self._principal.user_id,
                    CustomerNotificationPreference.organization_id.in_(organization_ids),
                )
            )
        ).all()
        policies = (
            await self._session.scalars(
                select(OrganizationNotificationPolicy).where(
                    OrganizationNotificationPolicy.organization_id.in_(organization_ids),
                    OrganizationNotificationPolicy.email_required.is_(True),
                )
            )
        ).all()
        by_key = {(item.organization_id, item.event_type): item for item in preferences}
        required = {(item.organization_id, item.event_type) for item in policies}
        items: list[CustomerNotificationPreferenceResponse] = []
        for organization_id, organization_name in memberships:
            for event_type in EVENT_TYPES:
                preference = by_key.get((organization_id, event_type))
                forced = (organization_id, event_type) in required
                items.append(
                    CustomerNotificationPreferenceResponse(
                        organization_id=organization_id,
                        organization_name=organization_name,
                        event_type=event_type,
                        email_enabled=forced
                        or (
                            preference.email_enabled
                            if preference is not None
                            else DEFAULTS[event_type]
                        ),
                        required_by_organization=forced,
                        version=preference.version if preference is not None else 0,
                    )
                )
        user = await self._session.get(User, self._principal.user_id)
        if user is None:
            raise AppError(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
        return CustomerNotificationPreferenceListResponse(
            destination=_mask_email(user.email),
            items=items,
        )

    async def update(
        self,
        payload: CustomerNotificationPreferenceUpdate,
    ) -> CustomerNotificationPreferenceResponse:
        organization = await self._session.scalar(
            select(Organization)
            .join(
                OrganizationMember,
                OrganizationMember.organization_id == Organization.id,
            )
            .where(
                Organization.id == payload.organization_id,
                *active_membership_conditions(
                    user_id=self._principal.user_id,
                    organization_id=Organization.id,
                ),
                Organization.is_active.is_(True),
            )
        )
        if organization is None:
            raise AppError(404, "ORGANIZATION_NOT_FOUND", "The organization was not found.")
        forced = bool(
            await self._session.scalar(
                select(OrganizationNotificationPolicy.id).where(
                    OrganizationNotificationPolicy.organization_id == organization.id,
                    OrganizationNotificationPolicy.event_type == payload.event_type,
                    OrganizationNotificationPolicy.email_required.is_(True),
                )
            )
        )
        if forced and not payload.email_enabled:
            raise AppError(
                409,
                "NOTIFICATION_REQUIRED_BY_ORGANIZATION",
                "This notification is required by the organization.",
            )
        preference = await self._session.scalar(
            select(CustomerNotificationPreference)
            .where(
                CustomerNotificationPreference.user_id == self._principal.user_id,
                CustomerNotificationPreference.organization_id == organization.id,
                CustomerNotificationPreference.event_type == payload.event_type,
            )
            .with_for_update()
        )
        if preference is None:
            if payload.version != 0:
                raise self._conflict()
            preference = CustomerNotificationPreference(
                user_id=self._principal.user_id,
                organization_id=organization.id,
                event_type=payload.event_type,
                email_enabled=payload.email_enabled,
                version=1,
            )
            self._session.add(preference)
        else:
            if preference.version != payload.version:
                raise self._conflict()
            preference.email_enabled = payload.email_enabled
            preference.version += 1
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise self._conflict() from exc
        add_audit_event(
            self._session,
            action="CUSTOMER_NOTIFICATION_PREFERENCE_CHANGED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=organization.id,
            target_type="notification_preference",
            target_id=preference.id,
            details={
                "event_type": payload.event_type,
                "email_enabled": payload.email_enabled,
            },
        )
        await self._session.commit()
        return CustomerNotificationPreferenceResponse(
            organization_id=organization.id,
            organization_name=organization.name,
            event_type=payload.event_type,
            email_enabled=forced or preference.email_enabled,
            required_by_organization=forced,
            version=preference.version,
        )

    @staticmethod
    def _conflict() -> AppError:
        return AppError(
            409,
            "NOTIFICATION_PREFERENCE_VERSION_CONFLICT",
            "The notification preference changed; reload it.",
        )


async def queue_customer_notification(
    session: AsyncSession,
    *,
    organization_id: UUID,
    event_type: CustomerNotificationEvent,
    event_key: str,
    subject: str,
    message: str,
    recipient_user_id: UUID | None = None,
) -> int:
    filters = [
        *active_membership_conditions(
            user_id=User.id,
            organization_id=organization_id,
        ),
        Organization.is_active.is_(True),
        User.is_active.is_(True),
        User.deleted_at.is_(None),
        User.role == UserRole.CUSTOMER.value,
    ]
    if recipient_user_id is not None:
        filters.append(User.id == recipient_user_id)
    users = (
        await session.scalars(
            select(User)
            .join(OrganizationMember, OrganizationMember.user_id == User.id)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .where(*filters)
        )
    ).all()
    queued = 0
    for user in users:
        if not await _enabled(session, user.id, organization_id, event_type):
            continue
        statement = (
            insert(CustomerNotificationDelivery)
            .values(
                user_id=user.id,
                organization_id=organization_id,
                event_type=event_type,
                event_key=event_key[:160],
                subject=subject[:200],
                message=message[:2000],
                status="PENDING",
                next_attempt_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    CustomerNotificationDelivery.user_id,
                    CustomerNotificationDelivery.event_key,
                ]
            )
            .returning(CustomerNotificationDelivery.id)
        )
        if (await session.execute(statement)).scalar_one_or_none() is not None:
            queued += 1
    return queued


class CustomerNotificationDispatcher:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        sender: EmailSender | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._sender = sender or self._send_email

    async def deliver_due(self) -> int:
        now = datetime.now(UTC)
        rows = (
            await self._session.scalars(
                select(CustomerNotificationDelivery)
                .where(
                    CustomerNotificationDelivery.status.in_(["PENDING", "RETRY"]),
                    CustomerNotificationDelivery.next_attempt_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        delivered = 0
        for item in rows:
            user = await self._session.get(User, item.user_id)
            if (
                user is None
                or not user.is_active
                or user.deleted_at is not None
                or not await _enabled(
                    self._session,
                    item.user_id,
                    item.organization_id,
                    item.event_type,
                )
            ):
                item.status = "CANCELLED"
                item.last_error_code = "RECIPIENT_NOT_ELIGIBLE"
                continue
            item.attempt_count += 1
            try:
                await self._sender(user.email, item.subject, item.message)
            except (AppError, TimeoutError):
                item.last_error_code = "CUSTOMER_EMAIL_DELIVERY_FAILED"
                if item.attempt_count >= self._settings.notification_max_attempts:
                    item.status = "FAILED"
                else:
                    item.status = "RETRY"
                    item.next_attempt_at = now + timedelta(
                        seconds=min(3600, 2**item.attempt_count * 15)
                    )
            else:
                item.status = "DELIVERED"
                item.delivered_at = now
                item.last_error_code = None
                delivered += 1
        await self._session.commit()
        return delivered

    async def _send_email(self, recipient: str, subject: str, body: str) -> None:
        settings = self._settings
        smtp_host = settings.smtp_host
        if not smtp_host or not settings.smtp_from_address or "@" not in recipient:
            raise AppError(503, "EMAIL_DELIVERY_UNAVAILABLE", "Email delivery is not configured.")
        message = EmailMessage()
        message["From"] = settings.smtp_from_address
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        def send() -> None:
            context = ssl.create_default_context()
            if settings.smtp_use_tls:
                client: smtplib.SMTP = smtplib.SMTP_SSL(
                    smtp_host,
                    settings.smtp_port,
                    timeout=settings.notification_timeout_seconds,
                    context=context,
                )
            else:
                client = smtplib.SMTP(
                    smtp_host,
                    settings.smtp_port,
                    timeout=settings.notification_timeout_seconds,
                )
                client.starttls(context=context)
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
            raise AppError(503, "EMAIL_DELIVERY_FAILED", "Email delivery failed.") from exc


async def _enabled(
    session: AsyncSession,
    user_id: UUID,
    organization_id: UUID,
    event_type: str,
) -> bool:
    membership = await session.scalar(
        select(OrganizationMember.id)
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .where(
            *active_membership_conditions(
                user_id=user_id,
                organization_id=organization_id,
            ),
            Organization.is_active.is_(True),
        )
    )
    if membership is None:
        return False
    forced = await session.scalar(
        select(OrganizationNotificationPolicy.email_required).where(
            OrganizationNotificationPolicy.organization_id == organization_id,
            OrganizationNotificationPolicy.event_type == event_type,
        )
    )
    if forced:
        return True
    preference = await session.scalar(
        select(CustomerNotificationPreference.email_enabled).where(
            CustomerNotificationPreference.user_id == user_id,
            CustomerNotificationPreference.organization_id == organization_id,
            CustomerNotificationPreference.event_type == event_type,
        )
    )
    default = DEFAULTS.get(cast(CustomerNotificationEvent, event_type), False)
    return bool(default if preference is None else preference)


def _mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    visible = local[:1]
    return f"{visible}{'*' * max(3, len(local) - 1)}@{domain}"
