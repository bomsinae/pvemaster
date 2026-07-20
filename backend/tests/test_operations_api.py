from fastapi import FastAPI


def test_required_power_operation_routes_are_registered(app: FastAPI) -> None:
    paths = app.openapi()["paths"]

    assert {
        "/api/v1/admin/workloads/{workload_id}/actions/{action}",
        "/api/v1/admin/vms/{vm_id}/actions/start",
        "/api/v1/admin/vms/{vm_id}/actions/shutdown",
        "/api/v1/admin/vms/{vm_id}/actions/stop",
        "/api/v1/admin/vms/{vm_id}/actions/reboot",
        "/api/v1/admin/vms/{vm_id}/actions/reset",
        "/api/v1/admin/vms/{vm_id}/spec",
        "/api/v1/admin/vms/{vm_id}",
        "/api/v1/jobs/{job_id}",
    }.issubset(paths)
    assert not paths["/api/v1/admin/workloads/{workload_id}/actions/{action}"]["post"].get(
        "deprecated", False
    )
    assert paths["/api/v1/admin/vms/{vm_id}/actions/start"]["post"]["deprecated"] is True
