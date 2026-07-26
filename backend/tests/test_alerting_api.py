from fastapi import FastAPI


def test_alerting_routes_and_channel_response_redaction(app: FastAPI) -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/admin/alerts" in paths
    assert "/api/v1/customer/alerts" in paths
    assert "/api/v1/admin/maintenance-windows" in paths
    assert "/api/v1/admin/notification-channels" in paths
    assert "/api/v1/admin/notification-rules" in paths

    channel = schema["components"]["schemas"]["NotificationChannelResponse"]["properties"]
    assert "configured" in channel
    assert "webhook_url" not in channel
    assert "email" not in channel
    assert "secret" not in channel
