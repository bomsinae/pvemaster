import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createBackupTarget,
  discoverBackupStorages,
  requestWorkloadBackup,
  requestBackupRestore,
} from "../lib/admin-api.ts";

test("backup API client uses scoped endpoints and an idempotency key", async () => {
  const requests: Array<{ url: string; init: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init = {}) => {
    requests.push({ url: String(input), init });
    if (String(input).endsWith("/backup-storages")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    if (String(input).endsWith("/backup-targets")) {
      return new Response(JSON.stringify({
        id: "target-1",
        cluster_id: "cluster-1",
        cluster_name: "pve-a",
        storage_id: "pbs-main",
        datastore: "main",
        namespace: null,
        is_enabled: true,
        available: true,
        last_checked_at: null,
        created_at: "2026-07-21T00:00:00Z",
        updated_at: "2026-07-21T00:00:00Z",
        version: 1,
      }), { status: 201 });
    }
    return new Response(JSON.stringify({
      id: "run-1",
      operation_id: "operation-1",
      status: "QUEUED",
    }), { status: 202 });
  };

  await discoverBackupStorages("https://example.test", "access", "cluster-1", fetcher);
  await createBackupTarget(
    "https://example.test",
    "access",
    "cluster-1",
    "pbs-main",
    fetcher,
  );
  await requestWorkloadBackup(
    "https://example.test",
    "access",
    "workload-1",
    "target-1",
    "backup-request-1",
    fetcher,
  );
  await requestBackupRestore(
    "https://example.test",
    "access",
    "run-1",
    { target_node: "pve-a", target_vmid: 220, target_name: "service-restored" },
    "restore-request-1",
    fetcher,
  );

  assert.equal(
    requests[0].url,
    "https://example.test/api/v1/admin/clusters/cluster-1/backup-storages",
  );
  assert.deepEqual(JSON.parse(String(requests[1].init.body)), {
    cluster_id: "cluster-1",
    storage_id: "pbs-main",
  });
  assert.equal(
    new Headers(requests[2].init.headers).get("Idempotency-Key"),
    "backup-request-1",
  );
  assert.deepEqual(JSON.parse(String(requests[2].init.body)), {
    backup_target_id: "target-1",
  });
  assert.equal(
    requests[3].url,
    "https://example.test/api/v1/admin/backups/run-1/restores",
  );
  assert.equal(
    new Headers(requests[3].init.headers).get("Idempotency-Key"),
    "restore-request-1",
  );
  assert.deepEqual(JSON.parse(String(requests[3].init.body)), {
    target_node: "pve-a",
    target_vmid: 220,
    target_name: "service-restored",
  });
});

test("backup workspace exposes filters, per-VM history, and transfer measurements", async () => {
  const [dashboard, styles] = await Promise.all([
    readFile(new URL("../app/admin-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(dashboard, /백업 내역 관리/);
  assert.match(dashboard, /신규 전송 데이터/);
  assert.match(dashboard, /기존 데이터 100% 재사용/);
  assert.match(dashboard, /논리 크기/);
  assert.match(dashboard, /VM에서 이동한 내역만 표시 중/);
  assert.match(dashboard, /같은 대상으로 다시 백업/);
  assert.match(dashboard, /새 VM\/CT로 복구/);
  assert.match(dashboard, /기존 VMID는 덮어쓰지 않으며/);
  assert.match(dashboard, /복구 작업/);
  assert.match(dashboard, /최근 백업/);
  assert.match(dashboard, /admin-drawer-backdrop/);
  assert.match(dashboard, /createPortal/);
  assert.match(dashboard, /document\.body\.style\.overflow = "hidden"/);
  assert.match(dashboard, /aria-modal="true"/);
  assert.match(styles, /\.admin-drawer\.backup-run-detail/);
  assert.doesNotMatch(styles, /\.backup-table-scroll\s*\{[\s\S]*?overflow-x/);
});
