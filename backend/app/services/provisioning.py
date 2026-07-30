import hmac
import ipaddress
import json
import logging
from collections.abc import Callable
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import Organization, UserRole
from app.models.cluster import Cluster
from app.models.ipam import IpAddress, IpPool
from app.models.operation import Workload
from app.models.provisioning import (
    Product,
    ProvisioningNode,
    ProvisioningRequest,
    ProvisioningStatus,
    ProvisioningStep,
    ProvisioningStepStatus,
    Template,
)
from app.schemas.provisioning import (
    ProductCreate,
    ProductResponse,
    ProductUpdate,
    ProvisioningNodeResponse,
    ProvisioningNodeUpsert,
    ProvisioningRequestCreate,
    ProvisioningRequestResponse,
    ProvisioningStepResponse,
    TemplateCreate,
    TemplateResponse,
    TemplateUpdate,
)
from app.security.access import Principal, require_service_role
from app.services.audit import add_audit_event
from app.services.quota import reserve_quota

ProvisioningPublisher = Callable[[UUID, str], None]
logger = logging.getLogger(__name__)

PROVISIONING_STEPS = (
    "VALIDATE_REQUEST",
    "CHECK_PRODUCT",
    "SELECT_TARGET",
    "RESERVE_VMID",
    "RESERVE_IP",
    "FULL_CLONE",
    "WAIT_CLONE",
    "CONFIGURE_COMPUTE",
    "RESIZE_DISK",
    "CONFIGURE_NETWORK",
    "CONFIGURE_IDENTITY",
    "START_VM",
    "VERIFY_STATUS",
    "ASSIGN_ORGANIZATION",
    "CONFIRM_IP",
)


class ProvisioningService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        principal: Principal,
        publisher: ProvisioningPublisher,
        request_id: str,
        source_ip: str,
    ) -> None:
        self._session = session
        self._settings = settings
        self._principal = principal
        self._publisher = publisher
        self._request_id = request_id
        self._source_ip = source_ip
        require_service_role(principal, UserRole.SUPER_ADMIN)

    async def create_product(self, payload: ProductCreate) -> ProductResponse:
        product = Product(
            id=uuid4(),
            name=payload.name.strip(),
            cpu_cores=payload.cpu_cores,
            memory_bytes=payload.memory_bytes,
            disk_bytes=payload.disk_bytes,
            is_enabled=True,
            created_by_id=self._principal.user_id,
        )
        self._session.add(product)
        await self._commit_or_conflict("PRODUCT_CONFLICT", "The product already exists.")
        return self._product_response(product)

    async def list_products(self) -> list[ProductResponse]:
        items = await self._session.scalars(select(Product).order_by(Product.name))
        return [self._product_response(item) for item in items]

    async def update_product(self, product_id: UUID, payload: ProductUpdate) -> ProductResponse:
        product = await self._session.get(Product, product_id)
        if product is None:
            raise AppError(404, "PRODUCT_NOT_FOUND", "The product was not found.")
        before = self._product_audit(product)
        if payload.name is not None:
            product.name = payload.name.strip()
        if payload.cpu_cores is not None:
            product.cpu_cores = payload.cpu_cores
        if payload.memory_bytes is not None:
            product.memory_bytes = payload.memory_bytes
        if payload.disk_bytes is not None:
            product.disk_bytes = payload.disk_bytes
        if payload.is_enabled is not None:
            product.is_enabled = payload.is_enabled
        add_audit_event(
            self._session,
            action="PRODUCT_UPDATED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="product",
            target_id=product.id,
            before=before,
            after=self._product_audit(product),
        )
        await self._commit_or_conflict("PRODUCT_CONFLICT", "The product already exists.")
        return self._product_response(product)

    async def delete_product(self, product_id: UUID) -> None:
        product = await self._session.get(Product, product_id)
        if product is None:
            raise AppError(404, "PRODUCT_NOT_FOUND", "The product was not found.")
        add_audit_event(
            self._session,
            action="PRODUCT_DELETED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="product",
            target_id=product.id,
            before=self._product_audit(product),
        )
        await self._session.delete(product)
        await self._commit_or_conflict(
            "PRODUCT_IN_USE", "The product is referenced by provisioning history."
        )

    async def create_template(self, payload: TemplateCreate) -> TemplateResponse:
        source = await self._template_source(payload.source_workload_id)
        template = Template(
            id=uuid4(),
            name=payload.name.strip(),
            source_workload_id=source.id,
            source_disk=payload.source_disk,
            default_storage=payload.default_storage,
            default_bridge=payload.default_bridge,
            default_vlan_tag=payload.default_vlan_tag,
            cloud_init_enabled=True,
            linux_only=True,
            is_enabled=True,
            created_by_id=self._principal.user_id,
        )
        self._session.add(template)
        await self._commit_or_conflict("TEMPLATE_CONFLICT", "The template already exists.")
        return self._template_response(template)

    async def list_templates(self) -> list[TemplateResponse]:
        items = await self._session.scalars(select(Template).order_by(Template.name))
        return [self._template_response(item) for item in items]

    async def update_template(self, template_id: UUID, payload: TemplateUpdate) -> TemplateResponse:
        template = await self._session.get(Template, template_id)
        if template is None:
            raise AppError(404, "TEMPLATE_NOT_FOUND", "The template was not found.")
        before = self._template_audit(template)
        if payload.source_workload_id is not None:
            source = await self._template_source(payload.source_workload_id)
            template.source_workload_id = source.id
        if payload.name is not None:
            template.name = payload.name.strip()
        if payload.source_disk is not None:
            template.source_disk = payload.source_disk
        if payload.default_storage is not None:
            template.default_storage = payload.default_storage
        if payload.default_bridge is not None:
            template.default_bridge = payload.default_bridge
        if "default_vlan_tag" in payload.model_fields_set:
            template.default_vlan_tag = payload.default_vlan_tag
        if payload.is_enabled is not None:
            template.is_enabled = payload.is_enabled
        add_audit_event(
            self._session,
            action="TEMPLATE_UPDATED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="template",
            target_id=template.id,
            before=before,
            after=self._template_audit(template),
        )
        await self._commit_or_conflict("TEMPLATE_CONFLICT", "The template already exists.")
        return self._template_response(template)

    async def delete_template(self, template_id: UUID) -> None:
        template = await self._session.get(Template, template_id)
        if template is None:
            raise AppError(404, "TEMPLATE_NOT_FOUND", "The template was not found.")
        add_audit_event(
            self._session,
            action="TEMPLATE_DELETED",
            outcome="SUCCEEDED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            source_ip=self._source_ip,
            target_type="template",
            target_id=template.id,
            before=self._template_audit(template),
        )
        await self._session.delete(template)
        await self._commit_or_conflict(
            "TEMPLATE_IN_USE", "The template is referenced by provisioning history."
        )

    async def upsert_node(self, payload: ProvisioningNodeUpsert) -> ProvisioningNodeResponse:
        cluster = await self._session.get(Cluster, payload.cluster_id)
        if cluster is None or not cluster.is_active:
            raise AppError(404, "CLUSTER_NOT_FOUND", "The cluster was not found.")
        node = await self._session.scalar(
            select(ProvisioningNode).where(
                ProvisioningNode.cluster_id == payload.cluster_id,
                ProvisioningNode.name == payload.name,
            )
        )
        if node is None:
            node = ProvisioningNode(id=uuid4(), cluster_id=payload.cluster_id, name=payload.name)
            self._session.add(node)
        node.is_enabled = payload.is_enabled
        node.is_maintenance = payload.is_maintenance
        node.available_memory_bytes = payload.available_memory_bytes
        node.available_storage_bytes = payload.available_storage_bytes
        await self._session.commit()
        return self._node_response(node)

    async def list_nodes(self) -> list[ProvisioningNodeResponse]:
        nodes = await self._session.scalars(
            select(ProvisioningNode).order_by(ProvisioningNode.cluster_id, ProvisioningNode.name)
        )
        return [self._node_response(node) for node in nodes]

    async def create_request(
        self, payload: ProvisioningRequestCreate, idempotency_key: str
    ) -> tuple[ProvisioningRequestResponse, bool]:
        key_hash = self._key_hash(idempotency_key)
        fingerprint = sha256(
            json.dumps(
                payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).digest()
        existing = await self._session.scalar(
            select(ProvisioningRequest).where(
                ProvisioningRequest.requested_by_id == self._principal.user_id,
                ProvisioningRequest.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                raise AppError(
                    status_code=409,
                    code="IDEMPOTENCY_KEY_REUSED",
                    message="The idempotency key was already used for another request.",
                )
            return await self._request_response(existing), False

        product, template, source, pool = await self._validate_references(payload)
        if payload.target_node_id is not None:
            await self._validate_node(payload.target_node_id, payload.target_cluster_id, product)
        task_id = str(uuid4())
        request = ProvisioningRequest(
            id=uuid4(),
            requested_by_id=self._principal.user_id,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            product_id=product.id,
            template_id=template.id,
            organization_id=payload.organization_id,
            target_cluster_id=payload.target_cluster_id,
            target_node_id=payload.target_node_id,
            target_vmid=payload.target_vmid,
            target_name=payload.target_name.lower(),
            ip_pool_id=pool.id,
            requested_ip_address=str(payload.ip_address) if payload.ip_address else None,
            status=ProvisioningStatus.QUEUED.value,
            current_step=PROVISIONING_STEPS[0],
            spec_snapshot={
                "cpu_cores": product.cpu_cores,
                "memory_bytes": product.memory_bytes,
                "disk_bytes": product.disk_bytes,
                "storage": template.default_storage,
                "bridge": template.default_bridge,
                "vlan_tag": template.default_vlan_tag,
                "cloud_init_username": payload.cloud_init.username,
                "ssh_public_keys": payload.cloud_init.ssh_public_keys,
                "start_after_create": payload.start_after_create,
                "source_node": source.node,
                "source_vmid": source.vmid,
                "source_disk": template.source_disk,
            },
            celery_task_id=task_id,
            clone_submitted=False,
            version=1,
        )
        self._session.add(request)
        try:
            await self._session.flush([request])
            await reserve_quota(
                self._session,
                payload.organization_id,
                provisioning_request_id=request.id,
                vcpu=product.cpu_cores,
                memory_bytes=product.memory_bytes,
                disk_bytes=product.disk_bytes,
                vms=1,
                ips=1,
            )
        except AppError:
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="PROVISIONING_REQUEST_CONFLICT",
                message="The provisioning request conflicts with an existing reservation.",
            ) from exc
        self._session.add_all(
            [
                ProvisioningStep(
                    id=uuid4(),
                    provisioning_request_id=request.id,
                    step_order=index,
                    step_name=name,
                    status=ProvisioningStepStatus.PENDING.value,
                    attempt_count=0,
                    safe_result={},
                )
                for index, name in enumerate(PROVISIONING_STEPS, start=1)
            ]
        )
        add_audit_event(
            self._session,
            action="VM_PROVISION_REQUEST",
            outcome="ATTEMPTED",
            request_id=self._request_id,
            actor_user_id=self._principal.user_id,
            actor_role=self._principal.role,
            organization_id=payload.organization_id,
            source_ip=self._source_ip,
            target_type="provisioning_request",
            target_id=request.id,
            details={"product_id": str(product.id), "template_id": str(template.id)},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(
                status_code=409,
                code="PROVISIONING_REQUEST_CONFLICT",
                message="The provisioning request conflicts with an existing reservation.",
            ) from exc
        try:
            self._publisher(request.id, task_id)
        except Exception:
            logger.exception(
                "Provisioning enqueue failed; worker recovery will retry",
                extra={"provisioning_request_id": str(request.id)},
            )
        return await self._request_response(request), True

    async def get_request(self, request_id: UUID) -> ProvisioningRequestResponse:
        request = await self._session.get(ProvisioningRequest, request_id)
        if request is None:
            raise AppError(404, "PROVISIONING_REQUEST_NOT_FOUND", "The request was not found.")
        return await self._request_response(request)

    async def list_requests(self) -> list[ProvisioningRequestResponse]:
        items = await self._session.scalars(
            select(ProvisioningRequest).order_by(ProvisioningRequest.requested_at.desc())
        )
        return [await self._request_response(item) for item in items]

    async def _validate_references(
        self, payload: ProvisioningRequestCreate
    ) -> tuple[Product, Template, Workload, IpPool]:
        product = await self._session.get(Product, payload.product_id)
        template = await self._session.get(Template, payload.template_id)
        organization = await self._session.get(Organization, payload.organization_id)
        cluster = await self._session.get(Cluster, payload.target_cluster_id)
        pool = await self._session.get(IpPool, payload.ip_pool_id)
        source = (
            await self._session.get(Workload, template.source_workload_id)
            if template is not None
            else None
        )
        if product is None or not product.is_enabled:
            raise AppError(422, "PRODUCT_UNAVAILABLE", "The product is unavailable.")
        if (
            template is None
            or not template.is_enabled
            or not template.cloud_init_enabled
            or not template.linux_only
            or source is None
            or source.kind != "QEMU"
            or not source.is_template
            or not source.is_present
        ):
            raise AppError(422, "TEMPLATE_UNAVAILABLE", "The Linux QEMU template is unavailable.")
        if cluster is None or not cluster.is_active or source.cluster_id != cluster.id:
            raise AppError(422, "CLUSTER_INCOMPATIBLE", "The target cluster is incompatible.")
        if organization is None or not organization.is_active:
            raise AppError(422, "ORGANIZATION_UNAVAILABLE", "The organization is unavailable.")
        if (
            pool is None
            or not pool.is_active
            or pool.ip_family != 4
            or (pool.cluster_id is not None and pool.cluster_id != cluster.id)
        ):
            raise AppError(422, "IP_POOL_INCOMPATIBLE", "A compatible IPv4 pool is required.")
        if payload.ip_address is not None and payload.ip_address not in ipaddress.ip_network(
            str(pool.cidr), strict=False
        ):
            raise AppError(
                422, "IP_OUTSIDE_POOL", "The requested IPv4 address is outside the pool."
            )
        return product, template, source, pool

    async def _validate_node(
        self, node_id: UUID, cluster_id: UUID, product: Product
    ) -> ProvisioningNode:
        node = await self._session.get(ProvisioningNode, node_id)
        if (
            node is None
            or node.cluster_id != cluster_id
            or not node.is_enabled
            or node.is_maintenance
            or node.available_memory_bytes < product.memory_bytes
            or node.available_storage_bytes < product.disk_bytes
        ):
            raise AppError(422, "NO_ELIGIBLE_NODE", "No eligible node has sufficient capacity.")
        return node

    async def _request_response(self, request: ProvisioningRequest) -> ProvisioningRequestResponse:
        steps = await self._session.scalars(
            select(ProvisioningStep)
            .where(ProvisioningStep.provisioning_request_id == request.id)
            .order_by(ProvisioningStep.step_order)
        )
        address = (
            await self._session.get(IpAddress, request.ip_address_id)
            if request.ip_address_id is not None
            else None
        )
        return ProvisioningRequestResponse(
            id=request.id,
            job_id=request.id,
            status=ProvisioningStatus(request.status),
            current_step=request.current_step,
            product_id=request.product_id,
            template_id=request.template_id,
            organization_id=request.organization_id,
            target_cluster_id=request.target_cluster_id,
            target_node_id=request.target_node_id,
            target_vmid=request.target_vmid,
            target_name=request.target_name,
            ip_pool_id=request.ip_pool_id,
            ip_address=str(address.address) if address is not None else None,
            workload_id=request.workload_id,
            error_code=request.error_code,
            error_summary=request.error_summary,
            requested_at=request.requested_at,
            started_at=request.started_at,
            finished_at=request.finished_at,
            steps=[self._step_response(step) for step in steps],
        )

    async def _commit_or_conflict(self, code: str, message: str) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AppError(status_code=409, code=code, message=message) from exc

    async def _template_source(self, source_workload_id: UUID) -> Workload:
        source = await self._session.scalar(
            select(Workload).where(
                Workload.id == source_workload_id,
                Workload.kind == "QEMU",
                Workload.is_template.is_(True),
                Workload.is_present.is_(True),
            )
        )
        if source is None:
            raise AppError(
                status_code=422,
                code="INVALID_TEMPLATE_SOURCE",
                message="The source must be a present QEMU template.",
            )
        return source

    def _key_hash(self, key: str) -> bytes:
        return hmac.new(
            self._settings.app_secret_key.get_secret_value().encode(), key.encode(), sha256
        ).digest()

    @staticmethod
    def _product_response(item: Product) -> ProductResponse:
        return ProductResponse.model_validate(item, from_attributes=True)

    @staticmethod
    def _product_audit(item: Product) -> dict[str, object]:
        return {
            "name": item.name,
            "cpu_cores": item.cpu_cores,
            "memory_bytes": item.memory_bytes,
            "disk_bytes": item.disk_bytes,
            "is_enabled": item.is_enabled,
        }

    @staticmethod
    def _template_response(item: Template) -> TemplateResponse:
        return TemplateResponse.model_validate(item, from_attributes=True)

    @staticmethod
    def _template_audit(item: Template) -> dict[str, object]:
        return {
            "name": item.name,
            "source_workload_id": str(item.source_workload_id),
            "source_disk": item.source_disk,
            "default_storage": item.default_storage,
            "default_bridge": item.default_bridge,
            "default_vlan_tag": item.default_vlan_tag,
            "is_enabled": item.is_enabled,
        }

    @staticmethod
    def _node_response(item: ProvisioningNode) -> ProvisioningNodeResponse:
        return ProvisioningNodeResponse.model_validate(item, from_attributes=True)

    @staticmethod
    def _step_response(item: ProvisioningStep) -> ProvisioningStepResponse:
        return ProvisioningStepResponse(
            order=item.step_order,
            name=item.step_name,
            status=ProvisioningStepStatus(item.status),
            attempt_count=item.attempt_count,
            pve_upid=item.pve_upid,
            safe_result=item.safe_result,
            error_code=item.error_code,
            error_summary=item.error_summary,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )
