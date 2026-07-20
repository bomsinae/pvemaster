from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.api.console import terminal_proxy_auth_frame
from app.models.auth import UserRole
from app.proxmox.client import ConsoleProxyTicket
from app.services.console import (
    ConsoleGrant,
    acquire_console_slot,
    protocol_token_from_header,
    release_console_slot,
)


class FakeConsoleRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool | None:
        del ex
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def eval(self, script: str, numkeys: int, key: str, expected: str) -> int:
        del script, numkeys
        if self.values.get(key) != expected:
            return 0
        del self.values[key]
        return 1


class UnavailableConsoleRedis(FakeConsoleRedis):
    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool | None:
        del key, value, ex, nx
        raise RedisError("unavailable")


def console_grant(*, user_id: Any, workload_id: Any) -> ConsoleGrant:
    return ConsoleGrant(
        session_id=uuid4(),
        user_id=user_id,
        session_epoch=1,
        role=UserRole.SUPER_ADMIN,
        workload_id=workload_id,
        pve_kind="qemu",
        pve_user=None,
        pve_port=5900,
        pve_ticket="ticket",
    )


def test_console_routes_are_registered(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/admin/workloads/{workload_id}/console-sessions" in paths
    assert "/api/v1/customer/vms/{workload_id}/console-sessions" in paths


def test_console_protocol_token_is_read_from_websocket_subprotocols() -> None:
    assert (
        protocol_token_from_header("binary, pvemaster.console.one-time-token") == "one-time-token"
    )


def test_console_protocol_token_rejects_missing_or_empty_value() -> None:
    assert protocol_token_from_header(None) is None
    assert protocol_token_from_header("binary") is None
    assert protocol_token_from_header("binary, pvemaster.console.") is None


async def test_console_slots_are_scoped_to_user_and_workload() -> None:
    fake = FakeConsoleRedis()
    redis = cast(Redis, fake)
    user_id = uuid4()
    first_workload = uuid4()
    second_workload = uuid4()
    first = console_grant(user_id=user_id, workload_id=first_workload)
    duplicate = console_grant(user_id=user_id, workload_id=first_workload)
    different_vm = console_grant(user_id=user_id, workload_id=second_workload)
    different_user = console_grant(user_id=uuid4(), workload_id=first_workload)

    assert await acquire_console_slot(redis, first, 60) is True
    assert await acquire_console_slot(redis, duplicate, 60) is False
    assert await acquire_console_slot(redis, different_vm, 60) is True
    assert await acquire_console_slot(redis, different_user, 60) is True


async def test_console_slot_release_only_removes_the_owning_session() -> None:
    fake = FakeConsoleRedis()
    redis = cast(Redis, fake)
    user_id = uuid4()
    workload_id = uuid4()
    owner = console_grant(user_id=user_id, workload_id=workload_id)
    other_session = console_grant(user_id=user_id, workload_id=workload_id)

    assert await acquire_console_slot(redis, owner, 60) is True
    await release_console_slot(redis, other_session)
    assert await acquire_console_slot(redis, other_session, 60) is False
    await release_console_slot(redis, owner)
    assert await acquire_console_slot(redis, other_session, 60) is True


async def test_console_slot_fails_closed_when_redis_is_unavailable() -> None:
    redis = cast(Redis, UnavailableConsoleRedis())
    grant = console_grant(user_id=uuid4(), workload_id=uuid4())

    assert await acquire_console_slot(redis, grant, 60) is False


def test_lxc_terminal_auth_frame_stays_on_the_server() -> None:
    proxy = ConsoleProxyTicket(
        kind="lxc",
        node="pve-a",
        vmid=202,
        port=5901,
        ticket="PVEVNC:short-lived",
        user="service@pve!pvemaster",
    )

    assert terminal_proxy_auth_frame(proxy) == "service@pve!pvemaster:PVEVNC:short-lived\n"


def test_qemu_console_does_not_use_termproxy_auth_frame() -> None:
    proxy = ConsoleProxyTicket(
        kind="qemu",
        node="pve-a",
        vmid=101,
        port=5900,
        ticket="PVEVNC:short-lived",
    )

    assert terminal_proxy_auth_frame(proxy) is None
