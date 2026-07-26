import hashlib
from collections.abc import Awaitable
from dataclasses import dataclass
from secrets import token_urlsafe
from typing import cast
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import Organization, OrganizationMember, User, UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Workload
from app.proxmox.client import ConsoleProxyTicket, ProxmoxClient
from app.schemas.console import ConsoleSessionResponse
from app.security.access import Principal
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event
from app.services.organization_access import WORKLOAD_READ_ROLES, active_membership_conditions

_CONSUME_SCRIPT = """
local expected = redis.call('HGET', KEYS[1], 'token_hash')
if not expected or expected ~= ARGV[1] then return {} end
local values = redis.call('HGETALL', KEYS[1])
redis.call('DEL', KEYS[1])
return values
"""

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class ConsoleGrant:
    session_id: UUID
    user_id: UUID
    session_epoch: int
    role: UserRole
    workload_id: UUID
    pve_kind: str
    pve_user: str | None
    pve_port: int
    pve_ticket: str


@dataclass(frozen=True)
class ConsoleConnection:
    workload: Workload
    client: ProxmoxClient
    proxy: ConsoleProxyTicket


class ConsoleSessionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        redis: Redis,
        settings: Settings,
        cipher: CredentialCipher,
        principal: Principal,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._redis = redis
        self._settings = settings
        self._cipher = cipher
        self._principal = principal
        self._request_id = request_id
        self._source_ip = source_ip

    async def create(self, workload_id: UUID) -> ConsoleSessionResponse:
        workload = await self._authorized_workload(workload_id)
        if workload.power_state.upper() != "RUNNING":
            raise AppError(
                409,
                "CONSOLE_REQUIRES_RUNNING_GUEST",
                "The guest must be running before opening its console.",
            )
        await self._check_rate_limit()
        client = await build_console_client(
            self._session,
            workload=workload,
            settings=self._settings,
            cipher=self._cipher,
        )
        if client is None:
            raise AppError(
                503,
                "CONSOLE_CLUSTER_UNAVAILABLE",
                "The Proxmox cluster is not available for console access.",
            )
        try:
            proxy = await client.create_console_proxy(
                kind=workload.kind,
                node=workload.node,
                vmid=workload.vmid,
            )
        finally:
            await client.aclose()
        session_id = uuid4()
        protocol_token = token_urlsafe(32)
        key = self._session_key(session_id)
        try:
            await cast(
                Awaitable[int],
                self._redis.hset(
                    key,
                    mapping={
                        "token_hash": self._token_hash(protocol_token),
                        "user_id": str(self._principal.user_id),
                        "session_epoch": str(self._principal.session_epoch),
                        "role": self._principal.role.value,
                        "workload_id": str(workload.id),
                        "pve_kind": proxy.kind,
                        "pve_user": proxy.user or "",
                        "pve_port": str(proxy.port),
                        "pve_ticket": proxy.ticket,
                    },
                ),
            )
            await self._redis.expire(key, self._settings.console_session_ttl_seconds)
        except RedisError as exc:
            raise AppError(
                503,
                "CONSOLE_SESSION_UNAVAILABLE",
                "A console session could not be created.",
            ) from exc
        add_audit_event(
            self._session,
            action="CONSOLE_SESSION_CREATED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=workload.organization_id,
            workload_id=workload.id,
            source_ip=self._source_ip,
            target_type="workload",
            target_id=workload.id,
            details={"console_kind": workload.kind},
        )
        await self._session.commit()
        return ConsoleSessionResponse(
            session_id=session_id,
            websocket_path=f"/api/v1/console/ws/{session_id}",
            protocol_token=protocol_token,
            console_type="NOVNC" if workload.kind == "QEMU" else "TERMINAL",
            rfb_password=proxy.ticket if workload.kind == "QEMU" else None,
            expires_in=self._settings.console_session_ttl_seconds,
        )

    async def _authorized_workload(self, workload_id: UUID) -> Workload:
        return await authorized_console_workload(
            self._session, principal=self._principal, workload_id=workload_id
        )

    async def _check_rate_limit(self) -> None:
        key = f"console:rate:{self._principal.user_id}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, 60)
        except RedisError as exc:
            raise AppError(
                503,
                "CONSOLE_SESSION_UNAVAILABLE",
                "A console session could not be created.",
            ) from exc
        if count > self._settings.console_sessions_per_minute:
            raise AppError(429, "CONSOLE_RATE_LIMITED", "Too many console sessions were requested.")

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _session_key(session_id: UUID) -> str:
        return f"console:session:{session_id}"


async def consume_console_grant(
    redis: Redis, *, session_id: UUID, protocol_token: str
) -> ConsoleGrant | None:
    try:
        values = await cast(
            Awaitable[list[str]],
            redis.eval(
                _CONSUME_SCRIPT,
                1,
                ConsoleSessionService._session_key(session_id),
                ConsoleSessionService._token_hash(protocol_token),
            ),
        )
    except RedisError:
        return None
    if not isinstance(values, list) or not values:
        return None
    payload = {str(values[index]): str(values[index + 1]) for index in range(0, len(values), 2)}
    try:
        return ConsoleGrant(
            session_id=session_id,
            user_id=UUID(payload["user_id"]),
            session_epoch=int(payload["session_epoch"]),
            role=UserRole(payload["role"]),
            workload_id=UUID(payload["workload_id"]),
            pve_kind=payload["pve_kind"],
            pve_user=payload.get("pve_user") or None,
            pve_port=int(payload["pve_port"]),
            pve_ticket=payload["pve_ticket"],
        )
    except (KeyError, ValueError):
        return None


async def acquire_console_slot(redis: Redis, grant: ConsoleGrant, ttl: int) -> bool:
    try:
        acquired = await redis.set(_console_slot_key(grant), str(grant.session_id), ex=ttl, nx=True)
    except RedisError:
        return False
    return bool(acquired)


async def release_console_slot(redis: Redis, grant: ConsoleGrant) -> None:
    try:
        await cast(
            Awaitable[int],
            redis.eval(
                _RELEASE_SCRIPT,
                1,
                _console_slot_key(grant),
                str(grant.session_id),
            ),
        )
    except RedisError:
        pass


def _console_slot_key(grant: ConsoleGrant) -> str:
    return f"console:live:{grant.user_id}:{grant.workload_id}"


async def load_console_connection(
    session: AsyncSession,
    *,
    grant: ConsoleGrant,
    settings: Settings,
    cipher: CredentialCipher,
) -> ConsoleConnection | None:
    user = await session.scalar(select(User).where(User.id == grant.user_id))
    if (
        user is None
        or not user.is_active
        or user.session_epoch != grant.session_epoch
        or user.role != grant.role.value
    ):
        return None
    principal = Principal(
        user_id=user.id,
        email=user.email,
        role=UserRole(user.role),
        session_epoch=user.session_epoch,
    )
    try:
        workload = await authorized_console_workload(
            session, principal=principal, workload_id=grant.workload_id
        )
    except AppError:
        return None
    if workload.power_state.upper() != "RUNNING":
        return None
    expected_pve_kind = "qemu" if workload.kind == "QEMU" else "lxc"
    if grant.pve_kind != expected_pve_kind:
        return None
    if grant.pve_kind == "lxc" and grant.pve_user is None:
        return None
    client = await build_console_client(
        session,
        workload=workload,
        settings=settings,
        cipher=cipher,
    )
    if client is None:
        return None
    return ConsoleConnection(
        workload=workload,
        client=client,
        proxy=ConsoleProxyTicket(
            kind=grant.pve_kind,
            node=workload.node,
            vmid=workload.vmid,
            port=grant.pve_port,
            ticket=grant.pve_ticket,
            user=grant.pve_user,
        ),
    )


async def build_console_client(
    session: AsyncSession,
    *,
    workload: Workload,
    settings: Settings,
    cipher: CredentialCipher,
) -> ProxmoxClient | None:
    row = (
        await session.execute(
            select(Cluster, ClusterCredential)
            .join(
                ClusterCredential,
                (ClusterCredential.cluster_id == Cluster.id) & ClusterCredential.is_active,
            )
            .where(Cluster.id == workload.cluster_id, Cluster.is_active.is_(True))
        )
    ).one_or_none()
    if row is None:
        return None
    cluster, credential = row
    try:
        token_secret = cipher.decrypt(
            EncryptedCredential(
                ciphertext=credential.secret_ciphertext,
                nonce=credential.secret_nonce,
                key_version=credential.key_version,
            ),
            cluster_id=cluster.id,
            credential_id=credential.id,
        )
    except (InvalidTag, UnicodeDecodeError):
        return None
    return ProxmoxClient(
        api_base_url=cluster.api_base_url,
        token_identifier=credential.token_identifier,
        token_secret=token_secret,
        connect_timeout=settings.pve_connect_timeout_seconds,
        read_timeout=settings.pve_read_timeout_seconds,
        max_connections=settings.pve_max_connections,
        max_keepalive_connections=settings.pve_max_keepalive_connections,
        allowed_hosts=settings.pve_allowed_hosts,
        allowed_networks=settings.pve_allowed_networks,
        ca_bundle_pem=cluster.ca_bundle_pem,
    )


def protocol_token_from_header(header: str | None) -> str | None:
    if not header:
        return None
    prefix = "pvemaster.console."
    for value in (item.strip() for item in header.split(",")):
        if value.startswith(prefix) and len(value) > len(prefix):
            return value.removeprefix(prefix)
    return None


async def authorized_console_workload(
    session: AsyncSession, *, principal: Principal, workload_id: UUID
) -> Workload:
    query = select(Workload).where(
        Workload.id == workload_id,
        Workload.kind.in_(["QEMU", "LXC"]),
        Workload.is_present.is_(True),
        Workload.is_template.is_(False),
    )
    if principal.role == UserRole.CUSTOMER:
        membership = exists(
            select(OrganizationMember.id).where(
                *active_membership_conditions(
                    user_id=principal.user_id,
                    organization_id=Workload.organization_id,
                    roles=WORKLOAD_READ_ROLES,
                ),
            )
        )
        active_organization = exists(
            select(Organization.id).where(
                Organization.id == Workload.organization_id,
                Organization.is_active.is_(True),
            )
        )
        query = query.where(
            Workload.organization_id.is_not(None),
            membership,
            active_organization,
        )
    elif principal.role not in {UserRole.SUPER_ADMIN, UserRole.OPERATOR}:
        raise AppError(403, "FORBIDDEN", "You do not have permission to open a console.")
    workload = await session.scalar(query)
    if workload is None:
        code = "VM_NOT_FOUND" if principal.role == UserRole.CUSTOMER else "WORKLOAD_NOT_FOUND"
        raise AppError(404, code, "The workload was not found.")
    return workload
