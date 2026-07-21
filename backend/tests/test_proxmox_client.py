import ssl
from collections.abc import Callable
from secrets import token_urlsafe
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.errors import AppError
from app.proxmox.client import ProxmoxClient

Handler = Callable[[httpx.Request], httpx.Response]


async def resolve_public_test_address(_host: str, _port: int) -> list[str]:
    return ["8.8.8.8"]


def make_client(handler: Handler, *, secret: str | None = None) -> ProxmoxClient:
    return ProxmoxClient(
        api_base_url="https://pve.example.test:8006",
        token_identifier="service@pve!pvemaster",
        token_secret=secret or token_urlsafe(32),
        connect_timeout=1,
        read_timeout=2,
        max_connections=5,
        max_keepalive_connections=2,
        allowed_hosts=["pve.example.test"],
        endpoint_resolver=resolve_public_test_address,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    ("status_code", "error_code", "api_status"),
    [
        (401, "PVE_AUTH_FAILED", 401),
        (403, "PVE_PERMISSION_DENIED", 403),
        (503, "PVE_UPSTREAM_ERROR", 502),
    ],
)
async def test_http_errors_are_classified(
    status_code: int,
    error_code: str,
    api_status: int,
) -> None:
    async with make_client(lambda _: httpx.Response(status_code)) as client:
        with pytest.raises(AppError) as error:
            await client.get_nodes()

    assert error.value.code == error_code
    assert error.value.status_code == api_status


async def test_timeout_is_classified() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    async with make_client(timeout) as client:
        with pytest.raises(AppError) as error:
            await client.get_nodes()

    assert error.value.code == "PVE_TIMEOUT"
    assert error.value.status_code == 504


async def test_tls_verification_error_is_classified() -> None:
    def tls_failure(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLCertVerificationError as exc:
            raise httpx.ConnectError("TLS failed", request=request) from exc

    async with make_client(tls_failure) as client:
        with pytest.raises(AppError) as error:
            await client.get_nodes()

    assert error.value.code == "PVE_TLS_ERROR"
    assert error.value.status_code == 502


@pytest.mark.parametrize("resolved_address", ["127.0.0.1", "169.254.169.254", "::1"])
async def test_ssrf_destination_is_rejected_before_authorization_is_sent(
    resolved_address: str,
) -> None:
    requests: list[httpx.Request] = []

    async def resolve_attacker_destination(_host: str, _port: int) -> list[str]:
        return [resolved_address]

    def capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    client = ProxmoxClient(
        api_base_url="https://attacker.example.test:8006",
        token_identifier="service@pve!pvemaster",
        token_secret=token_urlsafe(32),
        connect_timeout=1,
        read_timeout=2,
        max_connections=5,
        max_keepalive_connections=2,
        allowed_hosts=["attacker.example.test"],
        endpoint_resolver=resolve_attacker_destination,
        transport=httpx.MockTransport(capture),
    )
    async with client:
        with pytest.raises(AppError) as error:
            await client.get_nodes()

    assert error.value.code == "PVE_ENDPOINT_NOT_ALLOWED"
    assert requests == []


async def test_endpoint_outside_explicit_allowlist_is_rejected() -> None:
    requests: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    client = ProxmoxClient(
        api_base_url="https://attacker.example.test:8006",
        token_identifier="service@pve!pvemaster",
        token_secret=token_urlsafe(32),
        connect_timeout=1,
        read_timeout=2,
        max_connections=5,
        max_keepalive_connections=2,
        allowed_hosts=["pve.example.test"],
        endpoint_resolver=resolve_public_test_address,
        transport=httpx.MockTransport(capture),
    )
    async with client:
        with pytest.raises(AppError) as error:
            await client.get_nodes()

    assert error.value.code == "PVE_ENDPOINT_NOT_ALLOWED"
    assert requests == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"result": []}),
        httpx.Response(200, json={"data": {}}),
        httpx.Response(200, json={"data": ["not-an-object"]}),
    ],
)
async def test_invalid_proxmox_response_is_rejected(response: httpx.Response) -> None:
    async with make_client(lambda _: response) as client:
        with pytest.raises(AppError) as error:
            await client.get_nodes()

    assert error.value.code == "PVE_INVALID_RESPONSE"
    assert error.value.status_code == 502


async def test_connection_probe_uses_token_header_and_all_resource_endpoints() -> None:
    secret = token_urlsafe(32)
    paths: list[str] = []

    def success(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["Authorization"] == (f"PVEAPIToken=service@pve!pvemaster={secret}")
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"data": {"version": "9.0", "release": "1"}})
        return httpx.Response(200, json={"data": []})

    async with make_client(success, secret=secret) as client:
        result = await client.test_connection()

    assert result["version"] == "9.0"
    assert paths == [
        "/api2/json/version",
        "/api2/json/nodes",
        "/api2/json/cluster/resources",
        "/api2/json/cluster/resources",
    ]


async def test_node_status_uses_encoded_node_path() -> None:
    captured_path = ""

    def success(request: httpx.Request) -> httpx.Response:
        nonlocal captured_path
        captured_path = request.url.raw_path.decode()
        return httpx.Response(
            200,
            json={"data": {"cpu": 0.25, "loadavg": ["1.0", "0.8", "0.7"]}},
        )

    async with make_client(success) as client:
        status = await client.get_node_status(node="pve node")

    assert captured_path == "/api2/json/nodes/pve%20node/status"
    assert status["cpu"] == 0.25


async def test_node_rrd_data_uses_encoded_path_and_average_query() -> None:
    captured_path = ""
    captured_query: dict[str, list[str]] = {}

    def success(request: httpx.Request) -> httpx.Response:
        nonlocal captured_path, captured_query
        captured_path = request.url.raw_path.decode().split("?", maxsplit=1)[0]
        captured_query = parse_qs(request.url.query.decode())
        return httpx.Response(200, json={"data": [{"time": 1720000000, "cpu": 0.25}]})

    async with make_client(success) as client:
        data = await client.get_node_rrd_data(node="pve node", timeframe="day")

    assert captured_path == "/api2/json/nodes/pve%20node/rrddata"
    assert captured_query == {"timeframe": ["day"], "cf": ["AVERAGE"]}
    assert data[0]["cpu"] == 0.25


async def test_vm_power_action_and_upid_status_use_expected_endpoints() -> None:
    requests: list[tuple[str, str]] = []
    upid = "UPID:pve-a:00000001:00000002:00000003:qmstart:101:service@pve:"

    def success(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.raw_path.decode()))
        if request.method == "POST":
            return httpx.Response(200, json={"data": upid})
        if request.url.path.endswith("/status/current"):
            return httpx.Response(200, json={"data": {"status": "stopped"}})
        return httpx.Response(200, json={"data": {"status": "stopped", "exitstatus": "OK"}})

    async with make_client(success) as client:
        current = await client.get_vm_status(node="pve-a", vmid=101)
        submitted = await client.submit_vm_power_action(node="pve-a", vmid=101, action="start")
        task = await client.get_task_status(node="pve-a", upid=upid)

    assert current["status"] == "stopped"
    assert submitted == upid
    assert task["exitstatus"] == "OK"
    assert requests == [
        ("GET", "/api2/json/nodes/pve-a/qemu/101/status/current"),
        ("POST", "/api2/json/nodes/pve-a/qemu/101/status/start"),
        (
            "GET",
            "/api2/json/nodes/pve-a/tasks/"
            "UPID%3Apve-a%3A00000001%3A00000002%3A00000003%3Aqmstart%3A101%3Aservice%40pve%3A/status",
        ),
    ]


async def test_invalid_upid_response_is_rejected() -> None:
    async with make_client(lambda _: httpx.Response(200, json={"data": "not-a-upid"})) as client:
        with pytest.raises(AppError) as error:
            await client.submit_vm_power_action(node="pve-a", vmid=101, action="start")

    assert error.value.code == "PVE_INVALID_RESPONSE"


async def test_lxc_power_actions_use_container_endpoints_and_reject_reset() -> None:
    requests: list[tuple[str, str]] = []
    upid = "UPID:pve-a:00000001:00000002:00000003:vzstart:202:service@pve:"

    def success(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"data": upid})
        return httpx.Response(200, json={"data": {"status": "stopped"}})

    async with make_client(success) as client:
        current = await client.get_guest_status(kind="LXC", node="pve-a", vmid=202)
        for action in ("start", "shutdown", "reboot", "stop"):
            assert (
                await client.submit_guest_power_action(
                    kind="LXC", node="pve-a", vmid=202, action=action
                )
                == upid
            )
        with pytest.raises(ValueError, match="unsupported guest power action"):
            await client.submit_guest_power_action(
                kind="LXC", node="pve-a", vmid=202, action="reset"
            )

    assert current["status"] == "stopped"
    assert requests == [
        ("GET", "/api2/json/nodes/pve-a/lxc/202/status/current"),
        ("POST", "/api2/json/nodes/pve-a/lxc/202/status/start"),
        ("POST", "/api2/json/nodes/pve-a/lxc/202/status/shutdown"),
        ("POST", "/api2/json/nodes/pve-a/lxc/202/status/reboot"),
        ("POST", "/api2/json/nodes/pve-a/lxc/202/status/stop"),
    ]


async def test_pbs_storage_discovery_backup_submission_and_content_listing() -> None:
    requests: list[tuple[str, str, dict[str, list[str]]]] = []
    upid = "UPID:pve-a:00000001:00000002:00000003:vzdump:101:service@pve:"

    def success(request: httpx.Request) -> httpx.Response:
        form_or_query = (
            parse_qs(request.content.decode())
            if request.method == "POST"
            else parse_qs(request.url.query.decode())
        )
        requests.append((request.method, request.url.path, form_or_query))
        if request.url.path == "/api2/json/storage":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "storage": "pbs-main",
                            "type": "pbs",
                            "content": "backup",
                            "datastore": "main",
                            "namespace": "pvemaster/pve-a",
                        }
                    ]
                },
            )
        if request.method == "POST":
            return httpx.Response(200, json={"data": upid})
        if request.url.path.endswith("/log"):
            return httpx.Response(200, json={"data": [{"n": 1, "t": "transferred 1 GiB"}]})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "content": "backup",
                        "volid": "pbs-main:backup/vm/101/1720000000",
                        "vmid": 101,
                        "ctime": 1720000000,
                        "size": 1024,
                    }
                ]
            },
        )

    async with make_client(success) as client:
        configurations = await client.get_storage_configurations()
        submitted = await client.submit_guest_backup(
            node="pve-a",
            vmid=101,
            storage="pbs-main",
            mode="snapshot",
            compression="zstd",
        )
        content = await client.get_backup_content(node="pve-a", storage="pbs-main", vmid=101)
        task_log = await client.get_task_log(node="pve-a", upid=upid)

    assert configurations[0]["type"] == "pbs"
    assert submitted == upid
    assert content[0]["size"] == 1024
    assert task_log[0]["t"] == "transferred 1 GiB"
    assert requests == [
        ("GET", "/api2/json/storage", {}),
        (
            "POST",
            "/api2/json/nodes/pve-a/vzdump",
            {
                "vmid": ["101"],
                "storage": ["pbs-main"],
                "mode": ["snapshot"],
                "compress": ["zstd"],
            },
        ),
        (
            "GET",
            "/api2/json/nodes/pve-a/storage/pbs-main/content",
            {"content": ["backup"], "vmid": ["101"]},
        ),
        (
            "GET",
            f"/api2/json/nodes/pve-a/tasks/{upid}/log",
            {"start": ["0"], "limit": ["500"]},
        ),
    ]


@pytest.mark.parametrize(
    ("mode", "compression"),
    [("stop", "zstd"), ("snapshot", "gzip")],
)
async def test_backup_submission_rejects_non_allowlisted_options(
    mode: str, compression: str
) -> None:
    async with make_client(lambda _: httpx.Response(500)) as client:
        with pytest.raises(ValueError, match="unsupported backup option"):
            await client.submit_guest_backup(
                node="pve-a",
                vmid=101,
                storage="pbs-main",
                mode=mode,
                compression=compression,
            )


@pytest.mark.parametrize(
    ("kind", "path", "expected"),
    [
        (
            "QEMU",
            "/api2/json/nodes/pve-a/qemu",
            {
                "vmid": ["220"],
                "start": ["0"],
                "archive": ["pbs-main:backup/vm/101/1720000000"],
                "name": ["service-restored"],
                "unique": ["1"],
            },
        ),
        (
            "LXC",
            "/api2/json/nodes/pve-a/lxc",
            {
                "vmid": ["220"],
                "start": ["0"],
                "ostemplate": ["pbs-main:backup/ct/101/1720000000"],
                "hostname": ["service-restored"],
                "restore": ["1"],
            },
        ),
    ],
)
async def test_guest_restore_uses_new_guest_without_force(
    kind: str, path: str, expected: dict[str, list[str]]
) -> None:
    requests: list[tuple[str, dict[str, list[str]]]] = []
    upid = "UPID:pve-a:00000001:00000002:00000003:qmrestore:220:service@pve:"

    def success(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, parse_qs(request.content.decode())))
        return httpx.Response(200, json={"data": upid})

    archive = f"pbs-main:backup/{'vm' if kind == 'QEMU' else 'ct'}/101/1720000000"
    async with make_client(success) as client:
        result = await client.submit_guest_restore(
            kind=kind,
            node="pve-a",
            archive=archive,
            vmid=220,
            name="service-restored",
        )

    assert result == upid
    assert requests == [(path, expected)]
    assert "force" not in requests[0][1]


async def test_provisioning_calls_use_full_clone_and_allowlisted_form_fields() -> None:
    requests: list[tuple[str, str, dict[str, list[str]]]] = []

    def success(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        requests.append((request.method, request.url.path, form))
        if request.url.path.endswith("/clone"):
            return httpx.Response(200, json={"data": "UPID:clone"})
        return httpx.Response(200, json={"data": None})

    async with make_client(success) as client:
        upid = await client.clone_qemu_template(
            source_node="pve-source",
            source_vmid=9000,
            target_node="pve-target",
            target_vmid=101,
            name="linux-vm-01",
            storage="local-lvm",
        )
        await client.configure_qemu(
            node="pve-target",
            vmid=101,
            values={"cores": "2", "memory": "2048"},
        )
        await client.resize_qemu_disk(
            node="pve-target", vmid=101, disk="scsi0", size_bytes=21_474_836_480
        )

    assert upid == "UPID:clone"
    assert requests[0][2] == {
        "newid": ["101"],
        "node": ["pve-target"],
        "name": ["linux-vm-01"],
        "storage": ["local-lvm"],
        "full": ["1"],
    }
    assert requests[1][2] == {"cores": ["2"], "memory": ["2048"]}
    assert requests[2][2] == {
        "disk": ["scsi0"],
        "size": ["21474836480"],
    }


async def test_qemu_ssh_keys_are_encoded_for_proxmox_validation() -> None:
    captured: dict[str, list[str]] = {}

    def success(request: httpx.Request) -> httpx.Response:
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"data": None})

    async with make_client(success) as client:
        await client.configure_qemu(
            node="pve-a",
            vmid=101,
            values={"sshkeys": "ssh-rsa AAAA+test/value comment"},
        )

    assert captured == {"sshkeys": ["ssh-rsa%20AAAA%2Btest%2Fvalue%20comment"]}


async def test_admin_guest_mutations_use_kind_scoped_paths_and_allowlisted_values() -> None:
    requests: list[tuple[str, str, dict[str, list[str]]]] = []

    def success(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, parse_qs(request.content.decode())))
        if request.method == "DELETE":
            return httpx.Response(200, json={"data": "UPID:delete"})
        return httpx.Response(200, json={"data": None})

    async with make_client(success) as client:
        await client.configure_guest(kind="LXC", node="pve-a", vmid=101, cores=4, memory_mib=4096)
        await client.resize_guest_disk(
            kind="LXC", node="pve-a", vmid=101, disk="rootfs", size_bytes=40 * 1024**3
        )
        upid = await client.delete_guest(kind="LXC", node="pve-a", vmid=101)

    assert requests[0] == (
        "PUT",
        "/api2/json/nodes/pve-a/lxc/101/config",
        {"cores": ["4"], "memory": ["4096"]},
    )
    assert requests[1][1] == "/api2/json/nodes/pve-a/lxc/101/resize"
    assert requests[1][2]["disk"] == ["rootfs"]
    assert requests[2][:2] == ("DELETE", "/api2/json/nodes/pve-a/lxc/101")
    assert upid == "UPID:delete"


async def test_console_ticket_uses_websocket_proxy_endpoint_without_exposing_ticket() -> None:
    captured: tuple[str, str, dict[str, list[str]]] | None = None

    def success(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = (
            request.method,
            request.url.raw_path.decode(),
            parse_qs(request.content.decode()),
        )
        return httpx.Response(
            200,
            json={"data": {"port": "5900", "ticket": "PVEVNC:short-lived-ticket"}},
        )

    async with make_client(success) as client:
        ticket = await client.create_console_proxy(kind="QEMU", node="pve node", vmid=141)

    assert captured == (
        "POST",
        "/api2/json/nodes/pve%20node/qemu/141/vncproxy",
        {"websocket": ["1"]},
    )
    assert ticket.port == 5900
    assert ticket.kind == "qemu"


async def test_lxc_console_ticket_uses_termproxy_endpoint() -> None:
    captured: tuple[str, str, dict[str, list[str]]] | None = None

    def success(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = (
            request.method,
            request.url.raw_path.decode(),
            parse_qs(request.content.decode()),
        )
        return httpx.Response(
            200,
            json={
                "data": {
                    "port": "5901",
                    "ticket": "PVEVNC:lxc-terminal-ticket",
                    "user": "service@pve!pvemaster",
                }
            },
        )

    async with make_client(success) as client:
        ticket = await client.create_console_proxy(kind="LXC", node="pve node", vmid=202)

    assert captured == (
        "POST",
        "/api2/json/nodes/pve%20node/lxc/202/termproxy",
        {},
    )
    assert ticket.port == 5901
    assert ticket.kind == "lxc"
    assert ticket.user == "service@pve!pvemaster"


async def test_lxc_console_ticket_requires_proxy_user() -> None:
    async with make_client(
        lambda _: httpx.Response(
            200,
            json={"data": {"port": 5901, "ticket": "PVEVNC:lxc-terminal-ticket"}},
        )
    ) as client:
        with pytest.raises(AppError) as error:
            await client.create_console_proxy(kind="LXC", node="pve-a", vmid=202)

    assert error.value.code == "PVE_INVALID_RESPONSE"


@pytest.mark.parametrize(
    "payload",
    [
        {"port": None, "ticket": "ticket"},
        {"port": 0, "ticket": "ticket"},
        {"port": 5900, "ticket": ""},
    ],
)
async def test_invalid_console_ticket_is_rejected(payload: dict[str, object]) -> None:
    async with make_client(lambda _: httpx.Response(200, json={"data": payload})) as client:
        with pytest.raises(AppError) as error:
            await client.create_console_proxy(kind="QEMU", node="pve-a", vmid=141)

    assert error.value.code == "PVE_INVALID_RESPONSE"
