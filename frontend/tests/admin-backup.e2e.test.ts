import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createBackupTarget,
  createBackupPolicy,
  discoverBackupStorages,
  reconcileBackupMetadata,
  requestBackupMetadataVerification,
  requestWorkloadBackup,
  requestBackupRestore,
  runBackupPolicyNow,
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

test("backup automation client preserves policy and verification contracts", async () => {
  const requests: Array<{ url: string; init: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init = {}) => {
    requests.push({ url: String(input), init });
    if (String(input).endsWith("/backup-policies")) {
      return new Response(JSON.stringify({
        id: "policy-1",
        name: "daily",
        backup_target_id: "target-1",
        backup_target_name: "pbs-main",
        schedule: "0 2 * * *",
        timezone: "Asia/Seoul",
        mode: "snapshot",
        retention_reference: "daily-30",
        verification_interval_days: 90,
        is_enabled: true,
        next_run_at: "2026-07-27T17:00:00Z",
        last_dispatched_at: null,
        skip_next_at: null,
        recent_success_at: null,
        consecutive_failures: 0,
        assignments: [],
        created_at: "2026-07-26T00:00:00Z",
        updated_at: "2026-07-26T00:00:00Z",
        version: 1,
      }), { status: 201 });
    }
    return new Response(JSON.stringify({ dispatched_count: 1, processed_count: 0 }), {
      status: 202,
    });
  };

  await createBackupPolicy("https://example.test", "access", {
    name: "daily",
    backup_target_id: "target-1",
    schedule: "0 2 * * *",
    timezone: "Asia/Seoul",
    retention_reference: "daily-30",
    verification_interval_days: 90,
    assignments: [{ organization_id: "org-1" }],
  }, fetcher);
  await runBackupPolicyNow(
    "https://example.test",
    "access",
    "policy-1",
    fetcher,
  );
  await reconcileBackupMetadata("https://example.test", "access", fetcher);
  await requestBackupMetadataVerification(
    "https://example.test",
    "access",
    "run-1",
    "verification-request-1",
    fetcher,
  );

  assert.deepEqual(JSON.parse(String(requests[0].init.body)), {
    name: "daily",
    backup_target_id: "target-1",
    schedule: "0 2 * * *",
    timezone: "Asia/Seoul",
    retention_reference: "daily-30",
    verification_interval_days: 90,
    assignments: [{ organization_id: "org-1" }],
  });
  assert.equal(requests[1].url, "https://example.test/api/v1/admin/backup-policies/policy-1/run-now");
  assert.equal(requests[2].url, "https://example.test/api/v1/admin/backup-metadata/reconcile");
  assert.equal(
    new Headers(requests[3].init.headers).get("Idempotency-Key"),
    "verification-request-1",
  );
  assert.deepEqual(JSON.parse(String(requests[3].init.body)), {
    verification_type: "METADATA",
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
  assert.match(dashboard, /자동 백업 정책/);
  assert.match(dashboard, /다음 실행 건너뛰기/);
  assert.match(dashboard, /복구 검증/);
  assert.match(dashboard, /메타데이터 재조정/);
  assert.match(dashboard, /복구 영향 미리보기/);
  assert.match(dashboard, /자동 할당 없음/);
  assert.match(dashboard, /admin-drawer-backdrop/);
  assert.match(dashboard, /createPortal/);
  assert.match(dashboard, /document\.body\.style\.overflow = "hidden"/);
  assert.match(dashboard, /aria-modal="true"/);
  assert.match(styles, /\.admin-drawer\.backup-run-detail/);
  assert.doesNotMatch(styles, /\.backup-table-scroll\s*\{[\s\S]*?overflow-x/);
});
