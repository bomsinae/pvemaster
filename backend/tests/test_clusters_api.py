from datetime import UTC, datetime
from secrets import token_urlsafe
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.clusters import get_cluster_service
from app.core.config import Settings
from app.core.errors import AppError
from app.dependencies import get_current_principal
from app.models.auth import UserRole
from app.models.cluster import Cluster, ClusterCredential
from app.schemas.cluster import (
    ClusterCreate,
    ClusterRemovalBlock,
    ClusterResourceOverview,
    ClusterResponse,
    ClusterUpdate,
    CredentialSummary,
    GuestResponse,
    NodeMetricRange,
    NodeMetricSeriesResponse,
    NodeResponse,
    StorageResponse,
)
from app.security.access import Principal
from app.security.credentials import CredentialCipher
from app.services.clusters import ClusterConnection, ClusterService


class FakeClusterService:
    async def create(self, request: ClusterCreate) -> ClusterResponse:
        now = datetime.now(UTC)
        return ClusterResponse(
            id=uuid4(),
            name=request.name,
            api_base_url=str(request.api_base_url).rstrip("/"),
            is_active=True,
            ca_configured=request.ca_bundle_pem is not None,
            last_connection_error_code=None,
            last_connected_at=now,
            credential=CredentialSummary(token_identifier=request.token_identifier),
            created_at=now,
            updated_at=now,
            version=1,
        )

    async def node_metrics(
        self,
        cluster_id: UUID,
        *,
        node: str,
        metric_range: NodeMetricRange,
    ) -> NodeMetricSeriesResponse:
        return NodeMetricSeriesResponse(
            cluster_id=cluster_id,
            node=node,
            range=metric_range,
            observed_at=datetime.now(UTC),
            items=[],
        )


async def test_cluster_create_response_does_not_expose_token_secret(app: FastAPI) -> None:
    secret = token_urlsafe(32)
    fake_service = FakeClusterService()

    async def override_service() -> ClusterService:
        return cast(ClusterService, fake_service)

    app.dependency_overrides[get_cluster_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/clusters",
                json={
                    "name": "test-cluster",
                    "api_base_url": "https://pve.example.test:8006",
                    "token_identifier": "service@pve!pvemaster",
                    "token_secret": secret,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert secret not in response.text
    assert "token_secret" not in response.json()
    assert response.json()["credential"] == {
        "token_identifier": "service@pve!pvemaster",
        "configured": True,
        "last_used_at": None,
    }


async def test_cluster_validation_error_does_not_expose_token_secret(app: FastAPI) -> None:
    oversized_secret = token_urlsafe(800)

    async def override_principal() -> Principal:
        return Principal(
            user_id=uuid4(),
            email="admin@example.test",
            role=UserRole.SUPER_ADMIN,
            session_epoch=0,
        )

    app.dependency_overrides[get_current_principal] = override_principal

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/admin/clusters",
                json={
                    "name": "test-cluster",
                    "api_base_url": "https://pve.example.test:8006",
                    "token_identifier": "service@pve!pvemaster",
                    "token_secret": oversized_secret,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert oversized_secret not in response.text


async def test_node_metrics_route_validates_range_and_returns_sparse_series(
    app: FastAPI,
) -> None:
    async def override_service() -> ClusterService:
        return cast(ClusterService, FakeClusterService())

    cluster_id = uuid4()
    app.dependency_overrides[get_cluster_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/admin/clusters/{cluster_id}/nodes/pve-a/metrics?range=six_hours"
            )
            invalid = await client.get(
                f"/api/v1/admin/clusters/{cluster_id}/nodes/pve-a/metrics?range=month"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["range"] == "six_hours"
    assert response.json()["items"] == []
    assert invalid.status_code == 422


def test_required_cluster_routes_are_registered(app: FastAPI) -> None:
    paths = app.openapi()["paths"]

    assert {
        "/api/v1/admin/clusters",
        "/api/v1/admin/clusters/{cluster_id}",
        "/api/v1/admin/clusters/overview",
        "/api/v1/admin/clusters/{cluster_id}/removal-check",
        "/api/v1/admin/clusters/{cluster_id}/test",
        "/api/v1/admin/clusters/{cluster_id}/nodes",
        "/api/v1/admin/clusters/{cluster_id}/nodes/{node}/metrics",
        "/api/v1/admin/clusters/{cluster_id}/guests",
        "/api/v1/admin/clusters/{cluster_id}/storages",
        "/api/v1/admin/clusters/{cluster_id}/workloads/import",
        "/api/v1/admin/workloads",
        "/api/v1/admin/workloads/{workload_id}/assign",
        "/api/v1/admin/workloads/{workload_id}/assignment",
        "/api/v1/admin/workloads/{workload_id}/assignments",
    }.issubset(paths)


class PartialOverviewService(ClusterService):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.connection_results: list[tuple[object, str | None]] = []

    async def _fetch_resource_overview(
        self,
        cluster_id: UUID,
        name: str,
        connection: ClusterConnection,
    ) -> ClusterResourceOverview:
        del connection
        if name == "offline-pve":
            raise AppError(504, "PVE_TIMEOUT", "The Proxmox API request timed out.")
        return ClusterResourceOverview(
            cluster_id=cluster_id,
            name=name,
            connected=True,
            observed_at=datetime.now(UTC),
            node_count=1,
            guest_count=3,
            running_guest_count=2,
            qemu_count=2,
            lxc_count=1,
            storage_count=1,
            storage_used_bytes=25,
            storage_total_bytes=100,
            vm_storage_count=1,
            vm_storage_used_bytes=25,
            vm_storage_total_bytes=100,
            nodes=[],
        )

    async def _record_connection_result(self, cluster_id: UUID, error_code: str | None) -> None:
        self.connection_results.append((cluster_id, error_code))


async def test_resource_overview_preserves_healthy_clusters_when_one_times_out(
    settings: Settings,
) -> None:
    application_key = token_urlsafe(32)
    cipher = CredentialCipher(application_key)
    clusters: list[Cluster] = []
    for name in ("healthy-pve", "offline-pve"):
        cluster_id = uuid4()
        credential_id = uuid4()
        encrypted = cipher.encrypt(
            token_urlsafe(32), cluster_id=cluster_id, credential_id=credential_id
        )
        cluster = Cluster(
            id=cluster_id,
            name=name,
            api_base_url=f"https://{name}.example.test:8006",
            is_active=True,
            version=1,
        )
        cluster.credentials = [
            ClusterCredential(
                id=credential_id,
                cluster_id=cluster_id,
                token_identifier="service@pve!pvemaster",
                secret_ciphertext=encrypted.ciphertext,
                secret_nonce=encrypted.nonce,
                key_version=encrypted.key_version,
                is_active=True,
            )
        ]
        clusters.append(cluster)

    session = AsyncMock(spec=AsyncSession)
    scalar_result = MagicMock()
    scalar_result.unique.return_value.all.return_value = clusters
    session.scalars.return_value = scalar_result
    service = PartialOverviewService(
        session=cast(AsyncSession, session),
        settings=settings,
        cipher=cipher,
        principal=Principal(
            user_id=uuid4(),
            email="operator@example.test",
            role=UserRole.OPERATOR,
            session_epoch=0,
        ),
    )

    overview = await service.resource_overview()

    assert [item.name for item in overview] == ["healthy-pve", "offline-pve"]
    assert overview[0].connected is True
    assert overview[0].guest_count == 3
    assert overview[1].connected is False
    assert overview[1].error_code == "PVE_TIMEOUT"
    assert [error for _cluster_id, error in service.connection_results] == [
        None,
        "PVE_TIMEOUT",
    ]


def test_node_resource_overview_uses_live_status_metrics() -> None:
    node = ClusterService._node_resource_overview(
        NodeResponse(
            node="pve-a",
            status="online",
            cpu=0.1,
            maxcpu=16,
            mem=10,
            maxmem=100,
            disk=20,
            maxdisk=200,
            uptime=300,
        ),
        {
            "cpu": 0.25,
            "memory": {"used": 40, "total": 100},
            "rootfs": {"used": 60, "total": 200},
            "loadavg": ["1.10", "0.90", "0.70"],
            "uptime": 600,
        },
    )

    assert node.cpu == 0.25
    assert node.memory_used_bytes == 40
    assert node.disk_used_bytes == 60
    assert node.load_average == [1.1, 0.9, 0.7]
    assert node.uptime_seconds == 600


def test_node_metric_point_normalizes_rrd_fields_and_preserves_missing_psi() -> None:
    point = ClusterService._node_metric_point(
        {
            "time": "1720000000",
            "cpu": 0.25,
            "loadavg": "1.5",
            "memused": 40,
            "memtotal": 100,
            "netin": 1024,
            "netout": 2048,
            "pressurecpusome": 0.75,
            "pressureiosome": float("nan"),
        }
    )

    assert point is not None
    assert point.cpu_usage == 0.25
    assert point.server_load == 1.5
    assert point.memory_used_bytes == 40
    assert point.network_transmit_bps == 2048
    assert point.cpu_pressure_some == 0.75
    assert point.io_pressure_some is None
    assert point.io_pressure_full is None
    assert point.memory_pressure_some is None


def test_node_metric_point_rejects_missing_timestamp() -> None:
    assert ClusterService._node_metric_point({"cpu": 0.25}) is None


def test_storage_response_normalizes_cluster_resource_capacity() -> None:
    storage = StorageResponse.model_validate(
        {
            "storage": "local-lvm",
            "node": "pve-a",
            "maxdisk": 100 * 1024**3,
            "disk": 25 * 1024**3,
        }
    )

    assert storage.total == 100 * 1024**3
    assert storage.used == 25 * 1024**3
    assert storage.avail == 75 * 1024**3


def test_guest_response_preserves_live_resource_usage() -> None:
    guest = GuestResponse.model_validate(
        {
            "vmid": 101,
            "node": "pve-a",
            "type": "qemu",
            "name": "web-01",
            "status": "running",
            "cpu": 0.125,
            "maxcpu": 4,
            "mem": 2 * 1024**3,
            "maxmem": 8 * 1024**3,
            "disk": 25 * 1024**3,
            "maxdisk": 100 * 1024**3,
            "uptime": 90_061,
        }
    )

    assert guest.cpu == 0.125
    assert guest.mem == 2 * 1024**3
    assert guest.disk == 25 * 1024**3
    assert guest.uptime == 90_061


def test_vm_storage_capacity_excludes_node_and_backup_only_storage() -> None:
    storages = [
        StorageResponse(storage="local", node="pve-a", used=30, total=100),
        StorageResponse(storage="local-lvm", node="pve-a", used=70, total=200),
        StorageResponse(storage="ceph-vm", node="pve-a", shared=True, used=50, total=300),
        StorageResponse(storage="ceph-vm", node="pve-b", shared=True, used=50, total=300),
        StorageResponse(storage="pbs", node="pve-a", used=400, total=1_000),
    ]

    count, used, total = ClusterService._storage_capacity(
        storages,
        allowed_storage_ids={"local-lvm", "ceph-vm"},
    )

    assert count == 2
    assert used == 120
    assert total == 500
    assert ClusterService._stores_guest_disks("images,rootdir") is True
    assert ClusterService._stores_guest_disks("iso,vztmpl,backup") is False


async def test_cluster_list_only_queries_active_clusters(settings: Settings) -> None:
    session = AsyncMock(spec=AsyncSession)
    scalar_result = MagicMock()
    scalar_result.unique.return_value.all.return_value = []
    session.scalars.return_value = scalar_result
    principal = Principal(
        user_id=uuid4(),
        email="admin@example.test",
        role=UserRole.SUPER_ADMIN,
        session_epoch=0,
    )
    service = ClusterService(
        session=cast(AsyncSession, session),
        settings=settings,
        cipher=CredentialCipher(token_urlsafe(32)),
        principal=principal,
    )

    assert await service.list_clusters() == []

    statement = session.scalars.await_args.args[0]
    assert "clusters.is_active IS true" in str(statement)


class ClusterRemovalSafetyService(ClusterService):
    def __init__(
        self, *, cluster: Cluster, blocks: list[ClusterRemovalBlock], **kwargs: object
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.cluster = cluster
        self.blocks = blocks

    async def _get_cluster(self, _cluster_id: object) -> Cluster:
        return self.cluster

    async def _removal_blocks(self, _cluster_id: object) -> list[ClusterRemovalBlock]:
        return self.blocks


async def test_cluster_removal_is_blocked_when_an_assigned_workload_exists(
    settings: Settings,
) -> None:
    cluster_id = uuid4()
    cluster = Cluster(
        id=cluster_id,
        name="protected-cluster",
        api_base_url="https://pve.example.test:8006",
        is_active=True,
        version=1,
    )
    principal = Principal(
        user_id=uuid4(),
        email="admin@example.test",
        role=UserRole.SUPER_ADMIN,
        session_epoch=0,
    )
    service = ClusterRemovalSafetyService(
        cluster=cluster,
        blocks=[ClusterRemovalBlock(code="ASSIGNED_WORKLOADS", count=1)],
        session=cast(AsyncSession, object()),
        settings=settings,
        cipher=CredentialCipher(token_urlsafe(32)),
        principal=principal,
    )

    with pytest.raises(AppError) as error:
        await service.delete(cluster_id)

    assert error.value.code == "CLUSTER_REMOVAL_BLOCKED"
    assert error.value.status_code == 409
    assert error.value.details == {"blocks": [{"code": "ASSIGNED_WORKLOADS", "count": 1}]}
    assert cluster.is_active is True


async def test_cluster_removal_marks_projected_workloads_not_present(
    settings: Settings,
) -> None:
    cluster_id = uuid4()
    credential_id = uuid4()
    cluster = Cluster(
        id=cluster_id,
        name="retired-cluster",
        api_base_url="https://retired-pve.example.test:8006",
        is_active=True,
        version=1,
    )
    credential = ClusterCredential(
        id=credential_id,
        cluster_id=cluster_id,
        token_identifier="service@pve!retired",
        secret_ciphertext=b"ciphertext",
        secret_nonce=b"nonce",
        key_version="v1",
        is_active=True,
    )
    cluster.credentials.append(credential)
    session = AsyncMock(spec=AsyncSession)
    service = ClusterRemovalSafetyService(
        cluster=cluster,
        blocks=[],
        session=cast(AsyncSession, session),
        settings=settings,
        cipher=CredentialCipher(token_urlsafe(32)),
        principal=Principal(
            user_id=uuid4(),
            email="admin@example.test",
            role=UserRole.SUPER_ADMIN,
            session_epoch=0,
        ),
    )

    await service.delete(cluster_id)

    assert cluster.is_active is False
    assert credential.is_active is False
    statement = session.execute.await_args.args[0]
    assert "UPDATE workloads" in str(statement)
    assert "workloads.is_present IS true" in str(statement)
    session.commit.assert_awaited_once()


class ClusterUpdateAttackService(ClusterService):
    def __init__(self, *, cluster: Cluster, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.cluster = cluster
        self.probe_calls = 0

    async def _get_cluster(self, _cluster_id: object) -> Cluster:
        return self.cluster

    async def _probe(self, _connection: object) -> dict[str, object]:
        self.probe_calls += 1
        return {}


@pytest.mark.parametrize(
    "update",
    [
        ClusterUpdate(api_base_url="https://attacker.example.test:8006"),
        ClusterUpdate(ca_bundle_pem="attacker-controlled-ca"),
        ClusterUpdate(token_identifier="attacker@pve!capture"),
    ],
)
async def test_connection_identity_change_cannot_reuse_stored_token(
    settings: Settings,
    update: ClusterUpdate,
) -> None:
    application_key = token_urlsafe(32)
    cipher = CredentialCipher(application_key)
    cluster_id = uuid4()
    credential_id = uuid4()
    encrypted = cipher.encrypt(
        token_urlsafe(32), cluster_id=cluster_id, credential_id=credential_id
    )
    cluster = Cluster(
        id=cluster_id,
        name="existing-cluster",
        api_base_url="https://pve.example.test:8006",
        is_active=True,
        version=1,
    )
    cluster.credentials = [
        ClusterCredential(
            id=credential_id,
            cluster_id=cluster_id,
            token_identifier="service@pve!pvemaster",
            secret_ciphertext=encrypted.ciphertext,
            secret_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            is_active=True,
        )
    ]
    principal = Principal(
        user_id=uuid4(),
        email="operator@example.test",
        role=UserRole.OPERATOR,
        session_epoch=0,
    )
    service = ClusterUpdateAttackService(
        cluster=cluster,
        session=cast(AsyncSession, object()),
        settings=settings,
        cipher=cipher,
        principal=principal,
        transport=cast(
            httpx.AsyncBaseTransport,
            httpx.MockTransport(lambda _: httpx.Response(500)),
        ),
    )

    with pytest.raises(AppError) as error:
        await service.update(cluster_id, update)

    assert error.value.code == "PVE_CREDENTIAL_REENTRY_REQUIRED"
    assert service.probe_calls == 0
