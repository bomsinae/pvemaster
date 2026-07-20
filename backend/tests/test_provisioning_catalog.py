from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import UserRole
from app.models.provisioning import Product, Template
from app.schemas.provisioning import ProductUpdate, TemplateUpdate
from app.security.access import Principal
from app.services.provisioning import ProvisioningService


class FakeCatalogSession:
    def __init__(self, *items: Product | Template, fail_commit: bool = False) -> None:
        self.items = {(type(item), item.id): item for item in items}
        self.fail_commit = fail_commit
        self.deleted: list[Product | Template] = []
        self.added: list[object] = []
        self.rolled_back = False

    async def get(self, model: type[object], item_id: UUID) -> Any:
        return self.items.get((model, item_id))

    def add(self, item: object) -> None:
        self.added.append(item)

    async def delete(self, item: Product | Template) -> None:
        self.deleted.append(item)

    async def commit(self) -> None:
        if self.fail_commit:
            raise IntegrityError("catalog delete", {}, RuntimeError("referenced"))

    async def rollback(self) -> None:
        self.rolled_back = True


def service(settings: Settings, session: FakeCatalogSession) -> ProvisioningService:
    principal = Principal(uuid4(), "admin@example.test", UserRole.SUPER_ADMIN, 0)
    return ProvisioningService(
        session=cast(AsyncSession, session),
        settings=settings,
        principal=principal,
        publisher=lambda _request_id, _task_id: None,
        request_id="catalog-request",
        source_ip="127.0.0.1",
    )


async def test_product_and_template_can_be_updated_and_deleted(settings: Settings) -> None:
    product = Product(
        id=uuid4(),
        name="standard",
        cpu_cores=2,
        memory_bytes=2 * 1024**3,
        disk_bytes=20 * 1024**3,
        is_enabled=True,
        created_by_id=uuid4(),
    )
    template = Template(
        id=uuid4(),
        name="ubuntu",
        source_workload_id=uuid4(),
        source_disk="scsi0",
        default_storage="local-lvm",
        default_bridge="vmbr0",
        default_vlan_tag=None,
        cloud_init_enabled=True,
        linux_only=True,
        is_enabled=True,
        created_by_id=uuid4(),
    )
    session = FakeCatalogSession(product, template)
    catalog = service(settings, session)

    updated_product = await catalog.update_product(
        product.id,
        ProductUpdate(name="standard-v2", cpu_cores=4, is_enabled=False),
    )
    updated_template = await catalog.update_template(
        template.id,
        TemplateUpdate(default_bridge="vmbr1", default_vlan_tag=120, is_enabled=False),
    )
    await catalog.delete_product(product.id)
    await catalog.delete_template(template.id)

    assert updated_product.name == "standard-v2"
    assert updated_product.cpu_cores == 4
    assert updated_product.is_enabled is False
    assert updated_template.default_bridge == "vmbr1"
    assert updated_template.default_vlan_tag == 120
    assert updated_template.is_enabled is False
    assert session.deleted == [product, template]


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [("product", "PRODUCT_IN_USE"), ("template", "TEMPLATE_IN_USE")],
)
async def test_referenced_catalog_items_cannot_be_deleted(
    settings: Settings, kind: str, expected_code: str
) -> None:
    product = Product(
        id=uuid4(),
        name="referenced-product",
        cpu_cores=2,
        memory_bytes=2 * 1024**3,
        disk_bytes=20 * 1024**3,
        is_enabled=True,
        created_by_id=uuid4(),
    )
    template = Template(
        id=uuid4(),
        name="referenced-template",
        source_workload_id=uuid4(),
        source_disk="scsi0",
        default_storage="local-lvm",
        default_bridge="vmbr0",
        default_vlan_tag=None,
        cloud_init_enabled=True,
        linux_only=True,
        is_enabled=True,
        created_by_id=uuid4(),
    )
    session = FakeCatalogSession(product, template, fail_commit=True)
    catalog = service(settings, session)

    with pytest.raises(AppError) as caught:
        if kind == "product":
            await catalog.delete_product(product.id)
        else:
            await catalog.delete_template(template.id)

    assert caught.value.code == expected_code
    assert caught.value.status_code == 409
    assert session.rolled_back is True
