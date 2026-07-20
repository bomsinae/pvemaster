from collections.abc import Awaitable, Callable
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.errors import AppError, ErrorResponse
from app.health import Readiness

router = APIRouter(prefix="/api/v1/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(HealthResponse):
    services: dict[str, str]


async def readiness_from_app(request: Request) -> Readiness:
    readiness_check = cast(
        Callable[[object, object], Awaitable[Readiness]],
        request.app.state.readiness_check,
    )
    return await readiness_check(
        request.app.state.db_engine,
        request.app.state.redis,
    )


@router.get("", response_model=HealthResponse)
@router.get("/live", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ErrorResponse}},
)
async def ready(
    readiness: Annotated[Readiness, Depends(readiness_from_app)],
) -> ReadyResponse:
    if not readiness.ready:
        unavailable = [
            name
            for name, available in {
                "database": readiness.database,
                "redis": readiness.redis,
            }.items()
            if not available
        ]
        raise AppError(
            status_code=503,
            code="DEPENDENCY_UNAVAILABLE",
            message="A required service is unavailable.",
            details={"services": unavailable},
        )
    return ReadyResponse(
        status="ready",
        services={"database": "ready", "redis": "ready"},
    )
