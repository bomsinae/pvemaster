from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.health import Readiness


async def test_health_returns_ok_and_request_id(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health", headers={"X-Request-ID": "test-request-1"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "test-request-1"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "payment=()" in response.headers["Permissions-Policy"]
    assert "Strict-Transport-Security" not in response.headers


async def test_customer_power_action_cors_preflight_allows_idempotency_key(
    app: FastAPI,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/api/v1/customer/vms/test-vm/actions/shutdown",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": ("authorization,content-type,idempotency-key"),
            },
        )

    assert response.status_code == 200
    allowed_headers = response.headers["Access-Control-Allow-Headers"].lower()
    assert "idempotency-key" in allowed_headers


async def test_customer_forced_stop_route_is_registered_and_requires_auth(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/customer/vms/{uuid4()}/actions/stop",
            headers={"Idempotency-Key": "forced-stop-test-key"},
            json={"confirm_forced": True},
        )

    assert response.status_code == 401


async def test_customer_console_route_is_registered_and_requires_auth(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/customer/vms/{uuid4()}/console-sessions",
        )

    assert response.status_code == 401


async def test_ready_when_database_and_redis_are_available(app: FastAPI) -> None:
    async def ready(*_: object) -> Readiness:
        return Readiness(database=True, redis=True)

    app.state.readiness_check = ready
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "services": {"database": "ready", "redis": "ready"},
    }


async def test_ready_reports_database_failure(app: FastAPI) -> None:
    async def database_unavailable(*_: object) -> Readiness:
        return Readiness(database=False, redis=True)

    app.state.readiness_check = database_unavailable
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/health/ready", headers={"X-Request-ID": "ready-db-fail"}
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DEPENDENCY_UNAVAILABLE",
            "message": "A required service is unavailable.",
            "details": {"services": ["database"]},
            "request_id": "ready-db-fail",
        }
    }
