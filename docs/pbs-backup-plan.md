# PBS 워크로드 백업 구현 계획

## 1. 목적

PVE Master 관리자가 여러 Proxmox VE(PVE) 클러스터에 등록된 Proxmox Backup Server(PBS) 스토리지를 한 곳에서 확인하고, VM/CT의 백업을 실행하며 작업 상태와 백업 내역을 추적할 수 있게 한다.

이 문서는 PVE Master 자체 PostgreSQL 백업을 다루는 `docs/backup-restore.md`와 별개로, PVE 워크로드를 PBS에 백업하는 기능을 정의한다.

## 2. 설계 결정

### 2.1 연결 경계

초기 버전에서 PVE Master는 PBS API를 직접 호출하거나 PBS 자격 증명을 저장하지 않는다.

```text
관리자 브라우저 -> FastAPI -> Celery operations worker -> PVE API -> PBS storage
```

- PBS는 각 PVE 클러스터에 `pbs` 타입 스토리지로 미리 등록한다.
- PVE Master는 기존 PVE API token으로 스토리지를 검색하고 백업을 실행한다.
- PVE가 PBS 주소, token secret, 인증서 fingerprint와 선택적 암호화 키를 관리한다.
- Celery 메시지에는 기존 원칙대로 `operation_id`만 전달한다.
- 백업 실행 후 받은 PVE UPID는 기존 `pve_tasks`에 저장하고 폴링한다.

### 2.2 권한과 노출 범위

- `SUPER_ADMIN`: 백업 대상 등록/변경, 수동 백업 실행, 전체 백업 내역 조회.
- `OPERATOR`: 수동 백업 실행, 전체 백업 내역 조회.
- `CUSTOMER`: 초기 버전에서는 백업 API와 화면에 접근할 수 없다.
- 백업 삭제와 prune은 제공하지 않는다.
- 복구는 `SUPER_ADMIN`만 성공한 스냅샷을 같은 클러스터의 **새 VM/CT**로 실행할 수 있다.
- 기존 VMID 덮어쓰기와 원본 VM 교체는 허용하지 않는다.
- PBS token은 클러스터별로 분리하고 특정 datastore/namespace에 `DatastoreBackup` 최소 권한만 부여한다.
- prune은 삭제 권한이 있는 PVE client token이 아니라 PBS의 prune job에서 수행한다.

### 2.3 작업 충돌

백업 중 전원·사양 변경·삭제가 함께 실행되는 것을 막기 위해 기존 `operations`의 워크로드당 활성 작업 1개 제약을 유지한다. 백업 제출 뒤 응답이 유실된 경우 동일 요청을 즉시 다시 제출하지 않고 `TIMEOUT`으로 종료하여 중복 백업 가능성을 차단한다.

## 3. 이번 구현 범위

### 포함

1. 클러스터별 PBS 스토리지 검색
2. 검색된 PBS 스토리지를 로컬 백업 대상으로 활성화/비활성화
3. VM/CT 수동 백업 실행
4. PVE UPID 저장 및 완료 상태 추적
5. 성공한 백업의 스토리지 콘텐츠 조회 및 snapshot 메타데이터 저장
6. 백업 대상과 백업 실행 내역 관리자 API
7. 관리자 `백업` 메뉴와 백업 현황 화면
8. VM 목록에서 `지금 백업` 진입
9. 권한, 멱등성, 충돌, timeout, TLS/인증/권한 실패 테스트
10. 성공한 스냅샷을 새 VMID로 복구하고 PVE UPID 상태 추적

### 제외

- 자동 백업 정책과 스케줄 등록
- PBS 직접 연결 및 PBS credential 저장
- 백업 삭제와 prune 실행
- 기존 VM/CT 덮어쓰기 복구
- 파일 단위 복구
- 고객 셀프서비스 백업
- PBS client-side encryption key 관리

## 4. 데이터 모델

### 4.1 `backup_targets`

PVE에 등록된 PBS storage ID를 참조한다. secret은 저장하지 않는다.

| 컬럼 | 설명 |
|---|---|
| `id` | 외부 노출 UUID |
| `cluster_id` | PVE 클러스터 FK |
| `storage_id` | PVE storage ID |
| `datastore` | 관측된 PBS datastore 이름, 없으면 null |
| `namespace` | 관측된 PBS namespace, 없으면 null |
| `is_enabled` | PVE Master에서 백업 대상으로 사용할지 여부 |
| `last_observed_available` | 마지막 관측 가용 여부 |
| `last_checked_at` | 마지막 확인 시각 |
| `created_by_id` | 등록 관리자 |
| 생성/수정 시각 | 감사용 timestamp |

제약: `UNIQUE(cluster_id, storage_id)`.

### 4.2 `backup_runs`

수동 백업 한 번의 업무 상태를 저장한다.

| 컬럼 | 설명 |
|---|---|
| `id` | 백업 실행 UUID |
| `operation_id` | 기존 operation과 1:1 FK |
| `backup_target_id` | 사용한 백업 대상 FK |
| `workload_id` | 대상 VM/CT FK |
| `organization_id` | 요청 당시 조직 snapshot, nullable |
| `mode` | 초기에는 `snapshot` 고정 |
| `compression` | 초기에는 `zstd` 고정 |
| `status` | operation과 동기화된 상태 |
| `snapshot_volume_id` | PVE storage content volume ID |
| `snapshot_time` | 백업 시각 |
| `size_bytes` | PVE 콘텐츠 API가 보고한 논리 백업 크기 |
| `transferred_bytes` | 작업 로그의 전체 전송량에서 PBS 재사용량을 뺀 이번 실행의 신규 전송량 |
| 시작/완료/생성 시각 | 실행 이력 |

`operations.result`에는 storage ID, mode, compression 같은 비민감 실행 입력만 저장한다.

### 4.3 `restore_runs`

백업 스냅샷에서 새 VM/CT를 만드는 복구 실행 상태를 저장한다.

| 컬럼 | 설명 |
|---|---|
| `id` | 복구 실행 UUID |
| `operation_id` | 기존 operation과 1:1 FK |
| `backup_run_id` | 원본 백업 실행 FK |
| `cluster_id` | 복구 대상 PVE 클러스터 FK |
| `source_workload_id` | 백업 당시 원본 VM/CT FK |
| `target_node` | 새 VM/CT를 생성할 PVE node |
| `target_vmid` | 새 VMID, 기존 VMID 사용 금지 |
| `target_name` | 새 VM 이름 또는 CT hostname |
| `status` | operation과 동기화된 상태 |
| 시작/완료/생성 시각 | 실행 이력 |

동일 클러스터와 VMID에 활성 복구가 둘 이상 생성되지 않도록 부분 unique index를 둔다.

## 5. PVE adapter 확장

`ProxmoxClient`에 다음 typed method를 추가한다.

- `get_storage_configurations()`: `GET /api2/json/storage`
- `get_node_storages(node)`: `GET /api2/json/nodes/{node}/storage`
- `submit_guest_backup(node, vmid, storage, mode, compression)`: `POST /api2/json/nodes/{node}/vzdump`
- `get_backup_content(node, storage, vmid)`: `GET /api2/json/nodes/{node}/storage/{storage}/content?content=backup&vmid=...`
- `submit_guest_restore(kind, node, archive, vmid, name)`: QEMU는 `POST /nodes/{node}/qemu`, LXC는 `POST /nodes/{node}/lxc`

검증 규칙:

- storage type은 정확히 `pbs`여야 한다.
- storage ID와 node는 브라우저 입력을 PVE 경로에 직접 사용하지 않고 DB/검색 결과에서 가져오며 URL encode한다.
- 백업 mode는 `snapshot`, compression은 `zstd` allowlist로 제한한다.
- 모든 호출은 기존 TLS 검증, SSRF 정책, timeout, redirect 금지 설정을 공유한다.
- UPID 형식과 최대 길이를 검증한다.
- restore archive는 서버가 저장한 성공 백업의 `snapshot_volume_id`만 사용한다.
- 복구 전 PVE live inventory와 로컬 inventory에서 target VMID가 비어 있는지 모두 확인한다.
- `force`는 전송하지 않고 QEMU에는 `unique=1`, LXC에는 `restore=1`을 적용한다.

## 6. API 계약

### 스토리지 검색

```http
GET /api/v1/admin/clusters/{cluster_id}/backup-storages
```

PVE에 현재 등록된 PBS storage와 가용 상태를 반환한다. 자격 증명, PBS server 주소, fingerprint는 반환하지 않는다.

### 백업 대상 관리

```http
GET  /api/v1/admin/backup-targets
POST /api/v1/admin/backup-targets
PATCH /api/v1/admin/backup-targets/{target_id}
```

대상 생성 시 서버가 PVE storage를 다시 조회하여 `pbs` 타입과 가용성을 검증한다.

### 수동 백업

```http
POST /api/v1/admin/workloads/{workload_id}/backups
Idempotency-Key: <8..255 chars>
```

```json
{
  "backup_target_id": "uuid",
  "mode": "snapshot",
  "compression": "zstd"
}
```

성공적으로 접수하면 `202 Accepted`, `Location: /api/v1/admin/backups/{backup_run_id}`를 반환한다.

### 백업 내역

```http
GET /api/v1/admin/backups
GET /api/v1/admin/backups/{backup_run_id}
```

필터: cluster, workload, target, status. 목록은 최신 순으로 반환한다.

### 새 VM/CT로 복구

```http
POST /api/v1/admin/backups/{backup_run_id}/restores
Idempotency-Key: <8..255 chars>
```

```json
{
  "target_node": "pve-a",
  "target_vmid": 220,
  "target_name": "service-restored"
}
```

성공한 백업과 snapshot ID가 있어야 하며 `SUPER_ADMIN`만 요청할 수 있다. 접수 시 `202 Accepted`를 반환하고 기존 VMID와 충돌하면 `409`를 반환한다.

```http
GET /api/v1/admin/restores/{restore_run_id}
```

## 7. 작업 상태 흐름

```text
QUEUED -> RUNNING -> SUCCEEDED
                  -> FAILED
                  -> TIMEOUT
```

1. API가 역할, workload, cluster, backup target, idempotency key를 검증한다.
2. `Operation`과 `BackupRun`을 한 트랜잭션에 생성하고 감사 `ATTEMPTED` 사건을 기록한다.
3. commit 후 `operation_id`만 operations queue에 발행한다.
4. worker가 actor 활성 상태, 권한, workload/cluster/target 상태를 다시 확인한다.
5. PVE `vzdump`를 제출하고 반환 UPID를 즉시 `PveTask(step_name=backup)`에 저장한다.
6. 기존 timeout 설정 안에서 UPID를 폴링한다.
7. 성공하면 storage content를 조회하여 가장 최근 snapshot을 `BackupRun`에 연결한다.
8. 최종 상태와 감사 `SUCCEEDED`/`FAILED` 사건을 같은 commit에 기록한다.

복구도 같은 상태 흐름과 UPID 폴링을 사용한다. API와 worker가 권한, snapshot, 활성 클러스터, target node와 VMID 충돌을 각각 다시 확인한다. 성공 후 새 VM/CT는 다음 inventory 동기화에서 `workloads`에 반영하며 자동으로 조직에 할당하지 않는다.

## 8. UI 계획

관리자 사이드바에서 `가상 머신` 다음에 `백업` 메뉴를 추가한다.

백업 화면:

- 상단 요약: 활성 백업 대상, 최근 성공, 최근 실패, 백업 없는 VM/CT 수
- 백업 대상: 클러스터, PVE storage ID, datastore/namespace, 가용 상태, 활성 상태
- 수동 백업: workload와 백업 대상을 선택하고 실행
- 백업 내역: 검색·상태·클러스터·VM 필터, workload, 조직, 대상, 상태, 시각, 논리 크기, 이번 전송량
- 백업 상세: 스냅샷 ID, 실행 시간, 오류, 동일 대상으로 다시 백업
- 성공 백업 상세: SUPER_ADMIN 전용 `새 VM/CT로 복구`, 대상 node·새 VMID·이름 입력, 덮어쓰기 금지 안내
- 복구 진행: 같은 상세 drawer에서 QUEUED/RUNNING/성공/실패 상태 표시
- 진행 중 작업은 기존 job polling 패턴으로 갱신

VM 목록에는 SUPER_ADMIN/OPERATOR에게 최근 백업 상태·시각·전송량과 `백업 관리` 버튼을 표시하고 백업 화면으로 workload ID를 전달한다.

PBS는 청크를 여러 스냅샷이 공유하므로 스냅샷 하나의 독립적인 물리 점유량은 정확히 산정할 수 없다. UI의 `논리 크기`는 전체 백업 데이터 크기이고, `이번 전송량`은 PVE 작업 로그에서 `transferred - reused`로 계산한 이번 실행의 신규 전송량이다.

PVE 로그가 재사용률 100%와 동일한 반올림 크기를 보고하면 신규 전송량은 0이며 UI에는 `신규 데이터 없음 · 기존 데이터 100% 재사용`으로 표시한다. 반올림된 크기는 같지만 재사용률이 100% 미만이면 0으로 단정하지 않고 측정 정보 없음으로 처리한다.

## 9. 테스트 계획

### Backend

- PBS storage 검색과 non-PBS 제외
- 비활성 클러스터, credential 없음, storage 없음/비활성
- SUPER_ADMIN/OPERATOR 허용, CUSTOMER 거부
- workload와 target cluster 불일치
- 같은 idempotency key 재요청 및 다른 payload 재사용 거부
- 활성 operation 충돌
- 정상 UPID 제출/폴링/backup content 연결
- PVE 인증 실패, 권한 실패, TLS 오류, timeout, 5xx, malformed payload
- 제출 timeout은 재제출하지 않음
- worker 재실행 시 기존 UPID 재사용
- 다른 클러스터의 동일 VMID 격리
- 감사 이벤트와 API/로그/Celery payload secret 비노출
- SUPER_ADMIN 복구 허용, OPERATOR/CUSTOMER 거부
- 성공하지 않은 백업과 snapshot ID 없는 백업 거부
- 로컬/PVE live VMID 충돌, 잘못된 node, 중복 복구 요청 거부
- QEMU/LXC restore payload allowlist, UPID 폴링, timeout/TLS/권한 실패

### Frontend

- 백업 메뉴와 화면 접근
- 대상/VM 선택과 수동 백업 요청
- 권한별 버튼 노출
- 상태/오류/빈 목록 렌더링
- 작은 화면에서 표 가로 스크롤 경계 유지
- 복구 form 검증, 진행 상태와 실패 상태 렌더링

## 10. 구현 순서와 완료 조건

1. 이 계획 문서 확정
2. Alembic migration 및 SQLAlchemy 모델
3. PVE client method와 단위 테스트
4. backup schema/service/runner/task/API
5. backend 통합 테스트
6. frontend API client/navigation/page
7. frontend E2E/unit 테스트
8. 문서 API/DB/운영 가이드 갱신
9. pytest, Ruff, mypy, ESLint, TypeScript, frontend test/build 실행

MVP 완료 조건:

- 두 PVE 클러스터에서 각각 PBS storage를 선택할 수 있다.
- 관리자가 VM/CT 백업을 요청하면 즉시 202를 받고 UPID 기반 상태를 확인할 수 있다.
- worker 재시작 또는 중복 메시지에도 같은 operation이 중복 제출되지 않는다.
- 성공한 백업의 volume ID, 시각과 크기가 내역에 표시된다.
- PVE/PBS secret이 DB 신규 컬럼, API 응답, 로그와 Celery 인자에 노출되지 않는다.
- 인증·권한·timeout·TLS·작업 실패 테스트가 통과한다.

## 11. 후속 단계

1. PVE native backup job을 PVE Master 정책으로 관리하고 desired/observed 상태를 조정한다.
2. PBS prune/verification 상태를 읽기 전용으로 표시한다.
3. 복구 완료 후 inventory 동기화를 즉시 요청하고 새 workload 연결 상태를 표시한다.
4. 복구 대상 디스크 storage 재매핑과 네트워크 격리 옵션을 별도 검토한다.
5. 조직 재할당 이력을 고려한 고객 백업 조회 정책을 별도 보안 검토 후 도입한다.
