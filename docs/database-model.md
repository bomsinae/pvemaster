# 데이터베이스 모델

## 1. 모델링 원칙

- PostgreSQL을 업무 데이터의 기준으로 사용하고 SQLAlchemy 2의 명시적 typed mapping을 사용한다.
- 모든 시간은 `TIMESTAMPTZ` UTC, 외부 노출 ID는 UUIDv7 또는 무작위 UUID를 사용한다.
- 테이블명/컬럼명은 snake_case, enum 값은 대문자 문자열로 고정한다.
- 인벤토리 행은 PVE 투영이며 삭제 대신 `is_present=false`와 `missing_since`를 사용한다. 소유권, operation, 감사 이력은 물리 삭제하지 않는다.
- JSONB는 PVE의 비정형 관측값과 안전하게 정규화된 snapshot에만 쓰고, 권한/상태/관계의 핵심 값은 정규 컬럼으로 둔다.
- 비밀은 평문으로 저장하지 않으며 감사/operation JSON에도 포함하지 않는다.
- 낙관적 동시성 제어가 필요한 엔터티에는 `version`을 두고 `UPDATE ... WHERE version = :expected`를 사용한다.

## 2. 관계 개요

```text
users ──< refresh_tokens
  ├──< organization_members >── organizations
  │  └──< mfa_methods
  │
  ├──< workload_assignments >── workloads >── clusters ──< nodes
  │                                  │             └──< cluster_credentials
  │                                  └──< ip_allocations >── ip_addresses >── ip_pools
  │
  ├──< provision_requests >── templates ──> workloads(template source)
  └──< operations ──< pve_tasks
             └──< operation_outbox

sync_runs ──> clusters
audit_logs ──> actor/target/operation (논리 참조 포함)
backup_targets ──> clusters
backup_runs ──> backup_targets, workloads, operations
```

## 3. 공통 enum

애플리케이션 enum과 PostgreSQL enum 또는 `CHECK` 제약을 일치시킨다. 마이그레이션 유연성을 위해 초기에는 문자열 + `CHECK` 사용을 우선 고려한다.

- `user_role`: `SUPER_ADMIN`, `OPERATOR`, `CUSTOMER`
- `workload_kind`: `QEMU`, `LXC`
- `power_state`: `RUNNING`, `STOPPED`, `PAUSED`, `SUSPENDED`, `UNKNOWN`
- `operation_type`: `INVENTORY_SYNC`, `POWER_START`, `POWER_SHUTDOWN`, `POWER_STOP`, `POWER_REBOOT`, `POWER_RESET`, `PROVISION_VM`, `CANCEL_PROVISION`, `CREDENTIAL_TEST`
- `operation_status`: `PENDING`, `QUEUED`, `RUNNING`, `CANCEL_REQUESTED`, `SUCCEEDED`, `FAILED`, `TIMEOUT`, `CANCELLED`, `NEEDS_ATTENTION`
- `pve_task_status`: `SUBMITTED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `UNKNOWN`
- `provision_status`: `PENDING`, `RESERVING`, `CLONING`, `CONFIGURING`, `STARTING`, `VERIFYING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `NEEDS_ATTENTION`
- `ip_address_state`: `AVAILABLE`, `RESERVED`, `ASSIGNED`, `QUARANTINED`, `DISABLED`
- `allocation_kind`: `AUTOMATIC`, `MANUAL`
- `sync_status`: `RUNNING`, `SUCCEEDED`, `PARTIAL`, `FAILED`
- `audit_outcome`: `ATTEMPTED`, `SUCCEEDED`, `DENIED`, `FAILED`

## 4. 인증과 사용자

### `users`

| 컬럼 | 타입/제약 | 설명 |
|---|---|---|
| `id` | UUID PK | 사용자 식별자 |
| `email` | `CITEXT UNIQUE NOT NULL` | 정규화 로그인 ID |
| `display_name` | `VARCHAR(120) NOT NULL` | 표시명 |
| `role` | `VARCHAR(20) NOT NULL CHECK` | `SUPER_ADMIN`, `OPERATOR`, `CUSTOMER` |
| `password_hash` | `TEXT NULL` | Argon2id 해시; 외부 IdP 전환 대비 nullable |
| `is_active` | `BOOLEAN NOT NULL DEFAULT true` | 로그인/작업 허용 여부 |
| `mfa_required` | `BOOLEAN NOT NULL` | 관리자 true 강제 |
| `session_epoch` | `INTEGER NOT NULL DEFAULT 0` | 전체 세션 즉시 무효화 버전 |
| `last_login_at` | `TIMESTAMPTZ NULL` | 마지막 성공 로그인 |
| `created_by_id` | UUID FK users NULL | bootstrap 관리자는 NULL 가능 |
| `created_at`, `updated_at` | `TIMESTAMPTZ NOT NULL` | 생성/변경 시각 |
| `disabled_at` | `TIMESTAMPTZ NULL` | 비활성 시각 |
| `version` | `INTEGER NOT NULL` | 낙관적 잠금 |

규칙: 이메일은 API에서 Unicode/길이를 검증한다. 마지막 활성 관리자의 비활성화/강등을 서비스와 트랜잭션 잠금으로 막는다.

### `refresh_tokens`

- `id UUID PK`, `user_id FK NOT NULL`, `family_id UUID NOT NULL`.
- `token_hash BYTEA UNIQUE NOT NULL`: 원본 refresh token은 저장하지 않는다.
- `parent_id`, `replaced_by_id`: rotation chain과 재사용 탐지.
- `created_at`, `expires_at`, `revoked_at`, `reuse_detected_at`.

인덱스: `(user_id, revoked_at, expires_at)`, 만료 세션 정리용 `expires_at`.

### `organizations`, `organization_members`

- 조직은 `id`, 유일한 `name`, `is_active`, 생성자와 version/timestamp를 가진다.
- 구성원은 `(organization_id, user_id)` 유일 제약을 가지며 추가한 관리자를 기록한다.
- 조직 생성과 구성원 변경은 `SUPER_ADMIN`만 수행하고 `OPERATOR`는 목록만 조회한다.

### `mfa_methods`

- `id UUID PK`, `user_id FK`, `kind`, `label`, `is_primary`.
- TOTP secret 또는 WebAuthn credential material은 종류에 맞게 암호화/구조화 저장한다.
- `created_at`, `last_used_at`, `disabled_at`.
- 활성 방법 수와 관리자 필수 정책은 서비스 계층에서 트랜잭션으로 검증한다.

## 5. 클러스터와 자격 증명

### `clusters`

| 컬럼 | 타입/제약 | 설명 |
|---|---|---|
| `id` | UUID PK | 클러스터 내부 ID |
| `name` | `VARCHAR(100) UNIQUE NOT NULL` | UI 표시명 |
| `api_base_url` | `TEXT NOT NULL` | 정규화된 HTTPS endpoint |
| `tls_server_name` | `VARCHAR(253) NULL` | TLS 검증 이름 |
| `ca_bundle_ref` | `TEXT NULL` | 허용된 CA bundle의 비밀/파일 참조; PEM을 API로 반환하지 않음 |
| `is_active` | `BOOLEAN NOT NULL` | 동기화/작업 허용 |
| `last_sync_succeeded_at` | `TIMESTAMPTZ NULL` | stale 판단 |
| `last_connection_error_code` | `VARCHAR(64) NULL` | 비민감 오류 분류 |
| `sync_interval_seconds` | `INTEGER CHECK (>=15)` | 기본 60초 |
| `created_by_id` | UUID FK users | 등록 관리자 |
| `created_at`, `updated_at`, `disabled_at` | TIMESTAMPTZ | 수명주기 |
| `version` | INTEGER | 낙관적 잠금 |

URL 전체 유일 제약은 정규화된 endpoint 중복 등록을 막는다. hostname/IP 허용 정책은 DB 제약이 아닌 검증 서비스가 수행한다.

### `cluster_credentials`

- `id UUID PK`, `cluster_id UUID FK NOT NULL`.
- `token_identifier VARCHAR(255) NOT NULL`: PVE user/token ID.
- `secret_ciphertext BYTEA NOT NULL`, `secret_nonce BYTEA NOT NULL`, `key_version VARCHAR(32) NOT NULL`.
- `is_active BOOLEAN NOT NULL`, `created_by_id`, `created_at`, `last_used_at`, `retired_at`.
- 부분 유일 인덱스: `UNIQUE(cluster_id) WHERE is_active`.

삭제 대신 retire한다. API serializer, `__repr__`, exception context에서 암호 필드를 제외한다.

## 6. 인벤토리

### `nodes`

- `id UUID PK`, `cluster_id UUID FK NOT NULL`.
- `pve_name VARCHAR(255) NOT NULL`.
- `status VARCHAR(32) NOT NULL`, `cpu_total`, `cpu_usage`, `memory_total_bytes`, `memory_used_bytes`, `uptime_seconds`.
- `pve_version VARCHAR(64) NULL`, `raw_facts JSONB NOT NULL DEFAULT '{}'` (allowlist된 비민감 값).
- `observed_at TIMESTAMPTZ NOT NULL`, `sync_generation BIGINT NOT NULL`.
- `is_present BOOLEAN NOT NULL`, `missing_since TIMESTAMPTZ NULL`.
- `created_at`, `updated_at`.

제약/인덱스: `UNIQUE(cluster_id, pve_name)`, `(cluster_id, is_present)`, `observed_at`.

### `workloads`

| 컬럼 | 타입/제약 | 설명 |
|---|---|---|
| `id` | UUID PK | API에서 사용하는 전역 ID |
| `cluster_id` | UUID FK NOT NULL | PVE 클러스터 |
| `node_id` | UUID FK NULL | 현재 노드; 동기화 중/오프라인이면 NULL 가능 |
| `vmid` | `INTEGER NOT NULL CHECK (vmid > 0)` | 클러스터 범위 PVE ID |
| `kind` | `VARCHAR(10) CHECK` | QEMU 또는 LXC |
| `name` | `VARCHAR(255) NULL` | PVE 관측 이름 |
| `is_template` | `BOOLEAN NOT NULL` | PVE template 여부 |
| `power_state` | `VARCHAR(20) NOT NULL` | 마지막 관측 상태 |
| `cpu_cores` | `INTEGER NULL` | 마지막 관측 사양 |
| `memory_bytes`, `disk_bytes` | `BIGINT NULL` | 마지막 관측 사양 |
| `tags` | `JSONB NOT NULL DEFAULT '[]'` | 정규화된 PVE tags |
| `observed_config` | `JSONB NOT NULL DEFAULT '{}'` | 허용 목록 기반 관측 정보 |
| `observed_at` | `TIMESTAMPTZ NOT NULL` | 상태 관측 시각 |
| `sync_generation` | `BIGINT NOT NULL` | 동기화 세대 |
| `is_present`, `missing_since` | BOOLEAN/TIMESTAMPTZ | PVE 존재 여부 |
| `created_at`, `updated_at` | TIMESTAMPTZ | 로컬 행 시각 |
| `version` | INTEGER | 경쟁 상태 보호 |

핵심 제약은 `UNIQUE(cluster_id, vmid)`다. `vmid` 단독 유일 제약을 절대 두지 않는다. 목록 인덱스는 `(cluster_id, is_present, kind)`, `(node_id, is_present)`, `(power_state)`, 이름 검색용 인덱스를 둔다.

템플릿도 PVE 리소스이므로 `workloads.is_template=true`로 관측한다. 고객 할당은 `is_template=false`에만 허용한다.

### `sync_runs`

- `id UUID PK`, `cluster_id UUID FK`, `generation BIGINT`.
- `status`: `QUEUED`, `RUNNING`, `SUCCEEDED`, `PARTIAL`, `FAILED`, `SKIPPED`.
- `scope`: `FULL` 또는 `TARGET`; target은 `target_workload_id`를 가진다.
- `partial_failure`, `started_at`, `finished_at`, `error_code`, `resource_counts JSONB`.
- `triggered_by`: scheduler/admin/operation 구분, `requested_by_id NULL`.
- `UNIQUE(cluster_id, generation)`.
- 클러스터별 활성 전체 sync와 workload별 활성 target sync는 각각 최대 하나다.

완전 성공한 run만 누락 리소스 tombstone에 사용할 수 있다.

### `inventory_storages`

- `id UUID PK`, `cluster_id UUID FK`, `natural_key`, `storage_id`, `node`.
- 상태, 종류, 용량, content allowlist와 shared 여부를 저장한다.
- `observed_at`, `sync_generation`, `is_present`, `missing_since`를 node/workload와
  동일하게 사용한다.
- `UNIQUE(cluster_id, natural_key)`.

### `reconciliation_findings`, `workload_change_events`

- finding은 종류, 심각도, 대상, 안전한 변경 요약, 최초·최근 관측 시각을 가진다.
- 상태는 `OPEN`, `ACKNOWLEDGED`, `RESOLVED`이며 담당자와 해결 근거를 기록한다.
- workload change event는 sync run과 workload에 연결된 append형 외부 변경 요약이다.
- finding 처리로 workload assignment나 IP allocation을 자동 변경하지 않는다.

## 7. 고객 소유권

### `workload_assignments`

- `id UUID PK`, `workload_id UUID FK NOT NULL`, `organization_id UUID FK NOT NULL`.
- `assigned_by_id UUID FK users NOT NULL`, `assigned_at TIMESTAMPTZ NOT NULL`.
- `revoked_by_id UUID FK users NULL`, `revoked_at TIMESTAMPTZ NULL`, `revoke_reason TEXT NULL`.
- 선택적 `label`, `notes`는 길이 제한 및 민감정보 금지.

제약:

- `UNIQUE(workload_id) WHERE revoked_at IS NULL`: 워크로드의 현재 고객은 최대 한 명.
- 활성 조직 검증, template/absent workload 할당 금지는 서비스 계층에서 행 잠금과 함께 수행한다.
- 조직 목록 최적화 인덱스: `(organization_id, assigned_at)`.

이력 행은 갱신 가능한 필드를 최소화하고 회수 후 삭제하지 않는다. 재할당은 새 행이다.

## 8. 템플릿과 프로비저닝

### `products`

- `id UUID PK`, `name VARCHAR(120) UNIQUE`, `is_enabled`.
- 고정 상품 사양 `cpu_cores`, `memory_bytes`, `disk_bytes`와 생성자/시각을 저장한다.
- 요청은 상품 ID만 선택하며 worker는 저장된 snapshot과 현재 상품 사양이 일치하는지 다시 확인한다.

### `templates`

- `id UUID PK`, `name VARCHAR(120) UNIQUE`, `source_workload_id UUID FK UNIQUE`.
- `is_enabled BOOLEAN`, `description TEXT NULL`.
- 허용 기본값: `default_cpu_cores`, `default_memory_bytes`, `default_disk_bytes`, `default_storage`, `default_bridge`, `default_vlan_tag`.
- 제한: `min_*`, `max_*` 사양.
- `cloud_init_enabled BOOLEAN`, `created_by_id`, `created_at`, `updated_at`, `version`.

`source_workload_id`는 `QEMU`, `is_template=true`, `is_present=true`여야 한다. 이는 서비스 계층과 주기 검증으로 강제한다.

### `provisioning_nodes`

- `(cluster_id, name) UNIQUE`, `is_enabled`, `is_maintenance`.
- `available_memory_bytes`, `available_storage_bytes`, `last_selected_at`을 저장한다.
- 선택 시 row lock을 잡고 enabled, 비유지보수, 충분한 메모리/스토리지를 검사한다. 동률은 `last_selected_at`이 오래된 순서로 round-robin한다.

### `provision_requests`

| 컬럼 | 타입/제약 | 설명 |
|---|---|---|
| `id` | UUID PK | 요청 ID |
| `operation_id` | UUID FK UNIQUE | 논리 operation |
| `requested_by_id` | UUID FK users | 요청 관리자 |
| `idempotency_key_hash` | BYTEA NOT NULL | 원문 키 미저장 |
| `request_fingerprint` | BYTEA NOT NULL | 같은 키/다른 body 충돌 검출 |
| `template_id` | UUID FK | 템플릿 |
| `target_cluster_id` | UUID FK | 초기에는 템플릿 클러스터와 동일 |
| `target_node_id` | UUID FK NULL | 자동 선택 가능 |
| `target_vmid` | INTEGER NULL | 예약 후 확정 |
| `target_name` | VARCHAR(255) | VM 이름 |
| `customer_user_id` | UUID FK NULL | 성공 후 선택적 할당 |
| `status`, `current_step` | VARCHAR | 상태 머신 |
| `spec_snapshot` | JSONB | 검증·정규화된 CPU/메모리/디스크/네트워크 입력; 비밀 제외 |
| `error_code`, `error_summary` | VARCHAR/TEXT NULL | 비민감 오류 |
| `created_at`, `started_at`, `finished_at` | TIMESTAMPTZ | 실행 시각 |
| `version` | INTEGER | CAS 상태 전이 |

### `provisioning_steps`

- 요청별 15개 단계를 `step_order`, `step_name`, `status`, `attempt_count`로 기록한다.
- 외부 비동기 단계는 `pve_upid`, 안전한 결과 요약과 비민감 오류만 저장한다.
- `(provisioning_request_id, step_order)`와 `(provisioning_request_id, step_name)`은 유일하다.
- worker 재시작 시 `SUCCEEDED` 단계는 건너뛰고 `RUNNING` 또는 `FAILED`가 아닌 다음 단계부터 재개한다.

제약:

- `UNIQUE(requested_by_id, idempotency_key_hash)` 또는 운영 정책상 전역 key scope.
- `(target_cluster_id, target_vmid)`는 확정된 미완료/성공 요청끼리 중복되지 않도록 부분 유일 인덱스를 둔다.
- 같은 키에 fingerprint가 다르면 `409 IDEMPOTENCY_KEY_REUSED`.

Cloud-Init SSH 공개키는 public 정보지만 별도 정규화 테이블 또는 제한된 snapshot에 저장할 수 있다. 초기 비밀번호는 이 테이블에 저장하지 않는다.

## 9. Operation과 PVE UPID

### `operations`

- `id UUID PK`, `type`, `status`, `requested_by_id UUID FK`.
- `cluster_id UUID FK NULL`, `workload_id UUID FK NULL`.
- `idempotency_key_hash BYTEA NULL`, `request_fingerprint BYTEA NULL`.
- `celery_task_id VARCHAR(255) NULL`, `queue_name VARCHAR(64) NULL`.
- `progress_percent SMALLINT NULL CHECK (0..100)`, `current_step VARCHAR(64) NULL`.
- `error_code VARCHAR(64) NULL`, `error_summary TEXT NULL`.
- `result JSONB NOT NULL DEFAULT '{}'`: 식별자/비민감 요약만.
- `requested_at`, `queued_at`, `started_at`, `finished_at`, `heartbeat_at`.
- `cancel_requested_at`, `version`.

인덱스: `(requested_by_id, requested_at DESC)`, `(cluster_id, status)`, `(workload_id, status)`, stuck 탐지용 `(status, heartbeat_at)`.

동일 워크로드에서 충돌하는 활성 operation은 부분 유일 인덱스만으로 모든 type 조합을 표현하기 어렵기 때문에 workload advisory lock과 명시적 충돌 행 검사를 함께 사용한다.

### `pve_tasks`

- `id UUID PK`, `operation_id UUID FK NOT NULL`, `cluster_id UUID FK NOT NULL`.
- `workload_id UUID FK NULL`, `step_name VARCHAR(64) NOT NULL`.
- `upid TEXT NOT NULL`, `status VARCHAR(20) NOT NULL`.
- `pve_node VARCHAR(255) NULL`, `pve_exit_status VARCHAR(255) NULL`.
- `submitted_at`, `last_polled_at`, `completed_at`.
- `poll_attempts INTEGER`, `error_code`, `error_summary`.
- `raw_result JSONB`: 크기/키 allowlist와 보존 기간 적용.

제약: `UNIQUE(cluster_id, upid)`, `UNIQUE(operation_id, step_name, upid)`. UPID는 클러스터 범위에서 해석한다.

### `operation_outbox`

- `id BIGSERIAL PK`, `operation_id UUID FK`, `event_type`, `payload JSONB`(ID만).
- `status`는 `PENDING|PUBLISHED`이며 `created_at`, `published_at`, `attempt_count`,
  `next_attempt_at`, `last_error_code`를 함께 저장한다.
- `UNIQUE(operation_id, event_type)`로 중복 publish를 제어한다.

API 트랜잭션에서 operation과 outbox를 함께 commit하고 별도 dispatcher가 Celery에 발행한다. 발행 중복은 정상으로 간주하고 워커 멱등성으로 흡수한다.

### `scheduler_leases`

- `name VARCHAR(120) PK`, `owner_id UUID`, `fencing_token BIGINT`.
- `acquired_at`, `lease_expires_at`, `updated_at`.
- lease 획득은 PostgreSQL transaction advisory lock과 행 잠금으로 직렬화한다.
- 만료 후 소유권 이전마다 fencing token을 증가시키며 오래된 실행은 최종 쓰기 전에
  현재 owner/token을 다시 확인한다.

### `maintenance_runs`

- `id UUID PK`, `job_name`, `status`, `owner_id`, `fencing_token`.
- `started_at`, `finished_at`, `processed_count`, `error_code`.
- 최근 성공 시각과 실패는 관리자 운영 상태 API 및 Prometheus 지표의 기준이다.

완료·skip 행은 기본 7일 보존하고 실패 행은 incident 연결을 위해 별도 정책으로
보존한다.

## 9.1 PBS 워크로드 백업

### `backup_targets`

- `id UUID PK`, `cluster_id UUID FK NOT NULL`, `storage_id VARCHAR(255) NOT NULL`.
- `datastore`, `namespace`: PVE storage 구성에서 관측한 비민감 표시 정보.
- `is_enabled`, `last_observed_available`, `last_checked_at`.
- `created_by_id`, timestamps, `version`.
- `UNIQUE(cluster_id, storage_id)`.

PBS server 주소, token secret, fingerprint와 encryption key는 저장하지 않는다. 실제 연결
정보는 PVE의 storage 구성이 관리하며 PVE Master는 storage ID만 참조한다.

### `backup_runs`

- `id UUID PK`, `operation_id UUID FK UNIQUE NOT NULL`.
- `backup_target_id`, `workload_id`, 요청 당시 `organization_id`.

### `restore_runs`

- 성공한 `backup_run_id`와 실행 `operation_id`를 참조한다.
- `cluster_id`, `source_workload_id`, `target_node`, 새 `target_vmid`, `target_name`과 상태를 저장한다.
- 같은 클러스터·VMID의 `QUEUED`/`RUNNING` 복구는 부분 unique index로 하나만 허용한다.
- 기존 workload를 덮어쓰지 않으며 복구된 VM/CT는 inventory 동기화에서 새 workload로 수집한다.
- `mode`, `compression`, `status`.
- `snapshot_volume_id`, `snapshot_time`, 논리 크기 `size_bytes`, 이번 실행 신규 전송량 `transferred_bytes`.
- `started_at`, `finished_at`, `created_at`.

백업 실행 상태의 기준은 연결된 operation이다. 백업이 성공했지만 후속 storage content
조회가 실패하면 operation은 성공으로 유지하고 snapshot metadata를 후속 재조정 대상으로
남긴다.

## 10. IPAM

### `ip_pools`

- `id UUID PK`, `name VARCHAR(120) UNIQUE NOT NULL`.
- `cluster_id UUID FK NULL`: 특정 클러스터 전용, NULL이면 공유 정책 가능.
- `cidr CIDR NOT NULL`, `gateway INET NULL`, `dns_servers INET[] NOT NULL DEFAULT '{}'`.
- `bridge VARCHAR(64) NOT NULL`, `vlan_tag INTEGER NULL CHECK (1..4094)`.
- `ip_family SMALLINT CHECK (4,6)`, `allocation_strategy` (`SEQUENTIAL`/`RANDOM`).
- `next_offset NUMERIC(39,0)`: 큰 IPv6 풀을 전체 materialize하지 않는 sparse 순차 할당 위치.
- `is_active`, `quarantine_seconds DEFAULT 600`, `created_by_id`, timestamps, `version`.

겹치는 풀은 네트워크 영역/cluster가 같을 때 exclusion constraint 또는 서비스 검증으로 금지한다. gateway는 cidr 안에 있어야 하며 자동 할당 대상에서 제외한다.

### `ip_addresses`

- `id UUID PK`, `pool_id UUID FK`, `address INET NOT NULL`.
- `state`, `reserved_for TEXT NULL`, `quarantined_until TIMESTAMPTZ NULL`.
- `last_allocated_at`, `created_at`, `updated_at`, `version`.
- `UNIQUE(pool_id, address)`.

주소가 pool CIDR에 포함되고 family가 일치하는지는 서비스와 migration 제약으로 보장한다. 제외 범위는 `ip_pool_exclusions`에 시작/끝 주소로 보존한다. 매우 큰 IPv6 풀은 모든 주소를 미리 materialize하지 않고 할당되거나 개별 비활성화된 주소만 저장하는 sparse 전략을 사용한다.

### `ip_allocations`

- `id UUID PK`, `ip_address_id UUID FK`, `workload_id UUID FK NULL`, `provision_request_id UUID FK NULL`.
- `kind`, `status` (`RESERVED`, `ASSIGNED`, `RELEASED`, `QUARANTINED`).
- `allocated_by_id UUID FK users NULL`, `allocated_at`, `confirmed_at`, `released_at`.
- `release_reason`, `version`.

부분 유일 인덱스:

- `UNIQUE(ip_address_id) WHERE status IN ('RESERVED','ASSIGNED','QUARANTINED')`.
- 필요 시 `UNIQUE(workload_id, ip_address_id) WHERE status='ASSIGNED'`.

할당 알고리즘은 pool 행을 잠그고 materialize된 후보를 `SELECT ... FOR UPDATE SKIP LOCKED`로 선택한 뒤 같은 트랜잭션에서 address/할당 상태를 변경한다. 활성 할당 부분 유일 인덱스가 최종 중복 방어선이며 Redis 락은 IP 유일성 근거로 사용하지 않는다.

## 11. 감사 로그

### `audit_logs`

- `id UUID PK`, `created_at TIMESTAMPTZ NOT NULL`, `action`, `result`, `error_code`.
- `actor_user_id UUID NULL`, 당시의 `actor_role`, `organization_id`.
- `resource_type`, `resource_id`, 선택적 `workload_id`, `operation_id`, `pve_upid`.
- `source_ip`, 길이가 제한된 `user_agent`, `request_id`.
- `before JSONB`, `after JSONB`: 재귀 민감 키 마스킹을 통과한 비밀 없는 변경 snapshot.
- 과거 호환용 `outcome`은 `result`와 같은 값을 저장한다.

DB trigger가 모든 UPDATE를 차단하고 DELETE는 retention transaction flag가 설정된 별도 maintenance job에만 허용한다. API에는 변경·삭제 route가 없다. 운영 규모에 따라 월별 파티션과 외부 WORM/SIEM 전송을 추가한다.

## 12. 삭제와 보존 정책

- `users`: 비활성화/가명화. 할당/감사 이력 때문에 물리 삭제하지 않는다.
- `clusters`: active operation과 현재 리소스가 있으면 삭제 금지, 이후 disabled/tombstone 처리.
- `workloads`: PVE에서 사라져도 tombstone 보존. 할당/operation 참조가 없는 오래된 투영만 별도 정책으로 정리 가능.
- `operations`, `pve_tasks`: 운영/감사 요구 기간 동안 보존하며 큰 raw result는 더 짧게 정리한다.
- `backup_runs`: operation 보존 기간과 함께 유지한다. 실제 PBS snapshot 삭제 여부는 주기적 관측으로 조정한다.
- `audit_logs`: 기본 1년 온라인 보존 후 외부 불변 저장소 정책에 따라 archive.
- `auth_sessions`, outbox 완료 행, sync run 상세는 더 짧은 TTL로 정리한다.
- 모든 보존 작업 자체도 감사 대상이다.

## 13. 트랜잭션과 잠금 규칙

- 사용자 할당: workload 행 lock → 현재 assignment 확인 → revoke/create → audit/outbox를 한 트랜잭션으로 처리한다.
- 전원 작업: 현재 assignment/role 확인 → 충돌 operation 확인 → operation/outbox 생성. PVE 호출은 commit 후 수행한다.
- IP 예약: pool/address 후보 row lock → active allocation 유일 제약 → provision 연결을 한 트랜잭션으로 처리한다.
- 상태 전이: `version` 또는 예상 status가 일치할 때만 갱신한다.
- 외부 PVE 호출 중 DB 트랜잭션/row lock을 오래 유지하지 않는다.
- PVE 결과 반영은 새 짧은 트랜잭션으로 처리하고 실제 상태 재조회 결과를 함께 기록한다.

## 14. 마이그레이션 규칙

- Alembic revision은 schema와 데이터 backfill을 분리하고 대규모 테이블 변경은 expand/migrate/contract 순서를 사용한다.
- 새 NOT NULL 컬럼은 nullable 추가 → backfill → 검증 → 제약 추가로 배포한다.
- 유일/외래키/부분 인덱스가 실제 경쟁 조건을 막는지 PostgreSQL 통합 테스트를 둔다.
- downgrade가 데이터 손실을 유발하면 revision에 비가역 이유와 복구 절차를 명시한다.
- enum/check 변경은 구버전 API/worker가 새 값을 읽는 배포 호환성을 고려한다.
- 운영 migration은 별도 one-shot job에서 advisory lock을 얻고 실행한다.
