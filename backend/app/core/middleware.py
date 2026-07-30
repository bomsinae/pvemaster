import re
from contextvars import ContextVar
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_context

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
source_ip_context: ContextVar[str | None] = ContextVar("source_ip", default=None)
user_agent_context: ContextVar[str | None] = ContextVar("user_agent", default=None)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = Headers(scope=scope).get("X-Request-ID", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        headers = Headers(scope=scope)
        client = scope.get("client")
        source_ip = client[0] if client else None
        source_token = source_ip_context.set(source_ip)
        user_agent_token = user_agent_context.set(headers.get("user-agent"))

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            user_agent_context.reset(user_agent_token)
            source_ip_context.reset(source_token)
            request_id_context.reset(token)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, enable_hsts: bool = False) -> None:
        self.app = app
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = (
                    "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
                )
                headers["Cache-Control"] = "no-store"
                if self.enable_hsts:
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
