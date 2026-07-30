from fastapi import FastAPI


def test_backup_policy_and_verification_routes_are_documented(app: FastAPI) -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/admin/backup-policies" in paths
    assert "/api/v1/admin/backup-policies/{policy_id}/preview" in paths
    assert "/api/v1/admin/backup-policies/{policy_id}/run-now" in paths
    assert "/api/v1/admin/backup-policies/{policy_id}/skip" in paths
    assert "/api/v1/admin/backup-metadata/reconcile" in paths
    assert "/api/v1/admin/backups/{run_id}/verifications" in paths
    assert "/api/v1/admin/backup-verifications" in paths

    verification = schema["components"]["schemas"]["BackupVerificationResponse"]["properties"]
    assert "snapshot_volume_id" in verification
    assert "target_node" not in verification
    assert "pve_upid" not in verification
