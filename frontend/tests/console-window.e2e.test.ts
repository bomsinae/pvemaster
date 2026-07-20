import assert from "node:assert/strict";
import test from "node:test";

import {
  consoleWindowName,
  consoleWindowUrl,
  openConsoleWindow,
  type ConsoleWindowHandle,
} from "../lib/console-window.ts";
import { createConsoleSession } from "../lib/console-api.ts";
import {
  consumeTerminalHandshake,
  terminalInputFrame,
  terminalPingFrame,
  terminalResizeFrame,
} from "../lib/terminal-protocol.ts";

test("console window URL contains only the encoded workload identifier", () => {
  assert.equal(consoleWindowUrl("vm/id?ticket=secret"), "/console/vm%2Fid%3Fticket%3Dsecret");
});

test("console window uses a stable sanitized name", () => {
  assert.equal(consoleWindowName("vm/id:101"), "pvemaster-console-vm-id-101");
});

test("console window focuses the popup and removes its opener", () => {
  let focused = false;
  const handle: ConsoleWindowHandle = {
    opener: {},
    focus: () => { focused = true; },
  };
  const opened = openConsoleWindow("workload-1", (url, target, features) => {
    assert.equal(url, "/console/workload-1");
    assert.equal(target, "pvemaster-console-workload-1");
    assert.match(features, /width=1280/);
    assert.match(features, /resizable=yes/);
    return handle;
  });

  assert.equal(opened, true);
  assert.equal(handle.opener, null);
  assert.equal(focused, true);
});

test("console window reports a blocked popup", () => {
  assert.equal(openConsoleWindow("workload-1", () => null), false);
});

test("CT console session preserves the terminal discriminator without exposing a PVE ticket", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    return new Response(JSON.stringify({
      session_id: "console-session-1",
      websocket_path: "/api/v1/console/ws/console-session-1",
      protocol_token: "a".repeat(43),
      console_type: "TERMINAL",
      rfb_password: null,
      expires_in: 30,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  const session = await createConsoleSession(
    "https://master.example.test",
    "admin-access",
    "ct/workload",
    fetcher,
  );

  assert.equal(session.console_type, "TERMINAL");
  assert.equal(session.rfb_password, null);
  assert.equal(requests[0].url, "https://master.example.test/api/v1/admin/workloads/ct%2Fworkload/console-sessions");
  assert.equal(new Headers(requests[0].init?.headers).get("Authorization"), "Bearer admin-access");
});

test("customer console session uses the ownership-scoped endpoint", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async function (this: void, input, init) {
    assert.equal(this, undefined);
    requests.push({ url: String(input), init });
    return new Response(JSON.stringify({
      session_id: "customer-console-session",
      websocket_path: "/api/v1/console/ws/customer-console-session",
      protocol_token: "b".repeat(43),
      console_type: "NOVNC",
      rfb_password: "short-lived-rfb-password",
      expires_in: 30,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  await createConsoleSession(
    "https://master.example.test",
    "customer-access",
    "customer/workload",
    { scope: "customer", fetcher },
  );

  assert.equal(requests[0].url, "https://master.example.test/api/v1/customer/vms/customer%2Fworkload/console-sessions");
  assert.equal(new Headers(requests[0].init?.headers).get("Authorization"), "Bearer customer-access");
});

test("CT terminal frames follow the Proxmox termproxy protocol", () => {
  assert.equal(terminalInputFrame("가\n"), "0:4:가\n");
  assert.equal(terminalResizeFrame(120, 40), "1:120:40:");
  assert.equal(terminalPingFrame(), "2");
  assert.deepEqual(
    consumeTerminalHandshake(new Uint8Array([79, 75, 27, 91, 72])),
    new Uint8Array([27, 91, 72]),
  );
  assert.equal(consumeTerminalHandshake(new Uint8Array([69, 82, 82])), null);
});
