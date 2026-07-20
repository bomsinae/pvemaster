from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.provisioning import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
    ProvisioningNodeListResponse,
    ProvisioningNodeResponse,
    ProvisioningNodeUpsert,
    ProvisioningRequestCreate,
    ProvisioningRequestListResponse,
    ProvisioningRequestResponse,
    TemplateCreate,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdate,
)
from app.services.provisioning import ProvisioningPublisher, ProvisioningService

router = APIRouter(prefix="/api/v1/admin", tags=["provisioning"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)]


def _service(
    request: Request, session: AsyncSession, principal: PrincipalDependency
) -> ProvisioningService:
    return ProvisioningService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        principal=principal,
        publisher=cast(ProvisioningPublisher, request.app.state.provisioning_publisher),
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
    )


@router.post("/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ProductResponse:
    return await _service(request, session, principal).create_product(payload)


@router.get("/products", response_model=ProductListResponse)
async def list_products(
    request: Request, session: SessionDependency, principal: PrincipalDependency
) -> ProductListResponse:
    return ProductListResponse(items=await _service(request, session, principal).list_products())


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    payload: ProductUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ProductResponse:
    return await _service(request, session, principal).update_product(product_id, payload)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _service(request, session, principal).delete_product(product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/templates", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> TemplateResponse:
    return await _service(request, session, principal).create_template(payload)


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    request: Request, session: SessionDependency, principal: PrincipalDependency
) -> TemplateListResponse:
    return TemplateListResponse(items=await _service(request, session, principal).list_templates())


@router.patch("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: UUID,
    payload: TemplateUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> TemplateResponse:
    return await _service(request, session, principal).update_template(template_id, payload)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _service(request, session, principal).delete_template(template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/provisioning-nodes", response_model=ProvisioningNodeResponse)
async def upsert_provisioning_node(
    payload: ProvisioningNodeUpsert,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ProvisioningNodeResponse:
    return await _service(request, session, principal).upsert_node(payload)


@router.get("/provisioning-nodes", response_model=ProvisioningNodeListResponse)
async def list_provisioning_nodes(
    request: Request, session: SessionDependency, principal: PrincipalDependency
) -> ProvisioningNodeListResponse:
    return ProvisioningNodeListResponse(
        items=await _service(request, session, principal).list_nodes()
    )


@router.post(
    "/provision-requests",
    response_model=ProvisioningRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_provisioning_request(
    payload: ProvisioningRequestCreate,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ProvisioningRequestResponse:
    item, _created = await _service(request, session, principal).create_request(
        payload, idempotency_key
    )
    response.headers["Location"] = f"/api/v1/admin/provision-requests/{item.id}"
    response.headers["Retry-After"] = "2"
    response.headers["Cache-Control"] = "no-store"
    return item


@router.get("/provision-requests", response_model=ProvisioningRequestListResponse)
async def list_provisioning_requests(
    request: Request, session: SessionDependency, principal: PrincipalDependency
) -> ProvisioningRequestListResponse:
    return ProvisioningRequestListResponse(
        items=await _service(request, session, principal).list_requests()
    )


@router.get("/provision-requests/{request_id}", response_model=ProvisioningRequestResponse)
async def get_provisioning_request(
    request_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ProvisioningRequestResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).get_request(request_id)
