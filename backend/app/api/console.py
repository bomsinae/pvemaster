import asyncio
import logging
from contextlib import suppress
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.models.auth import UserRole
from app.proxmox.client import ConsoleProxyTicket, ProxmoxClient
from app.schemas.console import ConsoleSessionResponse
from app.security.access import require_service_role
from app.security.credentials import CredentialCipher
from app.services.console import (
    ConsoleSessionService,
    acquire_console_slot,
    consume_console_grant,
    load_console_connection,
    protocol_token_from_header,
    release_console_slot,
)

router = APIRouter(tags=["console"])
logger = logging.getLogger(__name__)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def terminal_proxy_auth_frame(proxy: ConsoleProxyTicket) -> str | None:
    if proxy.kind != "lxc":
        return None
    if proxy.user is None:
        raise ValueError("LXC console proxy user is missing")
    return f"{proxy.user}:{proxy.ticket}\n"


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> ConsoleSessionService:
    return ConsoleSessionService(
        session=session,
        redis=cast(Redis, request.app.state.redis),
        settings=cast(Settings, request.app.state.settings),
        cipher=cast(CredentialCipher, request.app.state.credential_cipher),
        principal=principal,
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
    )


@router.post(
    "/api/v1/admin/workloads/{workload_id}/console-sessions",
    response_model=ConsoleSessionResponse,
)
async def create_console_session(
    workload_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ConsoleSessionResponse:
    require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).create(workload_id)


@router.post(
    "/api/v1/customer/vms/{workload_id}/console-sessions",
    response_model=ConsoleSessionResponse,
)
async def create_customer_console_session(
    workload_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ConsoleSessionResponse:
    require_service_role(principal, UserRole.CUSTOMER)
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).create(workload_id)


async def _browser_to_pve(browser: WebSocket, upstream: ClientConnection) -> None:
    while True:
        message = await browser.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        binary = message.get("bytes")
        if binary is not None:
            await upstream.send(binary)
            continue
        text = message.get("text")
        if text is not None:
            await upstream.send(text)


async def _pve_to_browser(browser: WebSocket, upstream: ClientConnection) -> None:
    while True:
        message = await upstream.recv()
        if isinstance(message, bytes):
            await browser.send_bytes(message)
        else:
            await browser.send_text(str(message))


async def _relay(browser: WebSocket, upstream: ClientConnection, max_duration: int) -> None:
    tasks = {
        asyncio.create_task(_browser_to_pve(browser, upstream)),
        asyncio.create_task(_pve_to_browser(browser, upstream)),
    }
    try:
        async with asyncio.timeout(max_duration):
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.websocket("/api/v1/console/ws/{session_id}")
async def console_websocket(websocket: WebSocket, session_id: UUID) -> None:
    settings = cast(Settings, websocket.app.state.settings)
    allowed_origins = {str(origin).rstrip("/") for origin in settings.cors_origins}
    origin = websocket.headers.get("origin", "").rstrip("/")
    requested_protocols = {
        item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
    }
    protocol_token = protocol_token_from_header(websocket.headers.get("sec-websocket-protocol"))
    if origin not in allowed_origins or "binary" not in requested_protocols or not protocol_token:
        await websocket.close(code=4403)
        return

    redis = cast(Redis, websocket.app.state.redis)
    grant = await consume_console_grant(redis, session_id=session_id, protocol_token=protocol_token)
    if grant is None:
        await websocket.close(code=4401)
        return
    if not await acquire_console_slot(redis, grant, settings.console_max_duration_seconds):
        await websocket.close(code=4429)
        return

    upstream: ClientConnection | None = None
    client: ProxmoxClient | None = None
    accepted = False
    try:
        session_factory = cast(
            async_sessionmaker[AsyncSession], websocket.app.state.db_session_factory
        )
        async with session_factory() as session:
            connection = await load_console_connection(
                session,
                grant=grant,
                settings=settings,
                cipher=cast(CredentialCipher, websocket.app.state.credential_cipher),
            )
        if connection is None:
            await websocket.close(code=4404)
            return
        client = connection.client
        await websocket.accept(subprotocol="binary")
        accepted = True
        upstream = await client.open_console_websocket(
            connection.proxy, open_timeout=settings.console_connect_timeout_seconds
        )
        terminal_auth = terminal_proxy_auth_frame(connection.proxy)
        if terminal_auth is not None:
            await upstream.send(terminal_auth)
        await _relay(websocket, upstream, settings.console_max_duration_seconds)
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    except TimeoutError:
        if accepted:
            with suppress(RuntimeError):
                await websocket.close(code=1000, reason="Console session expired")
    except Exception:
        logger.exception(
            "Console relay failed",
            extra={"console_session_id": str(session_id), "workload_id": str(grant.workload_id)},
        )
        if accepted:
            with suppress(RuntimeError):
                await websocket.close(code=1011, reason="Console connection failed")
    finally:
        if upstream is not None:
            with suppress(Exception):
                await upstream.close()
        if client is not None:
            await client.aclose()
        await release_console_slot(redis, grant)
