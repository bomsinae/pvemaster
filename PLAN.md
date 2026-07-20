# PVE Master 개발 계획

## 1. 목적과 범위

PVE Master는 여러 Proxmox VE(PVE) 클러스터의 인벤토리와 VM 수명주기를 한 곳에서 관리하는 웹 플랫폼이다. 관리자는 클러스터, 노드, VM, CT, 템플릿과 IP 풀을 관리하고 고객에게 VM을 할당한다. 고객은 자신에게 할당된 VM만 조회하고 허용된 전원 작업만 수행한다.

이 문서는 구현 순서, 단계별 범위와 완료 조건을 정의한다. 상세 설계는 다음 문서를 기준으로 한다.

- [아키텍처](docs/architecture.md)
- [보안 모델](docs/security-model.md)
- [데이터베이스 모델](docs/database-model.md)
- [API 설계](docs/api-design.md)

## 2. 설계 원칙

1. 브라우저는 PVE API를 직접 호출하지 않는다. 모든 요청은 FastAPI와 작업 워커를 통한다.
2. PVE API 토큰의 비밀 부분은 애플리케이션 키로 암호화하여 저장하며 API 응답, 로그, 작업 인자에 노출하지 않는다.
3. 서버 측 객체 권한 검사를 모든 고객 API에 적용한다. 화면에서 숨기는 것은 권한 통제가 아니다.
4. VM/CT의 전역 식별자는 내부 UUID이고, PVE 식별자는 `(cluster_id, vmid)` 복합 유일 키다.
5. PVE의 VM/CT 상태가 최종 기준이다. 로컬 인벤토리는 조회 성능과 소유권 결정을 위한 캐시/메타데이터다.
6. 오래 걸리거나 재시도가 필요한 PVE 작업, 특히 생성·복제는 Celery 비동기 작업으로 실행한다.
7. PVE 작업의 UPID를 원문 그대로 저장하고 완료될 때까지 상태를 추적한다.
8. 권한 또는 인프라 상태를 변경하는 모든 관리 작업과 고객 전원 작업은 감사 로그에 남긴다.
9. API 요청, Celery 작업, PVE 요청은 상관관계 ID와 작업 ID로 추적한다.
10. 외부 작업은 완전한 원자적 트랜잭션이 될 수 없으므로 멱등성 키, 상태 머신, 재조정(reconciliation), 보상 작업으로 일관성을 회복한다.

## 3. 가정과 합리적 기본값

요구사항에서 정하지 않은 부분은 초기 버전에서 다음과 같이 가정한다.

- 단일 서비스 운영 조직이 여러 고객을 관리하는 모델이다. 고객별 조직/하위 관리자 기능은 1차 범위에서 제외한다.
- 사용자 역할은 `ADMIN`과 `CUSTOMER` 두 종류다. 최소 한 명의 활성 관리자가 항상 존재해야 한다.
- 인증은 서버 세션 기반의 HttpOnly/Secure/SameSite 쿠키를 기본으로 한다. 향후 외부 IdP(OIDC)를 붙일 수 있도록 인증과 사용자 도메인을 분리한다.
- 관리자 MFA는 운영 출시 전에 필수, 고객 MFA는 권장 및 정책 설정 가능으로 한다.
- 초기에는 PVE API 토큰 인증만 지원한다. 토큰에는 필요한 최소 권한만 부여하고 가능하면 권한 분리를 활성화한다.
- 클러스터 등록 시 TLS 인증서 검증이 기본이며, 사설 CA 번들을 등록할 수 있다. `verify_tls=false`는 운영에서 허용하지 않는다.
- PVE 클러스터 API 엔드포인트는 관리자가 입력한 허용 스킴 `https`와 포트에 한정하고, 저장 전 DNS/IP 및 SSRF 정책을 검증한다.
- VM과 CT는 공통 `workloads` 모델로 조회하되 `kind=QEMU|LXC`로 구분한다. 자동 프로비저닝 1차 범위는 Cloud-Init을 지원하는 QEMU VM이다.
- 한 워크로드는 동시에 최대 한 고객에게 할당된다. 할당 이력은 별도 테이블로 보존한다.
- 고객에게 허용되는 전원 작업은 `start`, `shutdown`, `stop`, `reboot`다. `stop`은 데이터 손상 위험 경고와 별도 정책/재확인을 적용한다. 고객은 현재 조직에 할당된 실행 중 VM의 콘솔만 열 수 있으며, 생성, 삭제, 마이그레이션, 스냅샷, 설정 변경은 관리자 전용이다.
- 관리자 화면에서 LXC는 `start`, `shutdown`, `stop`, `reboot` 전원 작업과 xterm.js 터미널 콘솔을 지원한다. QEMU 전용 `reset`과 noVNC 화면 콘솔은 LXC에 노출하지 않는다.
- 관리자 QEMU 콘솔은 noVNC를 사용하며 브라우저와 PVE 사이를 FastAPI WebSocket이 중계한다. PVE API 토큰과 endpoint는 브라우저에 노출하지 않는다. RFB 인증에 필요한 단기 콘솔 티켓만 `no-store` 응답으로 전달하고 메모리 밖에 보존하지 않는다.
- 관리자 LXC 콘솔은 PVE `termproxy`와 xterm.js를 사용한다. termproxy 티켓과 PVE 사용자 식별자는 서버 측 일회용 세션에만 보관하고, 브라우저에는 콘솔 종류와 PVE Master WebSocket용 일회용 토큰만 전달한다.
- 고객 QEMU 콘솔은 관리자와 동일한 PVE Master WebSocket 중계 경로를 사용하되, 세션 발급과 WebSocket 연결 시 사용자 활성 상태, 조직 멤버십과 현재 워크로드 할당을 각각 다시 확인한다.
- 인벤토리는 주기 동기화(기본 60초)와 중요 변경 직후 동기화를 병행한다. API 응답에 `observed_at`과 stale 여부를 노출한다.
- 클러스터 연결 실패 시 마지막 관측 상태를 명시적으로 stale로 표시하며 이를 현재 상태처럼 단정하지 않는다.
- PostgreSQL이 업무 데이터의 기준이고 Redis는 브로커/결과 캐시/단기 락에만 쓴다. Redis 데이터 유실이 업무 데이터 유실로 이어지지 않아야 한다.
- IP 풀은 IPv4와 IPv6 CIDR을 지원하되, 한 할당은 한 주소다. 정적 예약, 게이트웨이, DNS, VLAN/브리지 메타데이터를 지원한다. 주소 선택과 예약은 PostgreSQL 트랜잭션 잠금으로 직렬화한다.
- IP는 VM 생성 전에 `RESERVED`, 성공 및 검증 후 `ASSIGNED`, 실패/삭제 후 명시적 정리 과정을 거쳐 `AVAILABLE`로 돌아간다. 즉시 재사용하지 않고 기본 격리 시간 10분을 둔다.
- Cloud-Init 사용자 데이터의 비밀번호 평문은 저장하지 않는다. SSH 공개키 사용을 기본으로 하고, 일회성 비밀번호가 필요하면 생성 응답에서 한 번만 전달하거나 별도 비밀 저장소를 사용한다.
- 템플릿은 PVE에 이미 존재하는 QEMU 템플릿을 등록·동기화한다. 이미지 빌드 자체는 초기 범위 밖이다.
- 감사 로그의 기본 온라인 보존 기간은 1년이며 삭제/변조 방지형 외부 보관 연동을 운영 권고사항으로 둔다.
- 시간은 DB/API에서 UTC를 사용하고 UI에서 사용자 시간대로 변환한다.
- API는 `/api/v1`로 시작하며 하위 호환성이 깨지는 변경은 새 버전으로 제공한다.

## 4. 필요한 데이터 모델

상세 컬럼과 제약 조건은 `docs/database-model.md`를 따른다.

| 영역 | 모델 | 목적 |
|---|---|---|
| 사용자/인증 | `users`, `auth_sessions`, `mfa_methods` | 사용자, 역할, 로그인 세션, MFA 관리 |
| PVE 연결 | `clusters`, `cluster_credentials` | 클러스터 엔드포인트와 암호화된 API 토큰 저장 |
| 인벤토리 | `nodes`, `workloads` | PVE 노드와 QEMU/LXC 로컬 투영 |
| 고객 소유권 | `workload_assignments` | 현재 할당과 할당 이력, 고객 객체 권한의 근거 |
| 템플릿 | `templates` | PVE 템플릿 위치와 프로비저닝 기본값 |
| 네트워크 | `ip_pools`, `ip_addresses`, `ip_allocations` | 주소 범위, 예약/할당 상태와 이력 |
| 프로비저닝 | `provision_requests` | 입력 스냅샷, 멱등성 키, 생성 상태 머신 |
| 작업 | `operations`, `pve_tasks`, `operation_outbox` | 사용자 작업과 Celery 실행, PVE UPID 추적, DB commit 후 안전한 큐 발행 |
| 관측/통제 | `sync_runs`, `audit_logs` | 동기화 실행과 변경 불가능한 감사 사건 |

핵심 식별/제약 조건:

- `clusters.id`, `workloads.id` 등 외부 노출 식별자는 UUID다.
- `workloads`: `UNIQUE(cluster_id, vmid)`; 서로 다른 클러스터의 같은 VMID는 허용한다.
- `nodes`: `UNIQUE(cluster_id, pve_name)`.
- 현재 할당은 워크로드당 하나만 존재하도록 부분 유일 인덱스를 둔다.
- `pve_tasks`: UPID를 클러스터 범위에서 유일하게 저장한다.
- 비밀 필드와 감사 로그 상세에는 민감정보를 넣지 않는다.

## 5. API 목록

상세 요청/응답, 권한, 오류 규약은 `docs/api-design.md`를 따른다.

### 인증과 사용자

- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`
- `POST /api/v1/auth/mfa/challenge`, `POST /api/v1/auth/mfa/verify`
- `GET|POST /api/v1/admin/users`
- `GET|PATCH|DELETE /api/v1/admin/users/{user_id}`
- `POST /api/v1/admin/users/{user_id}/reset-password`
- `GET|POST /api/v1/admin/organizations`
- `PATCH|DELETE /api/v1/admin/organizations/{organization_id}`

### 클러스터와 인벤토리

- `GET|POST /api/v1/admin/clusters`
- `GET|PATCH|DELETE /api/v1/admin/clusters/{cluster_id}`
- `POST /api/v1/admin/clusters/{cluster_id}/test-connection`
- `POST /api/v1/admin/clusters/{cluster_id}/sync`
- `POST /api/v1/admin/clusters/{cluster_id}/rotate-credential`
- `GET /api/v1/admin/nodes`
- `GET /api/v1/admin/nodes/{node_id}`
- `GET /api/v1/admin/workloads`, `GET /api/v1/admin/workloads/{workload_id}`
- `GET /api/v1/me/workloads`, `GET /api/v1/me/workloads/{workload_id}`

### 할당과 전원 제어

- `POST /api/v1/admin/workloads/{workload_id}/assign`
- `DELETE /api/v1/admin/workloads/{workload_id}/assignment`
- `GET /api/v1/admin/workloads/{workload_id}/assignments`
- `POST /api/v1/admin/workloads/{workload_id}/actions/{action}`
- `POST /api/v1/me/workloads/{workload_id}/actions/{action}`

관리자 VM 콘솔은 `/console/{workload_id}` 전용 브라우저 창에서 연다. 새 창은 기존 HttpOnly 갱신 세션으로 관리자 권한과 VM 정보를 다시 확인한 뒤 일회용 콘솔 세션을 생성하며, URL에는 workload ID 외의 인증 정보나 PVE 티켓을 포함하지 않는다. 브라우저가 팝업을 차단하면 관리자 화면의 기존 콘솔 오버레이로 대체한다.
활성 콘솔 잠금은 사용자와 workload 조합을 기준으로 적용한다. 같은 사용자는 서로 다른 VM 콘솔을 동시에 사용할 수 있지만 같은 VM 콘솔을 중복 연결할 수 없다.

### 템플릿과 프로비저닝

- `GET|POST /api/v1/admin/templates`
- `GET|PATCH|DELETE /api/v1/admin/templates/{template_id}`
- `POST /api/v1/admin/provision-requests`
- `GET /api/v1/admin/provision-requests`
- `GET /api/v1/admin/provision-requests/{request_id}`
- `POST /api/v1/admin/provision-requests/{request_id}/cancel`

### IPAM

- `GET|POST /api/v1/admin/ip-pools`
- `GET|PATCH|DELETE /api/v1/admin/ip-pools/{pool_id}`
- `GET /api/v1/admin/ip-pools/{pool_id}/addresses`
- `POST /api/v1/admin/ip-pools/{pool_id}/reservations`
- `DELETE /api/v1/admin/ip-allocations/{allocation_id}`

### 작업과 감사

- `GET /api/v1/admin/operations`, `GET /api/v1/admin/operations/{operation_id}`
- `GET /api/v1/me/operations/{operation_id}` (본인이 요청한 허용 작업만)
- `GET /api/v1/admin/audit-logs`
- `GET /api/v1/admin/audit-logs/{audit_id}`
- `GET /api/v1/health/live`, `GET /api/v1/health/ready`

모든 상태 변경 API는 감사 대상이다. 비동기 API는 `202 Accepted`와 `operation_id`를 반환한다. 생성 요청은 `Idempotency-Key`를 필수로 받는다.

## 6. 개발 단계

### 단계 0 — 기반과 품질 게이트

목표:

- Backend/Frontend/Worker의 프로젝트 구조와 Docker Compose 개발 환경을 정의한다.
- PostgreSQL, Redis, FastAPI, Celery, Next.js의 최소 실행 경로를 구성한다.
- Ruff, mypy, pytest, Playwright, TypeScript 검사와 CI 품질 게이트를 고정한다.
- 설정 계층, 구조화 로그, 상관관계 ID, 상태 확인 엔드포인트를 마련한다.

주요 모델/API:

- Alembic 기반 빈 스키마 기준선
- `GET /health/live`, `GET /health/ready`

테스트:

- 컨테이너 기동/상태 확인 smoke test
- 설정 누락 및 잘못된 설정 실패 테스트
- CI에서 lint, type check, unit test 실행

완료 조건:

- 깨끗한 환경에서 한 명령으로 개발 스택이 기동한다.
- API, 워커, DB, Redis의 준비 상태를 구분해 진단할 수 있다.
- CI가 품질 검사 실패를 병합 차단 조건으로 사용한다.

### 단계 1 — 인증, 권한, 감사 기반

목표:

- 관리자/고객 계정, 안전한 로그인 세션, 관리자 MFA를 구현한다.
- 중앙 권한 검사와 고객 객체 소유권 검사 패턴을 확립한다.
- 민감정보 마스킹과 append-only 감사 기록을 구현한다.

주요 모델/API:

- `users`, `auth_sessions`, `mfa_methods`, `audit_logs`
- 인증 및 관리자 사용자 API

테스트:

- 역할별 허용/거부 매트릭스 단위/통합 테스트
- 세션 고정, CSRF, 로그아웃, 비활성화, MFA 우회 방지 테스트
- 감사 로그 생성 및 비밀 마스킹 테스트

완료 조건:

- 고객이 관리자 API에 접근할 수 없고, 비활성 사용자 세션은 즉시 거부된다.
- 모든 사용자/권한 변경에 actor, target, 결과, request ID가 기록된다.
- 로그와 API 응답에서 암호/토큰 비밀이 검출되지 않는다.

### 단계 2 — 클러스터 등록과 통합 인벤토리

목표:

- 여러 PVE 클러스터 등록, 연결 시험, 자격 증명 회전을 제공한다.
- 노드, VM, CT를 주기적으로 동기화하고 stale 상태를 표시한다.
- 동일 VMID가 다른 클러스터에 존재하는 경우를 올바르게 처리한다.

주요 모델/API:

- `clusters`, `cluster_credentials`, `nodes`, `workloads`, `sync_runs`
- 클러스터 관리, 인벤토리 조회, 수동 동기화 API

테스트:

- PVE API 계약 테스트용 mock/fake server
- TLS 실패, 인증 실패, 타임아웃, 부분 응답, 중복 VMID 테스트
- 사라진 리소스의 soft-delete/tombstone 및 재등장 테스트

완료 조건:

- 최소 두 클러스터의 노드/VM/CT가 한 화면/API에 통합 조회된다.
- `(cluster_id, vmid)` 충돌 없이 같은 VMID가 공존한다.
- PVE 장애 시 마지막 관측 시간과 stale 상태가 정확히 노출된다.
- 저장된 토큰은 DB 덤프만으로 복호화할 수 없다.

### 단계 3 — 고객 할당과 제한된 전원 제어

목표:

- 관리자가 VM/CT를 고객에게 할당/회수하고 이력을 조회한다.
- 고객이 자기 워크로드만 보고 허용된 전원 작업을 비동기로 실행한다.
- 실행 전후 PVE 상태를 재확인하고 UPID를 추적한다.

주요 모델/API:

- `workload_assignments`, `operations`, `pve_tasks`
- 할당 및 관리자/고객 전원 작업 API

테스트:

- 수평 권한 상승(IDOR) 테스트: 다른 고객 UUID, 추측 ID, 과거 소유 리소스
- 중복 클릭/재시도 멱등성, UPID 성공/실패/타임아웃 테스트
- 할당 변경과 진행 중 작업의 경쟁 조건 테스트

완료 조건:

- 고객 목록/상세/작업에서 소유권 밖 리소스는 일관되게 `404` 처리된다.
- 전원 작업마다 operation, UPID, 최종 PVE 결과와 감사 사건이 연결된다.
- 로컬 캐시와 PVE가 다르면 PVE 조회 결과로 상태가 교정된다.

### 단계 4 — 템플릿과 비동기 VM 프로비저닝

목표:

- PVE QEMU 템플릿을 등록/동기화한다.
- 템플릿 복제, 하드웨어 설정, Cloud-Init 적용, 시작을 상태 머신으로 실행한다.
- 실패 시 재시도 가능 여부와 정리 필요 상태를 명확히 표시한다.

주요 모델/API:

- `templates`, `provision_requests`, 확장된 `operations`, `pve_tasks`
- 템플릿 및 프로비저닝 API

테스트:

- 단계별 PVE 오류 주입, 워커 재시작, 메시지 중복 전달 테스트
- 멱등성 키와 VMID 중복/고갈 테스트
- UPID 폴링 및 장기 실행/취소 경계 테스트

완료 조건:

- API 요청은 즉시 `202`를 반환하고 워커가 생성 단계를 이어간다.
- 같은 멱등성 키 재요청이 VM을 중복 생성하지 않는다.
- 모든 PVE 비동기 단계의 UPID와 최종 결과가 저장된다.
- 실패한 요청은 안전하게 재시도하거나 `NEEDS_ATTENTION`으로 운영자에게 노출된다.

### 단계 5 — IP 풀과 자동 네트워크 할당

목표:

- IPv4/IPv6 풀, 제외/예약 주소, 게이트웨이/DNS/브리지/VLAN을 관리한다.
- VM 생성 과정에서 주소를 원자적으로 예약하고 Cloud-Init에 적용한다.
- PVE 설정 검증 후 할당을 확정하고 실패 시 격리 후 회수한다.

주요 모델/API:

- `ip_pools`, `ip_addresses`, `ip_allocations`
- IP 풀/주소 조회/예약/해제 API, 프로비저닝 요청의 IP 선택 필드

테스트:

- 동시 예약에서 중복 IP가 없는지 확인하는 DB 경쟁 테스트
- CIDR, 예약 범위, IPv4/IPv6, 주소 고갈 테스트
- 생성 실패/취소/삭제 후 주소 상태 전이 테스트

완료 조건:

- 병렬 생성에서도 한 IP가 둘 이상의 활성 할당에 연결되지 않는다.
- 할당된 IP가 Cloud-Init/PVE 설정과 일치함을 검증한다.
- 실패 작업이 주소를 영구 잠그거나 즉시 위험하게 재사용하지 않는다.

### 단계 6 — 운영 안정화와 출시

목표:

- 재조정, 보존 정책, 백업/복구, 관측성, 속도 제한, 장애 대응을 완성한다.
- 실제 PVE staging 환경에서 종단 간 검증 및 보안 검토를 수행한다.
- 운영 런북과 키/토큰 회전 절차를 마련한다.

주요 모델/API:

- `sync_runs`, 보존/재조정 작업, 운영 지표
- 작업/감사 조회 필터와 readiness 강화

테스트:

- Playwright 관리자/고객 핵심 여정
- PostgreSQL 복구, 워커/Redis 재시작, PVE 단절/복구 훈련
- 부하, 장시간 작업, 보안 테스트 및 의존성/이미지 스캔

완료 조건:

- staging에서 다중 클러스터 등록 → 동기화 → 생성 → IP 할당 → 고객 할당 → 고객 전원 제어가 통과한다.
- RPO/RTO 목표에 맞춘 백업 복구 훈련 결과가 기록된다.
- 고위험 보안 발견 사항이 없고 운영·장애·복구·회전 런북이 승인된다.
- 핵심 SLI(동기화 지연, 작업 성공률/대기시간, PVE 오류율)에 경보가 설정된다.

## 7. 보안 위험과 대응 우선순위

| 위험 | 영향 | 핵심 대응 |
|---|---|---|
| 고객의 다른 고객 VM 접근(IDOR) | 타 고객 VM 조회/중단 | 서버 측 소유권 쿼리, deny-by-default, 과거 할당 제외, 권한 회귀 테스트 |
| PVE 토큰 유출 | 전체 인프라 장악 | envelope encryption, 키 분리/회전, 최소 권한 토큰, 로그 마스킹 |
| 클러스터 URL을 통한 SSRF | 내부망 탐색/메타데이터 탈취 | HTTPS만 허용, IP/포트 allowlist, DNS 재검증, 리디렉션 금지 |
| TLS 검증 우회/MITM | 토큰 및 작업 탈취 | 검증 필수, 사설 CA 지원, 인증서 오류 fail closed |
| 중복/재전송 작업 | VM/IP 중복 생성 | 멱등성 키, DB 유일 제약, 상태 머신, 작업 락 |
| 캐시 상태를 진실로 오인 | 잘못된 제어/표시 | `observed_at`, stale 표시, 변경 전 PVE 재검증, 주기 재조정 |
| Celery/Redis 메시지 변조 또는 비밀 노출 | 권한 작업 위조/토큰 유출 | 신뢰 네트워크/TLS/ACL, ID만 전달, 워커에서 DB 권한 재확인 |
| Cloud-Init 비밀 노출 | 고객 VM 계정 탈취 | SSH 키 기본, 평문 미저장, 로그/감사 redaction, 일회성 전달 |
| IP 중복 할당 | 네트워크 충돌/서비스 장애 | PostgreSQL 잠금/유일 제약, 예약 상태, 검증 및 격리 시간 |
| 감사 로그 변조/누락 | 책임 추적 상실 | append-only 권한, 실패까지 기록, 외부 불변 보관/무결성 체인 |
| 강제 stop 남용 | 데이터 손상 | 정책 제한, 재확인, rate limit, 명확한 감사 기록 |
| 공급망/컨테이너 취약점 | 서비스 침해 | 버전 고정, SBOM/스캔, non-root, 최소 이미지, 정기 업데이트 |

상세 위협 모델과 통제는 `docs/security-model.md`에 정의한다.

## 8. 테스트 전략

### 테스트 계층

- 단위 테스트: 권한 정책, 상태 머신, CIDR/IP 선택, 암호화 경계, PVE 응답 변환.
- DB 통합 테스트: 실제 PostgreSQL에서 제약 조건, 트랜잭션, 부분 인덱스, 동시 IP 예약을 검증한다. SQLite로 대체하지 않는다.
- API 통합 테스트: FastAPI 의존성, 인증/CSRF, 역할 및 객체 권한, 오류 포맷을 검증한다.
- PVE 계약 테스트: 기록된 비민감 fixture와 fake PVE 서버로 응답/오류/UPID 폴링을 검증하고 staging PVE에 대한 별도 smoke test를 둔다.
- Celery 통합 테스트: eager mode만 믿지 않고 실제 Redis/worker 조합에서 재시도, 중복 전달, 워커 종료를 검증한다.
- E2E 테스트: Playwright로 관리자와 고객 핵심 여정 및 고객 간 격리를 검증한다.
- 비기능 테스트: 부하, 장애 주입, 백업 복구, 보안 스캔, 접근성, 브라우저 호환성을 검증한다.

### 필수 회귀 매트릭스

- 역할 × 엔드포인트 × 소유/비소유/과거 소유 리소스.
- 클러스터 정상/지연/단절/인증 실패/부분 응답.
- 작업 성공/실패/타임아웃/취소/중복 메시지/워커 재시작.
- IPv4/IPv6, 풀 고갈, 제외 주소, 동시 요청, 실패 후 회수.
- 동일 VMID를 가진 서로 다른 클러스터.

### 품질 게이트

- Ruff format/check, mypy strict에 가까운 점진적 정책, pytest, TypeScript typecheck, ESLint, Playwright 핵심 smoke를 CI에서 수행한다.
- 마이그레이션 upgrade와 downgrade 또는 명시된 비가역 정책을 테스트한다.
- 핵심 보안 정책과 상태 머신은 높은 분기 커버리지를 요구하되 전체 숫자만으로 품질을 판단하지 않는다.
- 외부 PVE 호출은 타임아웃을 필수로 하고 재시도 가능 오류만 제한적으로 재시도하는 테스트를 둔다.

## 9. 범위 밖 및 후속 후보

- PVE 백업/복원, 스냅샷, 라이브 마이그레이션, HA 정책 관리
- 고객 조직, 팀, 세분화된 사용자 역할과 승인 워크플로
- SPICE 콘솔, 청구/사용량 정산
- 템플릿 이미지 빌드 파이프라인과 구성 관리
- 다중 리전 액티브-액티브 제어 플레인
- 외부 IPAM, CMDB, Vault/HSM, SIEM 연동

이 기능들은 초기 권한·작업·감사 모델을 재사용하는 별도 단계로 추가한다.
