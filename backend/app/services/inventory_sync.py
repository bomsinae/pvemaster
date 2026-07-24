from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Workload
from app.models.scheduling import RunStatus, SyncRun
from app.proxmox.client import ProxmoxClient
from app.schemas.cluster import GuestResponse
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.audit import add_audit_event
from app.services.maintenance import acquire_lease, release_lease, require_current_lease

GuestLoader = Callable[[UUID], Awaitable[list[dict[str, object]]]]


class ScheduledInventorySyncRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        guest_loader: GuestLoader | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._guest_loader = guest_loader or self._load_guests

    async def run(self, cluster_id: UUID) -> SyncRun | None:
        owner_id = uuid4()
        grant = await acquire_lease(
            self._session,
            name=f"inventory:{cluster_id}",
            owner_id=owner_id,
            ttl_seconds=self._settings.scheduler_lease_seconds,
        )
        if grant is None:
            return None
        now = datetime.now(UTC)
        generation = (
            int(
                (
                    await self._session.scalar(
                        select(func.coalesce(func.max(SyncRun.generation), 0)).where(
                            SyncRun.cluster_id == cluster_id
                        )
                    )
                )
                or 0
            )
            + 1
        )
        run = SyncRun(
            cluster_id=cluster_id,
            generation=generation,
            status=RunStatus.RUNNING.value,
            triggered_by="scheduler",
            started_at=now,
            finished_at=None,
            error_code=None,
            resource_counts={},
        )
        self._session.add(run)
        await self._session.commit()
        try:
            raw_guests = await self._guest_loader(cluster_id)
            await require_current_lease(self._session, grant)
            guests = self._validate_guests(raw_guests)
            counts = await self._apply(cluster_id, guests)
            await require_current_lease(self._session, grant)
        except Exception as exc:
            await self._session.rollback()
            run = await self._session.get(SyncRun, run.id, with_for_update=True) or run
            run.status = RunStatus.FAILED.value
            run.error_code = self._error_code(exc)
            run.finished_at = datetime.now(UTC)
            cluster = await self._session.get(Cluster, cluster_id, with_for_update=True)
            if cluster is not None:
                cluster.last_connection_error_code = run.error_code
            await self._session.commit()
            raise
        else:
            run.status = RunStatus.SUCCEEDED.value
            run.resource_counts = counts
            run.finished_at = datetime.now(UTC)
            cluster = await self._session.get(Cluster, cluster_id, with_for_update=True)
            if cluster is not None:
                cluster.last_connection_error_code = None
                cluster.last_connected_at = run.finished_at
            add_audit_event(
                self._session,
                action="SCHEDULED_INVENTORY_SYNC",
                outcome="SUCCEEDED",
                request_id=None,
                actor_user_id=None,
                actor_role=None,
                target_type="cluster",
                target_id=cluster_id,
                after=counts,
            )
            await self._session.commit()
            return run
        finally:
            await release_lease(self._session, grant)

    async def _apply(
        self,
        cluster_id: UUID,
        guests: list[GuestResponse],
    ) -> dict[str, object]:
        cluster = await self._session.scalar(
            select(Cluster)
            .where(Cluster.id == cluster_id, Cluster.is_active.is_(True))
            .with_for_update()
        )
        if cluster is None:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
        rows = await self._session.scalars(
            select(Workload).where(Workload.cluster_id == cluster_id)
        )
        existing = {item.vmid: item for item in rows.all()}
        seen: set[int] = set()
        created = 0
        updated = 0
        observed_at = datetime.now(UTC)
        for guest in guests:
            kind = guest.type.upper()
            if (
                guest.vmid <= 0
                or guest.vmid in seen
                or kind not in {"QEMU", "LXC"}
                or guest.node is None
                or not guest.node.strip()
                or len(guest.node) > 255
                or (guest.name is not None and len(guest.name) > 255)
            ):
                raise AppError(
                    502,
                    "PVE_INVALID_RESPONSE",
                    "The Proxmox API returned an invalid workload item.",
                )
            seen.add(guest.vmid)
            power_state = guest.status.upper() if guest.status else "UNKNOWN"
            if len(power_state) > 20:
                power_state = "UNKNOWN"
            workload = existing.get(guest.vmid)
            if workload is None:
                self._session.add(
                    Workload(
                        cluster_id=cluster_id,
                        vmid=guest.vmid,
                        node=guest.node,
                        kind=kind,
                        name=guest.name,
                        power_state=power_state,
                        cpu_cores=guest.maxcpu,
                        memory_bytes=guest.maxmem,
                        disk_bytes=guest.maxdisk,
                        is_template=bool(guest.template),
                        is_present=True,
                        organization_id=None,
                        observed_at=observed_at,
                        version=1,
                    )
                )
                created += 1
            else:
                workload.node = guest.node
                workload.kind = kind
                workload.name = guest.name
                workload.power_state = power_state
                workload.cpu_cores = guest.maxcpu
                workload.memory_bytes = guest.maxmem
                workload.disk_bytes = guest.maxdisk
                workload.is_template = bool(guest.template)
                workload.is_present = True
                workload.observed_at = observed_at
                workload.version += 1
                updated += 1
        await self._session.flush()
        return {"discovered": len(guests), "created": created, "updated": updated}

    async def _load_guests(self, cluster_id: UUID) -> list[dict[str, object]]:
        cluster = await self._session.scalar(
            select(Cluster).where(Cluster.id == cluster_id, Cluster.is_active.is_(True))
        )
        credential = await self._session.scalar(
            select(ClusterCredential).where(
                ClusterCredential.cluster_id == cluster_id,
                ClusterCredential.is_active.is_(True),
            )
        )
        if cluster is None or credential is None:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
        try:
            secret = self._cipher.decrypt(
                EncryptedCredential(
                    ciphertext=credential.secret_ciphertext,
                    nonce=credential.secret_nonce,
                    key_version=credential.key_version,
                ),
                cluster_id=cluster.id,
                credential_id=credential.id,
            )
        except InvalidTag as exc:
            raise AppError(
                500,
                "CREDENTIAL_DECRYPTION_FAILED",
                "The cluster credential could not be decrypted.",
            ) from exc
        api_base_url = cluster.api_base_url
        ca_bundle_pem = cluster.ca_bundle_pem
        token_identifier = credential.token_identifier
        await self._session.rollback()
        async with self._client(
            api_base_url=api_base_url,
            ca_bundle_pem=ca_bundle_pem,
            token_identifier=token_identifier,
            token_secret=secret,
        ) as client:
            result: list[dict[str, object]] = await client.get_guests()
        return result

    @asynccontextmanager
    async def _client(
        self,
        *,
        api_base_url: str,
        ca_bundle_pem: str | None,
        token_identifier: str,
        token_secret: str,
    ) -> AsyncIterator[ProxmoxClient]:
        async with ProxmoxClient(
            api_base_url=api_base_url,
            token_identifier=token_identifier,
            token_secret=token_secret,
            ca_bundle_pem=ca_bundle_pem,
            connect_timeout=self._settings.pve_connect_timeout_seconds,
            read_timeout=self._settings.pve_read_timeout_seconds,
            max_connections=self._settings.pve_max_connections,
            max_keepalive_connections=self._settings.pve_max_keepalive_connections,
            allowed_hosts=self._settings.pve_allowed_hosts,
            allowed_networks=self._settings.pve_allowed_networks,
        ) as client:
            yield client

    @staticmethod
    def _validate_guests(raw: list[dict[str, object]]) -> list[GuestResponse]:
        try:
            return [GuestResponse.model_validate(item) for item in raw]
        except ValidationError as exc:
            raise AppError(
                502,
                "PVE_INVALID_RESPONSE",
                "The Proxmox API returned invalid inventory data.",
            ) from exc

    @staticmethod
    def _error_code(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        return code[:64] if isinstance(code, str) and code else "INVENTORY_SYNC_FAILED"
