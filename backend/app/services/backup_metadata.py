from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.backup import BackupRun, BackupTarget
from app.models.cluster import Cluster, ClusterCredential
from app.models.operation import Operation, OperationStatus, Workload
from app.proxmox.client import ProxmoxClient
from app.security.credentials import CredentialCipher, EncryptedCredential
from app.services.backup_runner import BackupOperationRunner

ContentLoader = Callable[[UUID], Awaitable[list[dict[str, Any]]]]


class BackupMetadataReconciler:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        cipher: CredentialCipher,
        content_loader: ContentLoader | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._cipher = cipher
        self._content_loader = content_loader or self._load_content

    async def reconcile(self, *, limit: int = 50) -> int:
        run_ids = (
            await self._session.scalars(
                select(BackupRun.id)
                .join(Operation, Operation.id == BackupRun.operation_id)
                .where(
                    Operation.status == OperationStatus.SUCCEEDED.value,
                    BackupRun.snapshot_volume_id.is_(None),
                )
                .order_by(BackupRun.finished_at, BackupRun.created_at)
                .limit(limit)
            )
        ).all()
        reconciled = 0
        for run_id in run_ids:
            try:
                content = await self._content_loader(run_id)
            except AppError:
                await self._session.rollback()
                continue
            row = (
                await self._session.execute(
                    select(BackupRun, Operation, Workload, BackupTarget)
                    .join(Operation, Operation.id == BackupRun.operation_id)
                    .join(Workload, Workload.id == BackupRun.workload_id)
                    .join(BackupTarget, BackupTarget.id == BackupRun.backup_target_id)
                    .where(BackupRun.id == run_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                await self._session.rollback()
                continue
            run, operation, workload, target = row
            snapshot = BackupOperationRunner._latest_snapshot(content, workload.vmid)
            target.last_checked_at = datetime.now(UTC)
            target.last_observed_available = snapshot is not None
            if snapshot is None:
                await self._session.commit()
                continue
            run.snapshot_volume_id = str(snapshot["volid"])
            ctime = snapshot.get("ctime")
            if isinstance(ctime, (int, float)):
                run.snapshot_time = datetime.fromtimestamp(ctime, tz=UTC)
            size = snapshot.get("size")
            if isinstance(size, int) and size >= 0:
                run.size_bytes = size
            operation.result = {
                **operation.result,
                "snapshot_volume_id": run.snapshot_volume_id,
                "metadata_pending": False,
            }
            await self._session.commit()
            reconciled += 1
        return reconciled

    async def _load_content(self, run_id: UUID) -> list[dict[str, Any]]:
        row = (
            await self._session.execute(
                select(BackupRun, BackupTarget, Workload, Cluster, ClusterCredential)
                .join(BackupTarget, BackupTarget.id == BackupRun.backup_target_id)
                .join(Workload, Workload.id == BackupRun.workload_id)
                .join(Cluster, Cluster.id == BackupTarget.cluster_id)
                .join(
                    ClusterCredential,
                    (ClusterCredential.cluster_id == Cluster.id)
                    & ClusterCredential.is_active.is_(True),
                )
                .where(
                    BackupRun.id == run_id,
                    BackupTarget.is_enabled.is_(True),
                    Cluster.is_active.is_(True),
                )
            )
        ).one_or_none()
        if row is None:
            raise AppError(404, "BACKUP_NOT_FOUND", "The backup run was not found.")
        _run, target, workload, cluster, credential = row
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
        node = workload.node
        storage_id = target.storage_id
        vmid = workload.vmid
        await self._session.rollback()
        async with self._client(
            api_base_url=api_base_url,
            ca_bundle_pem=ca_bundle_pem,
            token_identifier=token_identifier,
            token_secret=secret,
        ) as client:
            return await client.get_backup_content(
                node=node,
                storage=storage_id,
                vmid=vmid,
            )

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
