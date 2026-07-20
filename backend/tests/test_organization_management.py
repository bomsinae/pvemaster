from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI

from app.core.errors import AppError
from app.models.auth import Organization, UserRole
from app.schemas.auth import OrganizationUpdate
from app.security.access import Principal
from app.security.passwords import PasswordManager
from app.services.accounts import AccountService


def _service(role: UserRole = UserRole.SUPER_ADMIN) -> tuple[AccountService, AsyncMock]:
    session = AsyncMock()
    session.add = Mock()
    principal = Principal(
        user_id=uuid4(),
        email=f"{role.value.lower()}@example.test",
        role=role,
        session_epoch=0,
    )
    return AccountService(
        session=session,
        principal=principal,
        passwords=PasswordManager(),
        request_id="organization-management",
    ), session


def _organization() -> Organization:
    now = datetime.now(UTC)
    return Organization(
        id=uuid4(),
        name="Original organization",
        is_active=True,
        created_by_id=uuid4(),
        created_at=now,
        updated_at=now,
        version=2,
    )


def test_organization_update_and_delete_routes_are_registered(app: FastAPI) -> None:
    methods = app.openapi()["paths"]["/api/v1/admin/organizations/{organization_id}"]
    assert {"patch", "delete"}.issubset(methods)


def test_organization_directory_filters_are_registered(app: FastAPI) -> None:
    parameters = app.openapi()["paths"]["/api/v1/admin/organizations"]["get"]["parameters"]
    parameter_names = {parameter["name"] for parameter in parameters}
    assert {"q", "status", "sort", "limit", "offset"}.issubset(parameter_names)


@pytest.mark.asyncio
async def test_organization_directory_filters_inactive_and_sorts_oldest() -> None:
    service, session = _service(UserRole.OPERATOR)
    organization = _organization()
    organization.is_active = False
    session.scalar = AsyncMock(return_value=1)
    result = Mock()
    result.all.return_value = [organization]
    session.scalars = AsyncMock(return_value=result)

    items, total = await service.list_organizations(
        status="inactive",
        sort="oldest",
        limit=25,
        offset=25,
    )

    assert total == 1
    assert [item.id for item in items] == [organization.id]
    statement = session.scalars.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "organizations.is_active IS false" in sql
    assert "ORDER BY organizations.created_at ASC" in sql
    assert "LIMIT 25 OFFSET 25" in sql


@pytest.mark.asyncio
async def test_super_admin_updates_organization_with_version_check() -> None:
    service, session = _service()
    organization = _organization()
    service._get_organization = AsyncMock(return_value=organization)  # type: ignore[method-assign]

    updated = await service.update_organization(
        organization.id,
        OrganizationUpdate(name="Renamed organization", version=2),
    )

    assert updated.name == "Renamed organization"
    assert updated.version == 3
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_organization_delete_is_blocked_while_members_remain() -> None:
    service, session = _service()
    organization = _organization()
    service._get_organization = AsyncMock(return_value=organization)  # type: ignore[method-assign]
    session.scalar = AsyncMock(side_effect=[1, 0, 0])

    with pytest.raises(AppError) as caught:
        await service.delete_organization(organization.id, version=2)

    assert caught.value.code == "ORGANIZATION_IN_USE"
    assert caught.value.details["members"] == 1
    assert organization.is_active is True
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_organization_delete_soft_disables_empty_organization() -> None:
    service, session = _service()
    organization = _organization()
    service._get_organization = AsyncMock(return_value=organization)  # type: ignore[method-assign]
    session.scalar = AsyncMock(side_effect=[0, 0, 0])

    await service.delete_organization(organization.id, version=2)

    assert organization.is_active is False
    assert organization.version == 3
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_operator_cannot_update_organization() -> None:
    service, _session = _service(UserRole.OPERATOR)

    with pytest.raises(AppError) as caught:
        await service.update_organization(
            uuid4(),
            OrganizationUpdate(name="Denied", version=1),
        )

    assert caught.value.code == "FORBIDDEN"
