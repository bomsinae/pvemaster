from fastapi import FastAPI


def test_required_ipam_routes_are_registered(app: FastAPI) -> None:
    paths = app.openapi()["paths"]

    assert {
        "/api/v1/admin/ip-pools",
        "/api/v1/admin/ip-pools/{pool_id}",
        "/api/v1/admin/ip-pools/{pool_id}/addresses",
        "/api/v1/admin/ip-pools/{pool_id}/reservations",
        "/api/v1/admin/ip-pools/{pool_id}/allocations",
        "/api/v1/admin/ip-allocations/{allocation_id}",
        "/api/v1/admin/ip-addresses/{address_id}/approve-release",
    }.issubset(paths)
    pool_detail = paths["/api/v1/admin/ip-pools/{pool_id}"]
    assert {"get", "patch", "delete"}.issubset(pool_detail)
