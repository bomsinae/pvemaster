# API 설계

## 1. 기본 규약

- Base path: `/api/v1`.
- Content type: `application/json`; UTF-8.
- 필드명: snake_case. 시간: UTC RFC 3339(예: `2026-07-14T09:00:00Z`).
- 리소스 ID: 내부 UUID 문자열. PVE VMID는 속성으로만 노출하고 URL 식별자로 사용하지 않는다.
- 인증: HttpOnly session cookie. 모든 상태 변경 요청은 CSRF header가 필요하다.
- 관리자 API는 `/admin`, 고객 self-service API는 `/customer` 아래에 둔다.
- 모든 요청에 클라이언트가 `X-Request-ID`를 보낼 수 있고 서버는 검증/생성한 값을 응답한다.
- 비동기 작업은 `202 Accepted`와 operation을 반환한다.
- 생성/복제 등 중복 부작용이 큰 요청은 `Idempotency-Key` header가 필수다.
- PVE 원시 응답이나 토큰, 내부 stack trace를 API로 반환하지 않는다.

응답 envelope는 불필요하게 중첩하지 않는다. 단일 리소스는 리소스 객체, 목록은 다음 형식을 사용한다.

```json
{
  "items": [],
  "next_cursor": null
}
```

페이지 크기 기본값은 50, 최대 200이다. cursor는 opaque하며 정렬 키와 ID를 서명/인코딩한다. offset pagination은 대규모 변경 목록에서 사용하지 않는다.

## 2. 인증과 오류

### Token API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| POST | `/auth/login` | 공개 | access token과 refresh token 발급 |
| POST | `/auth/refresh` | refresh token | refresh rotation과 새 access token 발급 |
| POST | `/auth/logout` | refresh token | 해당 token family 폐기 |
| GET | `/auth/me` | 로그인 | 현재 사용자와 역할 조회 |
| POST | `/auth/change-password` | 로그인 | 비밀번호 변경과 전체 token 폐기 |

로그인 요청 예시:

```json
{
  "email": "admin@example.com",
  "password": "<redacted>"
}
```

로그인 실패는 계정 존재/비활성/비밀번호 오류를 구분하지 않는 `401 INVALID_CREDENTIALS`를 반환한다. access token은 Bearer header로 전달하고 refresh token 원문은 DB와 로그에 저장하지 않는다. 회전 전 token 재사용은 token family 전체를 폐기한다.

### 오류 형식

```json
{
  "error": {
    "code": "WORKLOAD_STATE_CONFLICT",
    "message": "요청한 전원 작업을 현재 상태에서 수행할 수 없습니다.",
    "details": {},
    "request_id": "0190..."
  }
}
```

`details`는 필드 validation처럼 안전한 정보만 포함한다. 공통 status:

- `400`: 잘못된 의미/형식, 지원하지 않는 action.
- `401`: 인증 없음/만료.
- `403`: 역할 또는 CSRF/MFA 정책 위반. 고객 객체 소유권 실패에는 사용하지 않는다.
- `404`: 존재하지 않음; 고객에게는 비소유 객체도 동일.
- `409`: 상태 충돌, 중복 할당, 같은 멱등성 키의 다른 body.
- `422`: schema validation.
- `429`: 속도/동시 작업 제한. `Retry-After` 제공.
- `502`: PVE가 유효하지 않은 응답을 반환.
- `503`: PVE/DB/queue 일시 사용 불가 또는 stale 정책상 작업 불가.
- `504`: 제한 시간 내 PVE 응답 없음.

예상 가능한 내부 `error.code`에는 `CLUSTER_UNREACHABLE`, `PVE_AUTH_FAILED`, `PVE_PERMISSION_DENIED`, `WORKLOAD_NOT_FOUND`, `WORKLOAD_STATE_CONFLICT`, `OPERATION_CONFLICT`, `IP_POOL_EXHAUSTED`, `IDEMPOTENCY_KEY_REUSED`, `STALE_INVENTORY`, `MFA_REQUIRED`가 있다.

## 3. 공통 리소스 표현

### Cluster

```json
{
  "id": "0190...",
  "job_id": "0190...",
  "name": "seoul-pve-1",
  "api_base_url": "https://pve.example.internal:8006",
  "is_active": true,
  "connection_status": "AVAILABLE",
  "last_sync_succeeded_at": "2026-07-14T08:59:30Z",
  "inventory_stale": false,
  "credential": {
    "token_identifier": "svc@pve!pvemaster",
    "configured": true,
    "last_used_at": "2026-07-14T08:59:30Z"
  },
  "version": 3
}
```

secret, ciphertext, nonce, key version은 반환하지 않는다. `api_base_url` 공개 범위는 관리자 응답으로 제한한다.

### Workload

```json
{
  "id": "0190...",
  "cluster": {"id": "0190...", "name": "seoul-pve-1"},
  "node": {"id": "0190...", "name": "pve01"},
  "vmid": 101,
  "kind": "QEMU",
  "name": "customer-web-01",
  "is_template": false,
  "power_state": "RUNNING",
  "resources": {
    "cpu_cores": 4,
    "memory_bytes": 8589934592,
    "disk_bytes": 107374182400
  },
  "assigned_ip_addresses": ["192.0.2.24"],
  "observed_at": "2026-07-14T08:59:30Z",
  "is_stale": false,
  "assignment": {
    "customer_user_id": "0190...",
    "assigned_at": "2026-07-01T00:00:00Z"
  },
  "allowed_actions": ["shutdown", "reboot", "stop"]
}
```

고객 응답은 현재 워크로드에 `ASSIGNED` 상태인 IP 주소를 포함하되 다른 고객/관리자 식별자, 내부 PVE 상세, 불필요한 cluster endpoint를 제외한다. `allowed_actions`는 UI 편의를 위한 현재 snapshot이며 서버가 요청 시 다시 검사한다.

### Operation

```json
{
  "id": "0190...",
  "type": "PROVISION_VM",
  "status": "RUNNING",
  "current_step": "CLONING",
  "progress_percent": 35,
  "target": {"workload_id": null, "cluster_id": "0190..."},
  "requested_at": "2026-07-14T09:00:00Z",
  "started_at": "2026-07-14T09:00:01Z",
  "finished_at": null,
  "error": null,
  "links": {"self": "/api/v1/admin/operations/0190..."}
}
```

관리자 operation 상세에는 PVE task의 `step_name`, 상태, submit/completed 시각과 필요 시 UPID를 포함할 수 있다. 고객 응답에는 본인이 요청한 작업의 필요한 상태만 제공하고 UPID 원문은 기본적으로 숨긴다.

## 4. 관리자 사용자 API

| Method | Path | 동작 |
|---|---|---|
| GET | `/admin/users` | role, active, 검색 필터로 사용자 목록 |
| POST | `/admin/users` | 고객/관리자 생성; 관리자 생성은 step-up MFA |
| GET | `/admin/users/{user_id}` | 사용자 상세와 현재 할당 수 |
| PATCH | `/admin/users/{user_id}` | 표시명, 활성 상태, 정책 변경. 비활성화 시 모든 세션 폐기 |
| POST | `/admin/users/{user_id}/reset-password` | 새 비밀번호 설정 및 대상 사용자 전체 세션 폐기 |
| DELETE | `/admin/users/{user_id}` | 계정 삭제. 로그인 식별정보를 익명화하고 조직 소속·세션을 제거하되 감사·작업 참조용 내부 행은 보존 |

생성 body:

```json
{
  "email": "customer@example.com",
  "display_name": "Customer A",
  "role": "CUSTOMER",
  "send_activation": true
}
```

초기 비밀번호를 관리자 API 응답으로 반복 노출하지 않는다. 실제 메일/초대 연동 전 개발 단계에서는 일회성 활성화 token을 별도 안전한 채널로 전달한다.

`PATCH`는 `If-Match` 또는 body의 `version`으로 lost update를 방지한다. 마지막 활성 관리자의 비활성화/강등은 `409 LAST_ADMIN`이다.

## 5. 클러스터 API

| Method | Path | 동기성 | 설명 |
|---|---|---:|---|
| GET | `/admin/clusters` | 동기 | 활성 클러스터의 연결/동기화 상태 목록; 등록 해제된 tombstone 제외 |
| GET | `/admin/clusters/overview` | 동기/짧은 timeout | 모든 활성 클러스터의 실시간 CPU, RAM, 노드 루트 디스크, load average, VM/CT 및 스토리지 집계. 전체 스토리지 필드와 별도로 `images`/`rootdir` 지원 스토리지의 `vm_storage_count`, `vm_storage_used_bytes`, `vm_storage_total_bytes`를 반환한다. 클러스터별 부분 실패는 해당 항목의 `error_code`로 반환 |
| POST | `/admin/clusters` | 동기 | 검증 후 클러스터와 암호화 credential 저장 |
| GET | `/admin/clusters/{cluster_id}` | 동기 | 상세; secret 제외 |
| PATCH | `/admin/clusters/{cluster_id}` | 동기 | 이름, endpoint, sync 설정 변경 |
| DELETE | `/admin/clusters/{cluster_id}` | 동기 | 안전 검사 후 disable/tombstone |
| POST | `/admin/clusters/{cluster_id}/test` | 동기/짧은 timeout | TLS, 인증, 노드/게스트/스토리지 최소 권한 probe (`test-connection` 호환 alias 제공) |
| GET | `/admin/clusters/{cluster_id}/nodes` | 동기 | 해당 PVE 클러스터의 실시간 노드 목록 |
| GET | `/admin/clusters/{cluster_id}/nodes/{node}/metrics?range=hour\|six_hours\|day\|week` | 동기/짧은 timeout | Proxmox RRD 기반 CPU, load, 메모리, 네트워크, CPU·IO·메모리 PSI 시계열. 지원하지 않는 PSI 값은 `null` |
| GET | `/admin/clusters/{cluster_id}/guests` | 동기 | 해당 PVE 클러스터의 실시간 QEMU/LXC 목록. 실행 중 게스트는 `cpu`, `maxcpu`, `mem`, `maxmem`, `disk`, `maxdisk`, `uptime` 현재 사용량과 한도를 포함 |
| GET | `/admin/clusters/{cluster_id}/storages` | 동기 | 해당 PVE 클러스터의 실시간 스토리지 목록 |
| POST | `/admin/clusters/{cluster_id}/sync` | 비동기 | 전체 또는 `workload_id` 대상 인벤토리 동기화 요청. 응답의 `operation_id`는 `sync_runs.id` |
| POST | `/admin/clusters/{cluster_id}/rotate-credential` | 동기 | 새 token 시험 후 활성 전환 |

클러스터 생성 body:

```json
{
  "name": "seoul-pve-1",
  "api_base_url": "https://pve.example.internal:8006",
  "token_identifier": "svc@pve!pvemaster",
  "token_secret": "<write-only>",
  "ca_bundle_id": "approved-ca-01",
  "sync_interval_seconds": 60
}
```

- `token_secret`은 write-only다.
- create 전에 별도 연결 시험을 수행하더라도 저장 시 endpoint/credential을 다시 검증한다.
- URL은 SSRF 정책을 통과해야 하고 redirect를 허용하지 않는다.
- `DELETE`는 활성 operation, 현재 assignment, IP allocation이 있으면 기본적으로 `409`; 명시적 이관/정리 후 disable한다.

연결 시험 응답은 `reachable`, `tls_valid`, `authenticated`, 기능별 `capabilities`와 안전한 오류 코드만 반환한다. PVE 응답 원문이나 secret을 반환하지 않는다.

## 6. 인벤토리 API

| Method | Path | 권한 | 주요 필터 |
|---|---|---|---|
| GET | `/admin/nodes` | ADMIN | `cluster_id`, `status`, `is_present`, cursor |
| GET | `/admin/nodes/{node_id}` | ADMIN | 없음 |
| GET | `/admin/workloads` | ADMIN | `cluster_id`, `node_id`, `kind`, `power_state`, `customer_user_id`, `is_template`, `is_present`, `q` |
| GET | `/admin/workloads/{workload_id}` | ADMIN | 없음 |
| GET | `/customer/vms` | CUSTOMER | 현재 사용자의 활성 조직에 할당된 QEMU VM으로 서버 강제 제한. 각 항목에 안전한 `organization_name`을 포함하고 내부 조직 ID는 노출하지 않음 |
| GET | `/customer/vms/{vm_id}` | CUSTOMER | 현재 사용자의 활성 조직 멤버십 필요. 안전한 `organization_name` 포함 |
| GET | `/admin/inventory/sync-runs` | ADMIN | cluster별 sync 실행, scope, generation, 부분 실패와 변경 건수 |
| GET | `/admin/inventory/sync-runs/{run_id}` | ADMIN | sync run 상세 |
| GET | `/admin/inventory/freshness` | ADMIN | 마지막 전체 성공, stale 기준과 최근 상태 |
| GET | `/admin/inventory/reconciliation/findings` | ADMIN | status, severity, cluster별 drift finding |
| GET | `/admin/inventory/reconciliation/findings/{finding_id}` | ADMIN | finding 상세 |
| POST | `/admin/inventory/reconciliation/findings/{finding_id}/acknowledge` | ADMIN | 확인·담당자 지정 |
| POST | `/admin/inventory/reconciliation/findings/{finding_id}/resolve` | ADMIN | 해결 근거 기록 |
| POST | `/admin/inventory/reconciliation/run` | ADMIN | cluster 전체 재조정 요청 |

정렬 allowlist는 `name`, `vmid`, `observed_at`, `power_state`다. admin 검색에서 VMID는 반드시 `cluster_id`와 함께 식별하거나 결과를 목록으로 취급한다.

응답은 다음 freshness metadata를 포함한다.

```json
{
  "observed_at": "2026-07-14T08:59:30Z",
  "is_stale": false,
  "stale_reason": null
}
```

클러스터 단절 시 마지막 관측 데이터를 `200`으로 줄 수 있으나 `is_stale=true`와 마지막 성공 시각을 명확히 제공한다. 상태 변경 요청은 별도 PVE preflight를 수행한다.
고객 workload가 stale이면 고객 전원 작업은 `503 INVENTORY_STALE`로 제한한다. 고객
응답에는 cluster, node, sync run ID 또는 내부 오류 원문을 포함하지 않는다.

## 7. 할당 API

| Method | Path | 설명 |
|---|---|---|
| POST | `/admin/workloads/{workload_id}/assign` | 미할당 워크로드를 고객에게 할당 |
| DELETE | `/admin/workloads/{workload_id}/assignment` | 현재 할당 회수 |
| GET | `/admin/workloads/{workload_id}/assignments` | 할당/회수 이력 |

할당 body:

```json
{
  "organization_id": "0190..."
}
```

규칙:

- 대상은 활성 조직이어야 하며 고객 접근은 별도 조직 멤버십으로 결정한다.
- PVE에 현재 존재하는 non-template 워크로드만 할당한다.
- 이미 같은 조직에 할당된 요청은 현재 assignment를 `200`으로 반환하고, 다른 조직이면 `409 WORKLOAD_ALREADY_ASSIGNED`다.
- 회수 시 진행 중 고객 operation이 있으면 안전 정책에 따라 `409` 또는 취소 요청 후 회수한다. 기본값은 충돌을 명시하는 `409`다.
- 할당과 회수는 모두 audit에 이전/새 조직 ID를 기록하되 메모의 민감정보를 금지한다.

기존 PVE 리소스와 조직 관리 API:

| Method | Path | 설명 |
|---|---|---|
| POST | `/admin/clusters/{cluster_id}/workloads/import` | 실시간 VM/CT를 로컬 workload로 멱등 upsert |
| GET | `/admin/organizations` | 조직 목록. `q`, `status=active|inactive|all`, `sort=newest|oldest|name`, `limit`, `offset`을 지원하며 기본값은 활성 조직·최근 생성순이라 기존 조회와 호환 |
| PATCH | `/admin/organizations/{organization_id}` | 조직 이름 수정 또는 비활성 조직 재활성화; version 충돌 검사 |
| DELETE | `/admin/organizations/{organization_id}` | 구성원·할당·진행 중 프로비저닝이 없을 때 비활성화. 조직 행과 감사 이력은 보존 |
| GET | `/admin/organizations/{organization_id}/members` | 조직 구성원 조회 |
| POST | `/admin/organizations/{organization_id}/members` | 활성 사용자 추가 |
| DELETE | `/admin/organizations/{organization_id}/members/{user_id}` | 구성원 제거 및 고객 접근 회수 |

## 8. 전원 작업 API

| Method | Path | 권한 |
|---|---|---|
| POST | `/admin/workloads/{workload_id}/actions/{action}` | SUPER_ADMIN, OPERATOR; QEMU/LXC 공통 전원 작업 |
| PATCH | `/admin/vms/{vm_id}/spec` | SUPER_ADMIN; CPU/RAM 변경, 디스크 증설 |
| DELETE | `/admin/vms/{vm_id}` | SUPER_ADMIN; 중지·미할당 VM/CT만 비동기 삭제 |
| GET | `/jobs/{job_id}` | SUPER_ADMIN, OPERATOR |
| POST | `/customer/vms/{vm_id}/actions/start` | CUSTOMER + 현재 조직 소유 |
| POST | `/customer/vms/{vm_id}/actions/shutdown` | CUSTOMER + 현재 조직 소유 |
| POST | `/customer/vms/{vm_id}/actions/stop` | CUSTOMER + 현재 조직 소유 + 명시적 강제 작업 확인 |
| POST | `/customer/vms/{vm_id}/actions/reboot` | CUSTOMER + 현재 조직 소유 |
| GET | `/customer/jobs/{job_id}` | 요청 CUSTOMER + 현재 조직 소유 |

관리자 QEMU `action` allowlist는 `start`, `shutdown`, `stop`, `reboot`, `reset`이며, LXC allowlist는 `start`, `shutdown`, `stop`, `reboot`다. LXC에는 `reset`을 허용하지 않는다. 고객 포털 allowlist는 QEMU의 `start`, `shutdown`, `stop`, `reboot`다. 고객 `stop`은 `confirm_forced=true`가 없으면 `422 FORCED_ACTION_CONFIRMATION_REQUIRED`로 거부하며 `reset`은 route 자체를 제공하지 않는다. 고객 조회와 작업 접수는 관리자 조회 함수를 재사용하지 않고 사용자-조직 멤버십과 VM 조직을 같은 DB predicate에서 검증한다.

기존 `/admin/vms/{vm_id}/actions/{action}` 개별 경로는 이전 클라이언트 호환을 위해 deprecated alias로 유지한다. 신규 관리자 클라이언트는 workload 공통 경로를 사용한다. 작업 응답은 canonical `workload_id`와 호환용 `vm_id`를 함께 반환한다.

### 콘솔 API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| POST | `/admin/workloads/{workload_id}/console-sessions` | SUPER_ADMIN, OPERATOR | 실행 중인 QEMU/LXC용 일회성 콘솔 세션 생성 |
| POST | `/customer/vms/{workload_id}/console-sessions` | CUSTOMER + 현재 조직 소유 | 할당된 실행 중 VM용 일회성 콘솔 세션 생성 |
| WebSocket | `/console/ws/{session_id}` | 일회성 WebSocket subprotocol token | QEMU noVNC 또는 LXC termproxy 프레임 중계 |

콘솔 세션 응답은 `console_type=NOVNC|TERMINAL`과 30초 안에 한 번만 사용할 수 있는 protocol token을 `Cache-Control: no-store`로 반환한다. QEMU에서만 PVE가 발급한 단기 RFB password를 반환하며 브라우저는 이를 noVNC 메모리에서만 사용하고 URL, 쿠키, 로컬 저장소에 넣지 않는다. LXC의 termproxy 티켓과 PVE 사용자 식별자는 Redis의 일회용 세션에만 저장하고 브라우저에 반환하지 않는다. `binary`와 `pvemaster.console.<token>` WebSocket subprotocol을 함께 요청하고 서버는 token을 원자적으로 소비한다. 연결 시 사용자 활성 상태, session epoch, 역할, workload 종류·존재 및 실행 상태를 다시 검사한다. 고객은 활성 조직 멤버십과 현재 workload 소유권도 다시 검사하며, 비소유·회수·비활성 조직은 모두 `404`로 처리한다. PVE API token과 PVE endpoint는 브라우저에 포함하지 않는다. 사용자와 workload 조합별 활성 콘솔은 한 개이며, 같은 사용자가 서로 다른 워크로드 콘솔을 동시에 열 수 있다. 연결 시간은 기본 60분으로 제한한다.

관리자와 고객 UI는 `/console/{workload_id}`를 별도 브라우저 창으로 열고, 해당 창에서 HttpOnly 갱신 세션을 사용해 역할별 권한과 workload를 다시 조회한 뒤 해당 역할의 콘솔 세션을 만든다. QEMU는 noVNC 화면, LXC는 xterm.js 터미널을 표시한다. 새 창 URL에는 workload ID만 포함한다. 팝업이 차단되면 동일한 흐름을 현재 화면의 오버레이에서 실행한다.

요청:

```http
POST /api/v1/admin/workloads/0190.../actions/shutdown
Idempotency-Key: 7f9c...
```

```json
{
  "reason": "scheduled maintenance"
}
```

성공 접수:

```http
HTTP/1.1 202 Accepted
Location: /api/v1/jobs/0190...
Retry-After: 2
```

```json
{
  "id": "0190...",
  "workload_id": "0190...",
  "vm_id": "0190...",
  "action": "shutdown",
  "action_mode": "GRACEFUL",
  "status": "QUEUED",
  "result": {
    "workload_kind": "LXC"
  },
  "pve_upid": null
}
```

처리 순서:

1. 역할/현재 assignment를 한 쿼리에서 확인한다.
2. 동일 대상의 충돌 operation과 rate limit을 확인한다.
3. operation/outbox/audit attempt를 DB transaction으로 생성한다.
4. 워커가 역할/assignment를 재확인하고 PVE 상태를 조회한다.
5. PVE 작업을 제출하고 UPID를 저장/추적한다.
6. 최종 PVE 상태를 조회해 operation과 audit outcome을 확정한다.

이미 원하는 상태인 `start`/`shutdown`/`stop`은 성공 no-op으로 끝내고 `result.no_op=true`를 기록한다. `shutdown`은 `action_mode=GRACEFUL`, SIGKILL에 해당하는 `stop`은 `action_mode=FORCED`로 API와 UI에서 구분하고 UI는 별도 위험 확인을 요구한다. 모든 요청은 `Idempotency-Key`가 필수다.

worker는 UPID가 저장된 작업을 재시작할 때 제출을 반복하지 않고 polling부터 재개한다. 제출 timeout은 결과 불명확 상태이므로 자동 재제출하지 않는다. polling의 일시적 timeout/5xx만 설정된 총 시간과 횟수 안에서 재시도하며 terminal 상태는 `SUCCEEDED`, `FAILED`, `TIMEOUT` 중 하나다.

## 9. 템플릿 API

| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/templates` | 사용 가능/누락/stale 템플릿 목록 |
| POST | `/admin/templates` | 동기화된 PVE template workload를 프로비저닝 템플릿으로 등록 |
| GET | `/admin/templates/{template_id}` | 기본/최소/최대 사양 상세 |
| PATCH | `/admin/templates/{template_id}` | 설명, 사양 제한, 네트워크 기본값 변경 |
| DELETE | `/admin/templates/{template_id}` | 플랫폼 등록 해제; PVE template은 삭제하지 않음 |

등록 body는 `source_workload_id`, 이름, 기본/최소/최대 CPU·메모리·디스크, storage/bridge/VLAN 정책을 받는다. PVE에서 `QEMU` 및 `is_template=true`인지 확인한다.

## 10. VM 프로비저닝 API

| Method | Path | 설명 |
|---|---|---|
| POST | `/admin/products` | CPU/RAM/disk 고정 상품 생성 |
| GET | `/admin/products` | 활성 상태를 포함한 상품 목록 |
| PATCH | `/admin/products/{product_id}` | 상품 이름, 고정 사양, 활성 상태 변경 |
| DELETE | `/admin/products/{product_id}` | 미사용 상품 삭제; 프로비저닝 이력이 참조하면 거부 |
| PUT | `/admin/provisioning-nodes` | 노드 enabled/maintenance/가용 용량 등록 또는 갱신 |
| POST | `/admin/provision-requests` | 비동기 VM 생성 요청 |
| GET | `/admin/provision-requests` | 상태/cluster/customer/기간 필터 목록 |
| GET | `/admin/provision-requests/{request_id}` | 단계, operation, 결과 workload 조회 |
| POST | `/admin/provision-requests/{request_id}/cancel` | best-effort 취소 요청 |

생성에는 `Idempotency-Key`가 필수다.

```json
{
  "template_id": "0190...",
  "target": {
    "cluster_id": "0190...",
    "node_id": null,
    "vmid": null,
    "name": "customer-web-01",
    "storage": "local-lvm"
  },
  "resources": {
    "cpu_cores": 4,
    "memory_bytes": 8589934592,
    "disk_bytes": 107374182400
  },
  "cloud_init": {
    "username": "clouduser",
    "ssh_public_keys": ["ssh-ed25519 AAAA..."],
    "dns_search_domain": "customer.example"
  },
  "network": {
    "ip_pool_id": "0190...",
    "ip_address": null,
    "bridge": null,
    "vlan_tag": null
  },
  "customer_user_id": "0190...",
  "start_after_create": true
}
```

검증:

- 템플릿과 target cluster 호환성, node/storage 존재와 stale 여부.
- 사양 제한/할당량(후속 정책), VMID 가용성, 이름 문자/길이.
- IP pool의 cluster/bridge/VLAN 호환성과 주소 가용성.
- customer가 활성 CUSTOMER인지 확인.
- private key, 평문 비밀번호, 임의 Cloud-Init script 필드는 schema에서 거부.

`vmid=null`, `node_id=null`이면 서버가 할당 정책에 따라 선택하고 요청 snapshot에 확정값을 기록한다.

동일 사용자/동일 `Idempotency-Key`와 같은 body는 기존 request/operation을 반환한다. body가 다르면 `409 IDEMPOTENCY_KEY_REUSED`다. 키는 최소 24시간 또는 operation 보존 기간 동안 유지한다.

취소 응답도 `202`다. 이미 terminal이면 현재 상태를 반환하고, PVE 단계가 취소 불가능하면 `CANCEL_REQUESTED` 후 최종 실제 결과를 보고한다. 자동 정리가 안전하지 않은 실패는 `NEEDS_ATTENTION`이다.

현재 구현은 Linux QEMU Cloud-Init template의 full clone만 허용한다. 요청 schema에는 평문 비밀번호와 임의 Cloud-Init script 필드가 없으며 알 수 없는 필드는 거부한다. VM 이름, Linux 사용자명, SSH 공개키, static IPv4는 형식 검증 후 저장된다. clone을 시도한 뒤의 실패는 생성 VM을 자동 삭제하지 않고 `MANUAL_REVIEW`로 전환한다. clone 전 실패는 예약 IP를 `QUARANTINED`로 전환하며 rollback 자체가 실패하면 역시 `MANUAL_REVIEW`가 된다.

## 11. IPAM API

| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/ip-pools` | 사용량/고갈/stale 상태 목록 |
| POST | `/admin/ip-pools` | CIDR과 네트워크 정책 생성 |
| GET | `/admin/ip-pools/{pool_id}` | 상세와 사용량 |
| PATCH | `/admin/ip-pools/{pool_id}` | gateway/DNS/정책 변경; CIDR 축소는 안전 검사 |
| DELETE | `/admin/ip-pools/{pool_id}` | 활성 할당이 없을 때 disable/삭제 |
| GET | `/admin/ip-pools/{pool_id}/addresses` | state/address/workload 필터 목록 |
| POST | `/admin/ip-pools/{pool_id}/reservations` | 특정 주소 수동 예약 |
| POST | `/admin/ip-pools/{pool_id}/allocations` | 주소 생략 시 자동, 지정 시 수동으로 VM에 할당 |
| DELETE | `/admin/ip-allocations/{allocation_id}` | 검증 후 해제/격리 전환 |
| POST | `/admin/ip-addresses/{address_id}/approve-release` | 격리 만료 후 관리자 검토로 AVAILABLE 전환 |

풀 생성 예시:

```json
{
  "name": "customer-vlan-120",
  "cluster_id": "0190...",
  "cidr": "192.0.2.0/24",
  "gateway": "192.0.2.1",
  "dns_servers": ["192.0.2.53"],
  "bridge": "vmbr0",
  "vlan_tag": 120,
  "excluded_ranges": [
    {"start": "192.0.2.1", "end": "192.0.2.20"}
  ],
  "allocation_strategy": "SEQUENTIAL"
}
```

API의 `available_count`는 sparse IPv6 pool에서 추정/계산 비용이 클 수 있으므로 `allocated_count`, `quarantined_count`, `availability_status`를 분리한다. 자동 할당은 네트워크 주소, IPv4 broadcast, gateway, 제외 범위와 `DISABLED` 주소를 건너뛴다. IP 해제는 즉시 `AVAILABLE`이 아니라 `QUARANTINED`로 전환하며, 격리 만료 후에도 관리자 승인 API가 호출되어야 재사용된다. VM이 삭제 또는 동기화에서 사라져도 할당 상태는 자동 변경하지 않는다.

## 11.1 PBS 백업 API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/admin/clusters/{cluster_id}/backup-storages` | SUPER_ADMIN/OPERATOR | PVE에 등록된 PBS storage 검색 |
| GET | `/admin/backup-targets` | SUPER_ADMIN/OPERATOR | 등록된 백업 대상 목록 |
| POST | `/admin/backup-targets` | SUPER_ADMIN | 검색된 PBS storage를 대상에 등록 |
| PATCH | `/admin/backup-targets/{target_id}` | SUPER_ADMIN | 대상 활성/비활성 전환 |
| POST | `/admin/workloads/{workload_id}/backups` | SUPER_ADMIN/OPERATOR | 수동 snapshot 백업 접수 |
| GET | `/admin/backups` | SUPER_ADMIN/OPERATOR | 백업 실행 내역 검색 |
| GET | `/admin/backups/{backup_run_id}` | SUPER_ADMIN/OPERATOR | 백업 실행 상태와 snapshot 메타데이터 |
| POST | `/admin/backups/{backup_run_id}/restores` | SUPER_ADMIN | 성공 스냅샷을 새 VM/CT로 복구 |
| GET | `/admin/restores/{restore_run_id}` | SUPER_ADMIN | 복구 실행과 PVE 작업 상태 조회 |

백업 실행 응답의 `size_bytes`는 논리 백업 크기이고 `transferred_bytes`는 이번 실행에서 PBS로 새로 전송된 데이터 양이다. 기존 실행이나 PVE 작업 로그에 측정 정보가 없으면 `transferred_bytes`는 `null`이다.

수동 백업은 `Idempotency-Key`가 필요하고 `202 Accepted`를 반환한다. 대상 storage는
workload와 같은 cluster여야 하며 초기 지원 option은 `mode=snapshot`,
`compression=zstd`로 제한한다. 고객 API, 삭제와 prune은 제공하지 않는다. 복구는 새 VMID,
대상 node와 이름을 필수로 받고 기존 VMID 덮어쓰기와 `force` 옵션을 허용하지 않는다.

## 12. Operation API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/admin/operations` | ADMIN | type/status/cluster/workload/requester/기간 필터 |
| GET | `/admin/operations/{operation_id}` | ADMIN | 단계와 PVE task 포함 상세 |
| POST | `/admin/operations/{operation_id}/cancel` | ADMIN | version 일치와 queued 상태에서만 취소 |
| POST | `/admin/operations/{operation_id}/retry` | ADMIN | 안전 판정 후 원본과 연결된 새 작업 생성 |
| POST | `/admin/operations/{operation_id}/assign` | ADMIN | 활성 관리자 담당자 지정 |
| POST | `/admin/operations/{operation_id}/acknowledge` | ADMIN | 운영자 확인 시각 기록 |
| POST | `/admin/operations/{operation_id}/resolve-manually` | ADMIN | 검증한 해결 근거 기록 |
| GET | `/customer/jobs` | 요청 CUSTOMER + 대상 현재 소유 | 새로고침 복구용 최근 작업 목록 |
| GET | `/customer/jobs/{job_id}` | 요청 CUSTOMER + 대상 현재 소유 | 자신의 허용 작업 상태 |

operation은 immutable request identity를 가지며 terminal status를 되돌리지 않는다. `Retry-After`는 poll 권고 간격이다. 기본 polling은 실행 중 2초에서 시작해 backoff하고 terminal에서 중단한다.

고객의 operation 상세 접근 조건:

- `requested_by_id == current_user.id`.
- 대상 워크로드 조직의 현재 멤버이며 사용자와 조직이 모두 활성 상태임.
- operation type이 고객 허용 목록에 있음.

조건 실패는 `404`다. operation에 저장된 요청 당시 조직과 workload의 현재 조직도
일치해야 한다. VM 상세의 최근 작업은 현재 사용자가 해당 VM에 요청한 최근 10개만
포함하며, 조직에서 제거되거나 workload가 재할당되는 즉시 VM과 작업 모두 조회할 수
없다. 고객 응답에는 PVE UPID, cluster/node, API endpoint와 token 정보를 포함하지
않는다.

## 13. 감사 API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/admin/audit-logs` | SUPER_ADMIN | append-only 감사 사건 검색 |
| GET | `/admin/audit-logs/{audit_id}` | SUPER_ADMIN | 단일 사건 상세 |

현재 필터: `actor_user_id`, `organization_id`, `action`, `result`, `limit`, `offset`.

- 기본 정렬은 `created_at DESC, id DESC`.
- 응답은 redacted metadata만 포함한다.
- 대량 export는 별도 비동기 operation, step-up MFA, 만료되는 다운로드 URL과 감사 기록이 필요하므로 초기 API에서 제외한다.
- audit API 자체 조회도 감사한다.

## 14. 상태 확인 API

| Method | Path | 공개 범위 | 설명 |
|---|---|---|---|
| GET | `/health/live` | 내부/프록시 | 프로세스 event loop 생존 여부만 |
| GET | `/health/ready` | 내부/오케스트레이터 | DB, 필수 migration, queue publish 가능 여부 |
| GET | `/admin/operations/status` | SUPER_ADMIN/OPERATOR | worker, 목적별 queue, scheduler 최근 실행·성공·실패, VM/CT 할당, 활성·전체 사용자와 조직 수, cluster 연결과 활성 경보 |
| GET | `/metrics` | 내부/Prometheus | worker, queue, scheduler 성공 시각·실패, cluster, operation, IP pool 지표 |

PVE 개별 클러스터 장애는 전체 API readiness를 실패시키지 않고 관리자 상태/지표로 표시한다. 응답에 DSN, host 상세, version fingerprint 같은 공격 유용 정보를 노출하지 않는다.

## 15. 동시성, 멱등성, 캐시

- 상태 변경 리소스의 `PATCH`에는 `If-Match`/version을 사용하고 충돌은 `409` 또는 `412`로 일관되게 처리한다.
- `Idempotency-Key`는 고엔트로피 문자열, 사용자 scope, endpoint/action, 정규화 body fingerprint에 결합한다.
- 진행 중 같은 워크로드의 충돌 작업은 `409 OPERATION_CONFLICT`와 기존 `operation_id`를 안전한 범위에서 반환한다.
- 인벤토리 GET은 짧은 private cache를 사용할 수 있지만 사용자/역할/assignment에 따라 `Vary`하고 고객 응답을 shared cache에 저장하지 않는다.
- 상태 변경 응답은 `Cache-Control: no-store`; 인증/감사/operation 상세도 no-store다.
- 목록 응답은 관측 시각을 포함하고 read-your-writes가 필요하면 operation 완료 후 해당 리소스를 PVE에서 재조회한다.

## 16. 속도 및 사용 제한 기본값

정확한 수치는 부하 시험에서 조정하되 다음 차원을 분리한다.

- 로그인: 계정 + source prefix 기준의 짧은 burst와 점진적 지연.
- 고객 전원 작업: 사용자 및 workload 기준 동시 1개, 분당 제한.
- 관리자 프로비저닝: 관리자/cluster별 동시성 및 queue depth 제한.
- PVE adapter: cluster별 요청 동시성/초당 제한.
- 목록/감사 검색: 사용자별 rate 및 최대 기간/페이지 크기 제한.

제한 초과는 `429`, `Retry-After`, 감사/보안 지표를 남긴다. Redis 장애 시 고위험 작업은 제한을 우회해 허용하지 않고 보수적으로 거부하거나 DB 기반 동시성 제약을 유지한다.

## 17. API 출시 완료 조건

- OpenAPI가 실제 route/권한/enum/오류와 일치하고 secret 필드는 response schema에 존재하지 않는다.
- 모든 고객 workload/operation endpoint가 소유/비소유/과거 소유/없는 UUID 테스트를 통과한다.
- 비동기 endpoint가 일관되게 `202`, `Location`, operation body를 반환한다.
- 같은 멱등성 키의 재전송이 중복 PVE 작업을 만들지 않는다.
- 동일 VMID가 다른 cluster에 있을 때 UUID 기반 route가 정확한 대상을 선택한다.
- stale 인벤토리와 PVE 연결 실패가 성공 상태처럼 표현되지 않는다.
- 모든 상태 변경의 접수/거부/최종 결과가 감사 로그와 request/operation ID로 연결된다.

## 18. MFA, Session과 step-up API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| POST | `/auth/mfa/challenges/verify` | 로그인 challenge | TOTP/WebAuthn/복구 코드 검증 후 token 발급 |
| GET | `/auth/mfa/methods` | 인증 사용자 | method, 복구 코드 잔여량, 정책 준수 조회 |
| POST | `/auth/mfa/totp/start`, `/auth/mfa/totp/verify` | 인증 사용자 | TOTP 등록 시작·검증 |
| POST | `/auth/mfa/webauthn/start`, `/auth/mfa/webauthn/finish` | 인증 사용자 | WebAuthn 등록 |
| POST | `/auth/mfa/recovery-codes` | 인증 사용자 + 재인증 | 복구 코드 재발급 |
| POST | `/auth/step-up/start`, `/auth/step-up/verify` | 인증 사용자 | action-bound step-up token 발급 |
| GET/DELETE | `/auth/sessions`, `/auth/sessions/{family_id}` | 본인 | 활성 session 조회·폐기 |
| DELETE | `/auth/sessions/others` | 본인 | 현재를 제외한 session 폐기 |
| GET | `/auth/login-events` | 본인 | 최근 로그인 성공·실패 |
| GET/PUT | `/auth/mfa/policy` | SUPER_ADMIN | 관리자 MFA 정책 조회·변경 |

보호 API는 `403 STEP_UP_REQUIRED`의 `details.action`을 반환한다. 클라이언트는
해당 action으로 발급한 token을 `X-Step-Up-Token`에 넣어 원 요청을 한 번만
  재시도한다. 다른 action, 사용자, epoch 또는 만료된 token은 동일하게 거부한다.

## 19. Alert, maintenance와 notification API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/admin/alerts`, `/admin/alerts/{id}` | ADMIN | 지속 경보와 event 이력 |
| POST | `/admin/alerts/{id}/{action}` | ADMIN | acknowledge/assign/silence/resolve |
| GET | `/customer/alerts`, `/customer/alerts/{id}` | CUSTOMER + 현재 조직 | 허용된 조직/VM 경보 |
| GET/POST/PUT/DELETE | `/admin/maintenance-windows` | ADMIN | 전달 suppress 기간 |
| GET/POST/PUT/DELETE | `/admin/notification-channels` | ADMIN | 암호화 channel 관리 |
| POST | `/admin/notification-channels/{id}/test` | ADMIN | 실제 test delivery |
| GET/POST/PUT/DELETE | `/admin/notification-rules` | ADMIN | event/severity/quiet/escalation 규칙 |

Alert action은 body의 `version`이 현재 값과 다르면 `409 ALERT_VERSION_CONFLICT`다.
Channel 응답 schema에는 endpoint, recipient, secret이 존재하지 않는다.

## 20. 자동 백업 정책과 복구 검증 API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET/POST | `/admin/backup-policies` | ADMIN / SUPER_ADMIN | 정책 목록·생성 |
| GET/PUT/DELETE | `/admin/backup-policies/{id}` | ADMIN / SUPER_ADMIN | 정책 조회·변경·삭제 |
| GET | `/admin/backup-policies/{id}/preview` | ADMIN | 현재 인벤토리 기준 적용 대상과 제외 사유 |
| POST | `/admin/backup-policies/{id}/run-now` | ADMIN | 정책 범위를 즉시 실행 |
| POST | `/admin/backup-policies/{id}/skip` | SUPER_ADMIN | version을 검사해 다음 1회 실행 건너뛰기 |
| POST | `/admin/backup-metadata/reconcile` | ADMIN | 성공 run의 누락 snapshot metadata 보정 |
| GET | `/admin/backup-verifications` | ADMIN | metadata·restore drill 검증 이력 |
| POST | `/admin/backups/{id}/verifications` | SUPER_ADMIN | metadata 검증 또는 격리 restore drill 요청 |

정책 변경과 skip에는 action-bound step-up MFA를 적용한다. Schedule은 5-field cron과
IANA timezone을 함께 저장하며 API 응답은 UTC `next_run_at`을 반환한다. 고객 backup
API는 제품 정책상 계속 제공하지 않는다.

## 21. 고객 VM 이력과 성능 지표 API

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/customer/vms/{vm_id}` | CUSTOMER + 현재 조직/할당 | 사양, uptime, 최근 본인 작업, 상태 변화, 백업 상태와 유지보수 |
| GET | `/customer/vms/{vm_id}/metrics` | CUSTOMER + 현재 조직/할당 | `day`, `month`, `year` 범위의 안전한 집계 지표 |
| GET | `/customer/jobs` | CUSTOMER + 현재 조직/할당 + 요청자 | 페이지네이션과 VM·상태·기간 필터 |
| GET | `/customer/jobs/{job_id}` | CUSTOMER + 현재 조직/할당 + 요청자 | 고객 작업 상세 |

작업 목록은 `limit` 1–100, `offset` 0–100000을 허용하고 최대 조회 기간은 365일이다.
시간 파라미터는 timezone을 포함해야 한다. metric 해상도는 24시간 1분, 30일 5분,
365일 1시간이며 응답은 최대 10000개 점으로 제한한다. 모든 조회는 현재 활성
멤버십, 현재 workload 조직과 미회수 assignment를 같은 DB query 경계에서 다시
검사한다. assignment 시작 전 또는 다른 조직 snapshot의 metric과 operation은
반환하지 않는다.

고객 응답에는 조직 UUID, cluster/node, VMID, PVE UPID·endpoint와 내부 오류 원문이
없다. metric 응답은 누락 값을 `null`, 예상 구간 대비 부족 여부를 `partial`로
표현하고 shared cache를 사용하지 않는다.
