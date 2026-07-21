import ipaddress
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.auth import UserRole
from app.models.cluster import Cluster
from app.models.ipam import (
    IpAddress,
    IpAddressState,
    IpAllocation,
    IpAllocationKind,
    IpAllocationStatus,
    IpPool,
    IpPoolExclusion,
)
from app.models.operation import Workload
from app.models.provisioning import ProvisioningRequest
from app.schemas.ipam import (
    IpAddressResponse,
    IpAllocationResponse,
    IpPoolCreate,
    IpPoolResponse,
    IpPoolUpdate,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address
IPAM_POOL_LOCK = 7_401_127


class IpamService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        principal: Principal,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._principal = principal
        self._request_id = request_id
        self._source_ip = source_ip
        require_service_role(principal, UserRole.SUPER_ADMIN)

    async def create_pool(self, payload: IpPoolCreate) -> IpPoolResponse:
        network = self._network(payload.cidr)
        gateway = self._address(payload.gateway) if payload.gateway is not None else None
        if gateway is not None and gateway not in network:
            raise self._invalid("GATEWAY_OUTSIDE_POOL", "The gateway must belong to the pool CIDR.")
        if payload.cluster_id is not None:
            cluster = await self._session.get(Cluster, payload.cluster_id)
            if cluster is None:
                raise AppError(
                    status_code=404,
                    code="CLUSTER_NOT_FOUND",
                    message="The cluster was not found.",
                )

        exclusions: list[tuple[Address, Address, str | None]] = []
        for item in payload.excluded_ranges:
            start, end = self._address(item.start), self._address(item.end)
            if start.version != network.version or end.version != network.version:
                raise self._invalid(
                    "IP_FAMILY_MISMATCH", "Excluded addresses must match the pool family."
                )
            if start not in network or end not in network or int(start) > int(end):
                raise self._invalid(
                    "INVALID_EXCLUDED_RANGE", "An excluded range is outside the pool."
                )
            exclusions.append((start, end, item.reason))

        dns = [self._address(value) for value in payload.dns_servers]
        if any(item.version != network.version for item in dns):
            raise self._invalid("IP_FAMILY_MISMATCH", "DNS addresses must match the pool family.")

        await self._session.execute(text(f"SELECT pg_advisory_xact_lock({IPAM_POOL_LOCK})"))
        existing_pools = await self._session.scalars(
            select(IpPool).where(IpPool.is_active.is_(True))
        )
        for existing in existing_pools:
            existing_network = self._network(str(existing.cidr))
            same_scope = (
                existing.cluster_id == payload.cluster_id
                or existing.cluster_id is None
                or payload.cluster_id is None
            )
            if same_scope and network.overlaps(existing_network):
                raise AppError(
                    status_code=409,
                    code="IP_POOL_OVERLAP",
                    message="The CIDR overlaps an existing pool in the same scope.",
                )

        pool = IpPool(
            id=uuid4(),
            name=payload.name.strip(),
            cluster_id=payload.cluster_id,
            cidr=str(network),
            gateway=str(gateway) if gateway is not None else None,
            dns_servers=[str(item) for item in dns],
            bridge=payload.bridge.strip(),
            vlan_tag=payload.vlan_tag,
            ip_family=network.version,
            allocation_strategy=payload.allocation_strategy,
            quarantine_seconds=payload.quarantine_seconds,
            next_offset=Decimal(0),
            is_active=True,
            created_by_id=self._principal.user_id,
            version=1,
        )
        self._session.add(pool)
        for start, end, reason in exclusions:
            self._session.add(
                IpPoolExclusion(
                    id=uuid4(),
                    pool_id=pool.id,
                    start_address=str(start),
                    end_address=str(end),
                    reason=reason,
                )
            )
        add_audit_event(
            self._session,
            action="IP_POOL_CREATE",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="ip_pool",
            target_id=pool.id,
            details={"cidr": str(network), "excluded_range_count": len(exclusions)},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="IP_POOL_CONFLICT",
                message="The IP pool already exists.",
            ) from exc
        return await self._pool_response(pool)

    async def list_pools(self) -> list[IpPoolResponse]:
        pools = await self._session.scalars(
            select(IpPool).where(IpPool.is_active.is_(True)).order_by(IpPool.name, IpPool.id)
        )
        return [await self._pool_response(pool) for pool in pools]

    async def get_pool(self, pool_id: UUID) -> IpPoolResponse:
        pool = await self._session.get(IpPool, pool_id)
        if pool is None:
            raise AppError(
                status_code=404,
                code="IP_POOL_NOT_FOUND",
                message="The IP pool was not found.",
            )
        return await self._pool_response(pool)

    async def update_pool(self, pool_id: UUID, payload: IpPoolUpdate) -> IpPoolResponse:
        pool = await self._locked_pool(pool_id)
        if pool.version != payload.version:
            raise AppError(
                status_code=409,
                code="IP_POOL_VERSION_CONFLICT",
                message="The IP pool was modified by another request.",
            )

        fields = payload.model_fields_set
        target_cluster_id = payload.cluster_id if "cluster_id" in fields else pool.cluster_id
        target_network = (
            self._network(payload.cidr)
            if payload.cidr is not None
            else self._network(str(pool.cidr))
        )
        if "gateway" in fields:
            target_gateway = self._address(payload.gateway) if payload.gateway is not None else None
        else:
            target_gateway = self._address(pool.gateway) if pool.gateway is not None else None
        target_dns = (
            [self._address(value) for value in payload.dns_servers or []]
            if "dns_servers" in fields
            else [self._address(value) for value in pool.dns_servers]
        )
        if target_gateway is not None and target_gateway not in target_network:
            raise self._invalid("GATEWAY_OUTSIDE_POOL", "The gateway must belong to the pool CIDR.")
        if any(item.version != target_network.version for item in target_dns):
            raise self._invalid("IP_FAMILY_MISMATCH", "DNS addresses must match the pool family.")
        if target_cluster_id is not None:
            cluster = await self._session.get(Cluster, target_cluster_id)
            if cluster is None:
                raise AppError(
                    status_code=404,
                    code="CLUSTER_NOT_FOUND",
                    message="The cluster was not found.",
                )

        current_network = self._network(str(pool.cidr))
        current_gateway = self._address(pool.gateway) if pool.gateway is not None else None
        addressing_changed = (
            target_network != current_network
            or target_cluster_id != pool.cluster_id
            or target_gateway != current_gateway
        )
        materialized_count = await self._session.scalar(
            select(func.count()).select_from(IpAddress).where(IpAddress.pool_id == pool.id)
        )
        if addressing_changed and (materialized_count or 0) > 0:
            raise AppError(
                status_code=409,
                code="IP_POOL_ADDRESSES_EXIST",
                message="CIDR, cluster, or gateway cannot change after addresses have been used.",
            )
        exclusion_count = await self._session.scalar(
            select(func.count())
            .select_from(IpPoolExclusion)
            .where(IpPoolExclusion.pool_id == pool.id)
        )
        if target_network != current_network and (exclusion_count or 0) > 0:
            raise AppError(
                status_code=409,
                code="IP_POOL_EXCLUSIONS_EXIST",
                message="CIDR cannot change while the pool has excluded address ranges.",
            )

        await self._session.execute(text(f"SELECT pg_advisory_xact_lock({IPAM_POOL_LOCK})"))
        existing_pools = await self._session.scalars(
            select(IpPool).where(IpPool.is_active.is_(True), IpPool.id != pool.id)
        )
        for existing in existing_pools:
            existing_network = self._network(str(existing.cidr))
            same_scope = (
                existing.cluster_id == target_cluster_id
                or existing.cluster_id is None
                or target_cluster_id is None
            )
            if same_scope and target_network.overlaps(existing_network):
                raise AppError(
                    status_code=409,
                    code="IP_POOL_OVERLAP",
                    message="The CIDR overlaps an existing pool in the same scope.",
                )

        before: dict[str, object] = {
            "name": pool.name,
            "cluster_id": str(pool.cluster_id) if pool.cluster_id else None,
            "cidr": str(current_network),
            "gateway": str(current_gateway) if current_gateway else None,
            "dns_servers": [str(item) for item in pool.dns_servers],
            "bridge": pool.bridge,
            "vlan_tag": pool.vlan_tag,
            "allocation_strategy": pool.allocation_strategy,
            "quarantine_seconds": pool.quarantine_seconds,
        }
        if payload.name is not None:
            pool.name = payload.name.strip()
        pool.cluster_id = target_cluster_id
        pool.cidr = str(target_network)
        pool.gateway = str(target_gateway) if target_gateway is not None else None
        pool.dns_servers = [str(item) for item in target_dns]
        if payload.bridge is not None:
            pool.bridge = payload.bridge.strip()
        if "vlan_tag" in fields:
            pool.vlan_tag = payload.vlan_tag
        if payload.allocation_strategy is not None:
            pool.allocation_strategy = payload.allocation_strategy
        if payload.quarantine_seconds is not None:
            pool.quarantine_seconds = payload.quarantine_seconds
        pool.ip_family = target_network.version
        pool.version += 1
        after: dict[str, object] = {
            "name": pool.name,
            "cluster_id": str(pool.cluster_id) if pool.cluster_id else None,
            "cidr": str(target_network),
            "gateway": str(target_gateway) if target_gateway else None,
            "dns_servers": [str(item) for item in target_dns],
            "bridge": pool.bridge,
            "vlan_tag": pool.vlan_tag,
            "allocation_strategy": pool.allocation_strategy,
            "quarantine_seconds": pool.quarantine_seconds,
        }
        add_audit_event(
            self._session,
            action="IP_POOL_UPDATE",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="ip_pool",
            target_id=pool.id,
            before=before,
            after=after,
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="IP_POOL_CONFLICT",
                message="The IP pool name or network conflicts with an existing pool.",
            ) from exc
        return await self._pool_response(pool)

    async def delete_pool(self, pool_id: UUID, *, version: int) -> None:
        pool = await self._locked_pool(pool_id)
        if pool.version != version:
            raise AppError(
                status_code=409,
                code="IP_POOL_VERSION_CONFLICT",
                message="The IP pool was modified by another request.",
            )
        unavailable_count = await self._session.scalar(
            select(func.count())
            .select_from(IpAddress)
            .where(
                IpAddress.pool_id == pool.id,
                IpAddress.state != IpAddressState.AVAILABLE.value,
            )
        )
        active_request_count = await self._session.scalar(
            select(func.count())
            .select_from(ProvisioningRequest)
            .where(
                ProvisioningRequest.ip_pool_id == pool.id,
                ProvisioningRequest.status.in_(["QUEUED", "RUNNING", "MANUAL_REVIEW"]),
            )
        )
        if (unavailable_count or 0) > 0 or (active_request_count or 0) > 0:
            raise AppError(
                status_code=409,
                code="IP_POOL_IN_USE",
                message=(
                    "Release reserved, assigned, or quarantined addresses before deleting the pool."
                ),
                details={
                    "unavailable_addresses": int(unavailable_count or 0),
                    "active_provisioning_requests": int(active_request_count or 0),
                },
            )
        pool.is_active = False
        pool.version += 1
        add_audit_event(
            self._session,
            action="IP_POOL_DELETE",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="ip_pool",
            target_id=pool.id,
            before={"name": pool.name, "cidr": str(pool.cidr), "is_active": True},
            after={"is_active": False},
        )
        await self._session.commit()

    async def reserve_address(
        self, pool_id: UUID, address: object, reason: str
    ) -> IpAddressResponse:
        pool = await self._locked_pool(pool_id)
        candidate = self._validated_candidate(pool, self._address(address), allow_special=False)
        existing = await self._session.scalar(
            select(IpAddress)
            .where(IpAddress.pool_id == pool.id, IpAddress.address == str(candidate))
            .with_for_update()
        )
        if existing is not None and existing.state != IpAddressState.AVAILABLE.value:
            raise AppError(
                status_code=409,
                code="IP_ADDRESS_UNAVAILABLE",
                message="The IP address is not available.",
            )
        item = existing or IpAddress(id=uuid4(), pool_id=pool.id, address=str(candidate), version=1)
        item.state = IpAddressState.DISABLED.value
        item.reserved_for = reason
        item.quarantined_until = None
        self._session.add(item)
        await self._audit_and_commit("IP_ADDRESS_EXCLUDE", "ip_address", item.id, pool=pool)
        return self._address_response(item, None)

    async def allocate(
        self, pool_id: UUID, workload_id: UUID, requested_address: object | None
    ) -> IpAllocationResponse:
        pool = await self._locked_pool(pool_id)
        workload = await self._session.scalar(
            select(Workload)
            .where(Workload.id == workload_id, Workload.is_present.is_(True))
            .with_for_update()
        )
        if workload is None:
            raise AppError(
                status_code=404,
                code="WORKLOAD_NOT_FOUND",
                message="The workload was not found.",
            )
        if pool.cluster_id is not None and workload.cluster_id != pool.cluster_id:
            raise AppError(
                status_code=409,
                code="IP_POOL_CLUSTER_MISMATCH",
                message="The workload is in another cluster.",
            )

        kind = (
            IpAllocationKind.MANUAL if requested_address is not None else IpAllocationKind.AUTOMATIC
        )
        if requested_address is not None:
            candidate = self._validated_candidate(
                pool, self._address(requested_address), allow_special=False
            )
            if await self._is_excluded(pool.id, candidate):
                raise AppError(
                    status_code=409,
                    code="IP_ADDRESS_EXCLUDED",
                    message="The IP address is excluded.",
                )
            address = await self._session.scalar(
                select(IpAddress)
                .where(IpAddress.pool_id == pool.id, IpAddress.address == str(candidate))
                .with_for_update()
            )
            if address is None:
                address = IpAddress(
                    id=uuid4(),
                    pool_id=pool.id,
                    address=str(candidate),
                    state=IpAddressState.AVAILABLE.value,
                    version=1,
                )
                self._session.add(address)
                await self._session.flush()
        else:
            address = await self._automatic_candidate(pool)

        if address.state != IpAddressState.AVAILABLE.value:
            raise AppError(
                status_code=409,
                code="IP_ADDRESS_UNAVAILABLE",
                message="The IP address is not available.",
            )
        now = datetime.now(UTC)
        address.state = IpAddressState.ASSIGNED.value
        address.last_allocated_at = now
        address.quarantined_until = None
        address.reserved_for = None
        address.version += 1
        allocation = IpAllocation(
            id=uuid4(),
            ip_address_id=address.id,
            workload_id=workload.id,
            kind=kind.value,
            status=IpAllocationStatus.ASSIGNED.value,
            allocated_by_id=self._principal.user_id,
            confirmed_at=now,
            version=1,
        )
        self._session.add(allocation)
        add_audit_event(
            self._session,
            action="IP_ALLOCATE",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            workload_id=workload.id,
            source_ip=self._source_ip,
            target_type="ip_allocation",
            target_id=allocation.id,
            details={"pool_id": str(pool.id), "kind": kind.value},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="IP_ADDRESS_UNAVAILABLE",
                message="The IP address is not available.",
            ) from exc
        return self._allocation_response(allocation, address, pool)

    async def reserve_for_provisioning(
        self,
        pool_id: UUID,
        provisioning_request_id: UUID,
        requested_address: object | None,
    ) -> tuple[IpAddress, IpAllocation]:
        pool = await self._locked_pool(pool_id)
        existing = await self._session.scalar(
            select(IpAllocation).where(
                IpAllocation.provisioning_request_id == provisioning_request_id,
                IpAllocation.status.in_(
                    [IpAllocationStatus.RESERVED.value, IpAllocationStatus.ASSIGNED.value]
                ),
            )
        )
        if existing is not None:
            address = await self._session.get(IpAddress, existing.ip_address_id)
            assert address is not None
            return address, existing

        kind = (
            IpAllocationKind.MANUAL if requested_address is not None else IpAllocationKind.AUTOMATIC
        )
        if requested_address is not None:
            candidate = self._validated_candidate(
                pool, self._address(requested_address), allow_special=False
            )
            if await self._is_excluded(pool.id, candidate):
                raise AppError(
                    status_code=409,
                    code="IP_ADDRESS_EXCLUDED",
                    message="The IP address is excluded.",
                )
            address = await self._session.scalar(
                select(IpAddress)
                .where(IpAddress.pool_id == pool.id, IpAddress.address == str(candidate))
                .with_for_update()
            )
            if address is None:
                address = IpAddress(
                    id=uuid4(),
                    pool_id=pool.id,
                    address=str(candidate),
                    state=IpAddressState.AVAILABLE.value,
                    version=1,
                )
                self._session.add(address)
                await self._session.flush()
        else:
            address = await self._automatic_candidate(pool)
        if address.state != IpAddressState.AVAILABLE.value:
            raise AppError(
                status_code=409,
                code="IP_ADDRESS_UNAVAILABLE",
                message="The IP address is not available.",
            )
        address.state = IpAddressState.RESERVED.value
        address.last_allocated_at = datetime.now(UTC)
        address.version += 1
        allocation = IpAllocation(
            id=uuid4(),
            ip_address_id=address.id,
            workload_id=None,
            provisioning_request_id=provisioning_request_id,
            kind=kind.value,
            status=IpAllocationStatus.RESERVED.value,
            allocated_by_id=self._principal.user_id,
            version=1,
        )
        self._session.add(allocation)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="IP_ADDRESS_UNAVAILABLE",
                message="The IP address is not available.",
            ) from exc
        return address, allocation

    async def confirm_provisioning_ip(
        self, provisioning_request_id: UUID, workload_id: UUID
    ) -> None:
        allocation = await self._session.scalar(
            select(IpAllocation)
            .where(
                IpAllocation.provisioning_request_id == provisioning_request_id,
                IpAllocation.status == IpAllocationStatus.RESERVED.value,
            )
            .with_for_update()
        )
        if allocation is None:
            already = await self._session.scalar(
                select(IpAllocation.id).where(
                    IpAllocation.provisioning_request_id == provisioning_request_id,
                    IpAllocation.status == IpAllocationStatus.ASSIGNED.value,
                )
            )
            if already is not None:
                return
            raise AppError(
                status_code=409,
                code="IP_RESERVATION_MISSING",
                message="The provisioning IP reservation is missing.",
            )
        address = await self._session.get(IpAddress, allocation.ip_address_id)
        assert address is not None
        allocation.workload_id = workload_id
        allocation.status = IpAllocationStatus.ASSIGNED.value
        allocation.confirmed_at = datetime.now(UTC)
        allocation.version += 1
        address.state = IpAddressState.ASSIGNED.value
        address.version += 1
        await self._session.commit()

    async def quarantine_provisioning_ip(self, provisioning_request_id: UUID) -> None:
        allocation = await self._session.scalar(
            select(IpAllocation)
            .where(
                IpAllocation.provisioning_request_id == provisioning_request_id,
                IpAllocation.status == IpAllocationStatus.RESERVED.value,
            )
            .with_for_update()
        )
        if allocation is None:
            return
        address = await self._session.get(IpAddress, allocation.ip_address_id)
        assert address is not None
        pool = await self._session.get(IpPool, address.pool_id)
        assert pool is not None
        now = datetime.now(UTC)
        allocation.status = IpAllocationStatus.QUARANTINED.value
        allocation.released_at = now
        allocation.release_reason = "Provisioning failed before clone completion"
        allocation.version += 1
        address.state = IpAddressState.QUARANTINED.value
        address.quarantined_until = now + timedelta(seconds=pool.quarantine_seconds)
        address.version += 1
        await self._session.commit()

    async def release(self, allocation_id: UUID, reason: str) -> IpAllocationResponse:
        allocation = await self._session.scalar(
            select(IpAllocation).where(IpAllocation.id == allocation_id).with_for_update()
        )
        if allocation is None:
            raise AppError(
                status_code=404,
                code="IP_ALLOCATION_NOT_FOUND",
                message="The IP allocation was not found.",
            )
        address = await self._session.scalar(
            select(IpAddress).where(IpAddress.id == allocation.ip_address_id).with_for_update()
        )
        assert address is not None
        pool = await self._session.get(IpPool, address.pool_id)
        assert pool is not None
        if allocation.status == IpAllocationStatus.RELEASED.value:
            return self._allocation_response(allocation, address, pool)
        if allocation.status != IpAllocationStatus.QUARANTINED.value:
            now = datetime.now(UTC)
            allocation.status = IpAllocationStatus.QUARANTINED.value
            allocation.released_at = now
            allocation.release_reason = reason
            allocation.version += 1
            address.state = IpAddressState.QUARANTINED.value
            address.quarantined_until = now + timedelta(seconds=pool.quarantine_seconds)
            address.version += 1
            await self._audit_and_commit(
                "IP_RELEASE_TO_QUARANTINE", "ip_allocation", allocation.id, pool=pool
            )
        return self._allocation_response(allocation, address, pool)

    async def approve_release(self, address_id: UUID) -> IpAddressResponse:
        address = await self._session.scalar(
            select(IpAddress).where(IpAddress.id == address_id).with_for_update()
        )
        if address is None:
            raise AppError(
                status_code=404,
                code="IP_ADDRESS_NOT_FOUND",
                message="The IP address was not found.",
            )
        if address.state != IpAddressState.QUARANTINED.value:
            raise AppError(
                status_code=409,
                code="IP_ADDRESS_NOT_QUARANTINED",
                message="The address is not quarantined.",
            )
        now = datetime.now(UTC)
        if address.quarantined_until is not None and address.quarantined_until > now:
            raise AppError(
                status_code=409,
                code="IP_QUARANTINE_ACTIVE",
                message="The quarantine period has not elapsed.",
            )
        allocation = await self._session.scalar(
            select(IpAllocation)
            .where(
                IpAllocation.ip_address_id == address.id,
                IpAllocation.status == IpAllocationStatus.QUARANTINED.value,
            )
            .with_for_update()
        )
        if allocation is None:
            raise AppError(
                status_code=409,
                code="IP_ALLOCATION_STATE_INVALID",
                message="No quarantined allocation exists.",
            )
        allocation.status = IpAllocationStatus.RELEASED.value
        allocation.version += 1
        address.state = IpAddressState.AVAILABLE.value
        address.quarantined_until = None
        address.version += 1
        pool = await self._session.get(IpPool, address.pool_id)
        assert pool is not None
        await self._audit_and_commit("IP_RELEASE_APPROVE", "ip_address", address.id, pool=pool)
        return self._address_response(address, None)

    async def list_addresses(self, pool_id: UUID) -> list[IpAddressResponse]:
        await self.get_pool(pool_id)
        rows = await self._session.execute(
            select(IpAddress, IpAllocation.workload_id)
            .outerjoin(
                IpAllocation,
                (IpAllocation.ip_address_id == IpAddress.id)
                & IpAllocation.status.in_(
                    [
                        IpAllocationStatus.RESERVED.value,
                        IpAllocationStatus.ASSIGNED.value,
                        IpAllocationStatus.QUARANTINED.value,
                    ]
                ),
            )
            .where(IpAddress.pool_id == pool_id)
            .order_by(IpAddress.address)
        )
        return [self._address_response(address, workload_id) for address, workload_id in rows]

    async def _automatic_candidate(self, pool: IpPool) -> IpAddress:
        available = await self._session.scalar(
            select(IpAddress)
            .where(
                IpAddress.pool_id == pool.id,
                IpAddress.state == IpAddressState.AVAILABLE.value,
            )
            .order_by(IpAddress.address)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if available is not None:
            return available

        network = self._network(str(pool.cidr))
        exclusions = await self._exclusion_bounds(pool.id)
        materialized = {
            int(self._address(item))
            for item in await self._session.scalars(
                select(IpAddress.address).where(IpAddress.pool_id == pool.id)
            )
        }
        base = int(network.network_address)
        blocked = [
            (max(0, low - base), min(network.num_addresses - 1, high - base))
            for low, high in exclusions
        ]
        blocked.extend((value - base, value - base) for value in materialized)
        blocked.append((0, 0))
        if isinstance(network, ipaddress.IPv4Network):
            blocked.append((network.num_addresses - 1, network.num_addresses - 1))
        if pool.gateway is not None:
            gateway_offset = int(self._address(pool.gateway)) - base
            blocked.append((gateway_offset, gateway_offset))

        start = int(pool.next_offset)
        offset: int | None = None
        if pool.allocation_strategy == "RANDOM" and network.num_addresses > 2:
            generator = random.SystemRandom()
            for _ in range(1024):
                random_offset = generator.randrange(network.num_addresses)
                if not any(low <= random_offset <= high for low, high in blocked):
                    offset = random_offset
                    break
        offset = (
            offset
            if offset is not None
            else self._first_free_offset(network.num_addresses, start, blocked)
        )
        if offset is None:
            raise AppError(
                status_code=409,
                code="IP_POOL_EXHAUSTED",
                message="No allocatable IP address remains in the pool.",
            )
        candidate = network.network_address + offset
        pool.next_offset = Decimal((offset + 1) % network.num_addresses)
        address = IpAddress(
            id=uuid4(),
            pool_id=pool.id,
            address=str(candidate),
            state=IpAddressState.AVAILABLE.value,
            version=1,
        )
        self._session.add(address)
        await self._session.flush()
        return address

    @staticmethod
    def _first_free_offset(size: int, start: int, blocked: list[tuple[int, int]]) -> int | None:
        merged: list[tuple[int, int]] = []
        for low, high in sorted(blocked):
            if high < 0 or low >= size:
                continue
            low, high = max(0, low), min(size - 1, high)
            if merged and low <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], high))
            else:
                merged.append((low, high))

        for range_start, range_end in ((start, size - 1), (0, start - 1)):
            cursor = range_start
            if cursor > range_end:
                continue
            for low, high in merged:
                if high < cursor:
                    continue
                if low > range_end:
                    break
                if low > cursor:
                    return cursor
                cursor = max(cursor, high + 1)
                if cursor > range_end:
                    break
            if cursor <= range_end:
                return cursor
        return None

    async def _locked_pool(self, pool_id: UUID) -> IpPool:
        pool = await self._session.scalar(
            select(IpPool).where(IpPool.id == pool_id).with_for_update()
        )
        if pool is None or not pool.is_active:
            raise AppError(
                status_code=404,
                code="IP_POOL_NOT_FOUND",
                message="The IP pool was not found.",
            )
        return pool

    async def _is_excluded(self, pool_id: UUID, address: Address) -> bool:
        bounds = await self._exclusion_bounds(pool_id)
        value = int(address)
        return any(low <= value <= high for low, high in bounds)

    async def _exclusion_bounds(self, pool_id: UUID) -> list[tuple[int, int]]:
        rows = await self._session.execute(
            select(IpPoolExclusion.start_address, IpPoolExclusion.end_address).where(
                IpPoolExclusion.pool_id == pool_id
            )
        )
        return [(int(self._address(start)), int(self._address(end))) for start, end in rows]

    def _validated_candidate(
        self, pool: IpPool, candidate: Address, *, allow_special: bool
    ) -> Address:
        network = self._network(str(pool.cidr))
        if candidate.version != network.version or candidate not in network:
            raise self._invalid("IP_OUTSIDE_POOL", "The IP address is outside the pool CIDR.")
        if not allow_special and self._special(pool, network, candidate):
            raise AppError(
                status_code=409,
                code="IP_ADDRESS_EXCLUDED",
                message="The IP address is reserved by the network.",
            )
        return candidate

    def _special(self, pool: IpPool, network: Network, candidate: Address) -> bool:
        if candidate == network.network_address:
            return True
        if isinstance(network, ipaddress.IPv4Network) and candidate == network.broadcast_address:
            return True
        return pool.gateway is not None and candidate == self._address(pool.gateway)

    async def _pool_response(self, pool: IpPool) -> IpPoolResponse:
        counts = await self._session.execute(
            select(IpAddress.state, func.count(IpAddress.id))
            .where(IpAddress.pool_id == pool.id)
            .group_by(IpAddress.state)
        )
        by_state = {state: count for state, count in counts}
        network = self._network(str(pool.cidr))
        return IpPoolResponse(
            id=pool.id,
            name=pool.name,
            cluster_id=pool.cluster_id,
            cidr=str(network),
            prefix_length=network.prefixlen,
            gateway=str(pool.gateway) if pool.gateway is not None else None,
            dns_servers=[str(item) for item in pool.dns_servers],
            bridge=pool.bridge,
            vlan_tag=pool.vlan_tag,
            ip_family=pool.ip_family,
            allocation_strategy=pool.allocation_strategy,
            quarantine_seconds=pool.quarantine_seconds,
            is_active=pool.is_active,
            allocated_count=int(by_state.get(IpAddressState.ASSIGNED.value, 0)),
            quarantined_count=int(by_state.get(IpAddressState.QUARANTINED.value, 0)),
            availability_status="AVAILABLE" if pool.is_active else "DISABLED",
            version=pool.version,
        )

    @staticmethod
    def _address_response(address: IpAddress, workload_id: UUID | None) -> IpAddressResponse:
        return IpAddressResponse(
            id=address.id,
            pool_id=address.pool_id,
            address=str(address.address),
            state=IpAddressState(address.state),
            reserved_for=address.reserved_for,
            quarantined_until=address.quarantined_until,
            workload_id=workload_id,
        )

    @staticmethod
    def _allocation_response(
        allocation: IpAllocation, address: IpAddress, pool: IpPool
    ) -> IpAllocationResponse:
        assert allocation.workload_id is not None
        return IpAllocationResponse(
            id=allocation.id,
            pool_id=pool.id,
            ip_address_id=address.id,
            address=str(address.address),
            workload_id=allocation.workload_id,
            kind=IpAllocationKind(allocation.kind),
            status=IpAllocationStatus(allocation.status),
            allocated_at=allocation.allocated_at,
            released_at=allocation.released_at,
            quarantined_until=address.quarantined_until,
        )

    async def _audit_and_commit(
        self, action: str, target_type: str, target_id: UUID, *, pool: IpPool
    ) -> None:
        add_audit_event(
            self._session,
            action=action,
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type=target_type,
            target_id=target_id,
            details={"pool_id": str(pool.id)},
        )
        await self._session.commit()

    @staticmethod
    def _network(value: str) -> Network:
        try:
            return ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise IpamService._invalid("INVALID_CIDR", "The CIDR is invalid.") from exc

    @staticmethod
    def _address(value: object) -> Address:
        try:
            return ipaddress.ip_address(str(value).split("/")[0])
        except ValueError as exc:
            raise IpamService._invalid("INVALID_IP_ADDRESS", "The IP address is invalid.") from exc

    @staticmethod
    def _invalid(code: str, message: str) -> AppError:
        return AppError(status_code=422, code=code, message=message)
