import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.schemas.provisioning import ProvisioningRequestCreate


def test_provisioning_routes_are_registered(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    assert {
        "/api/v1/admin/products",
        "/api/v1/admin/products/{product_id}",
        "/api/v1/admin/templates",
        "/api/v1/admin/templates/{template_id}",
        "/api/v1/admin/provisioning-nodes",
        "/api/v1/admin/provision-requests",
        "/api/v1/admin/provision-requests/{request_id}",
    }.issubset(paths)
    assert {"patch", "delete"}.issubset(paths["/api/v1/admin/products/{product_id}"])
    assert {"patch", "delete"}.issubset(paths["/api/v1/admin/templates/{template_id}"])
    assert {"get", "put"}.issubset(paths["/api/v1/admin/provisioning-nodes"])
    node_schema = app.openapi()["components"]["schemas"]["ProvisioningNodeResponse"]
    assert "last_selected_at" in node_schema["properties"]


def test_provisioning_schema_rejects_password_and_unvalidated_inputs() -> None:
    base = {
        "product_id": "11111111-1111-1111-1111-111111111111",
        "template_id": "22222222-2222-2222-2222-222222222222",
        "organization_id": "33333333-3333-3333-3333-333333333333",
        "target_cluster_id": "44444444-4444-4444-4444-444444444444",
        "target_name": "linux-vm-01",
        "ip_pool_id": "55555555-5555-5555-5555-555555555555",
        "cloud_init": {
            "username": "clouduser",
            "ssh_public_keys": ["ssh-ed25519 !!!invalid!!!"],
            "password": "must-not-be-accepted",
        },
    }
    with pytest.raises(ValidationError):
        ProvisioningRequestCreate.model_validate(base)

    base["target_name"] = "unsafe name;shutdown"
    with pytest.raises(ValidationError):
        ProvisioningRequestCreate.model_validate(base)
