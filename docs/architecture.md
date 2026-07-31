# 시스템 아키텍처

## 1. 개요

PVE Master는 PVE 위에 위치하는 제어 플레인이다. 브라우저는 FastAPI만 호출하며 PVE 자격 증명이나 PVE API 주소를 알지 못한다. PostgreSQL은 사용자, 소유권, 요청, 작업 및 감사 데이터의 기준이고, PVE는 실제 노드/VM/CT 구성과 실행 상태의 기준이다.

```text
관리자/고객 브라우저
        |
        | HTTPS + session/CSRF
        v
 Next.js Web  ----same-origin---->  FastAPI
                                      |  |
                            SQLAlchemy|  | enqueue operation ID only
                                      v  v
                                PostgreSQL   Redis
                                                |
                                                v
                                         Celery workers
                                           |       |
                         inventory/operation       | UPID polling
                                           v       v
                                   Proxmox VE clusters
```

## 2. 컴포넌트와 책임

### Next.js Web

- 관리자와 고객 UI, 입력 검증, stale/작업 진행 상태 표시를 담당한다.
- FastAPI의 same-origin API만 사용한다.
- PVE URL, API 토큰, 내부 UPID 상세를 브라우저 저장소에 보관하지 않는다.
- UI 가드는 편의 기능일 뿐이며 최종 권한 판단은 API가 한다.

### FastAPI

- 인증, CSRF, 역할 및 객체 소유권 검사를 수행한다.
- REST API와 일관된 오류 형식을 제공한다.
- 짧은 DB 변경은 요청 내에서 처리하고 PVE 네트워크 작업은 operation을 만든 뒤 워커로 위임한다.
- PVE 자격 증명 복호화는 실제 호출 직전에 서버 측에서만 수행한다.
- 상태 변경 요청과 결과를 감사 로그에 기록한다.
- 관리자 QEMU noVNC 및 LXC termproxy 콘솔에 짧은 일회용 세션을 발급하고 PVE WebSocket을 양방향 중계한다.

### Celery workers

큐를 목적별로 분리한다.

- `inventory`: 클러스터 인벤토리 동기화와 재조정.
- `operations`: 전원, 생성, 복제, 설정 작업.
- `maintenance`: UPID 회수, 보존, stuck 작업 탐지, IP 격리 해제.

메시지에는 `operation_id`, `cluster_id` 같은 내부 ID만 전달한다. 토큰, Cloud-Init 비밀, 전체 사용자 입력은 넣지 않는다. 워커는 실행 직전에 DB에서 operation과 현재 권한/대상 상태를 다시 읽는다.

### PostgreSQL

- 사용자와 세션, 클러스터 메타데이터, 암호화된 자격 증명, 인벤토리 투영, 소유권, IPAM, operation, UPID, 감사 로그를 저장한다.
- 유일 제약, 부분 인덱스, 행 잠금으로 중복 생성/할당을 방지한다.
- 외부 PVE 변경과 한 트랜잭션으로 묶을 수 없으므로 의도(intent)와 결과를 상태 머신으로 기록한다.

### Redis

- Celery broker/result backend, 짧은 TTL의 속도 제한과 보조 락에 사용한다.
- 업무 상태의 기준으로 사용하지 않는다. 워커는 Redis 메시지가 중복되거나 사라질 수 있다는 전제로 설계한다.

### PVE adapter

- PVE HTTP API 호출, 인증 헤더, TLS, timeout, 오류 분류, 응답 정규화, UPID 폴링을 한 경계에 캡슐화한다.
- 클러스터별 동시성 제한과 지수 백오프를 적용한다.
- HTTP 5xx/일시 네트워크 오류만 제한적으로 재시도하고 인증/권한/검증 오류는 즉시 실패시킨다.
- 리디렉션은 따르지 않는다.

## 3. 도메인 경계

- Identity & Access: 사용자, 세션, MFA, 역할.
- Cluster Registry: 클러스터 엔드포인트와 자격 증명 생명주기.
- Inventory: 노드와 워크로드의 PVE 관측 투영.
- Ownership: 고객 할당 및 이력.
- Operations: 사용자 의도, Celery 실행, PVE UPID 추적.
- Provisioning: 템플릿 복제와 Cloud-Init 상태 머신.
- IPAM: 풀, 주소, 예약과 할당.
- Backup: PVE에 등록된 PBS storage 참조, 워크로드 백업 실행과 snapshot 메타데이터.
- Audit: 보안/관리 사건의 append-only 기록.

각 경계는 한 프로세스 안의 모듈로 시작하는 모듈러 모놀리스로 구현한다. 초기 규모에서 분산 서비스의 운영 복잡성을 피하면서, 큐 기반 작업과 명확한 도메인 인터페이스로 향후 분리를 가능하게 한다.

## 4. 상태와 일관성 모델

### 인벤토리 투영

`nodes`와 `workloads`는 PVE 데이터의 로컬 투영이다. 각 행은 `observed_at`, `sync_generation`, `is_present`를 가진다.

1. 동기화 시작 시 `sync_runs`와 generation을 만든다.
2. PVE에서 리소스를 읽고 `(cluster_id, natural_key)` 기준 upsert한다.
3. 전체 동기화가 성공한 경우에만 이번 generation에서 보이지 않은 리소스를 `is_present=false`로 표시한다.
4. 부분 실패에서는 리소스를 사라졌다고 판정하지 않는다.
5. API는 클러스터별 stale 임계치와 마지막 성공 시각을 함께 반환한다.

PVE 외부에서 이루어진 변경도 다음 동기화에서 반영된다. 로컬 이름/상태를 PVE에 덮어써서 맞추지 않는다.

### Operation과 PVE task

`operations`는 사용자가 요청한 논리 작업이며, 한 operation은 여러 `pve_tasks`를 가질 수 있다. 예를 들어 프로비저닝은 clone UPID, config UPID, start UPID를 순서대로 만들 수 있다.

```text
PENDING -> QUEUED -> RUNNING -> SUCCEEDED
                       |  |\-> NEEDS_ATTENTION
                       |  \--> CANCEL_REQUESTED -> CANCELLED
                       \-----> FAILED
```

- 상태 전이는 DB에서 compare-and-set 방식으로 보호한다.
- Celery task ID는 실행 추적용이며 업무 식별자는 operation UUID다.
- PVE가 UPID를 반환한 뒤 워커가 죽어도 maintenance worker가 미완료 `pve_tasks`를 다시 폴링한다.
- PVE 호출 결과가 불명확한 네트워크 오류에서는 같은 생성 명령을 즉시 반복하지 않는다. 먼저 VMID/UPID/대상 상태를 조회해 결과를 판별한다.
- 취소는 best effort다. PVE가 이미 완료했거나 취소를 지원하지 않으면 최종 실제 상태를 보고한다.

### 프로비저닝 사가

기본 흐름은 다음과 같다.

1. 요청 검증 및 `Idempotency-Key` 예약.
2. 대상 클러스터/노드/스토리지/VMID 결정 및 DB에 입력 스냅샷 저장.
3. IP 주소 `RESERVED`.
4. 템플릿 clone 요청, UPID 저장 및 완료 확인.
5. CPU/메모리/디스크/네트워크와 템플릿 OS에 따른 Cloud-Init 또는 Cloudbase-Init 설정.
6. 필요 시 VM 시작, 각 PVE 작업의 UPID 추적.
7. PVE에서 생성 VM과 네트워크 설정을 재조회.
8. `workloads` 투영 갱신, IP `ASSIGNED`, 선택한 고객에게 할당.
9. operation 성공 및 감사 결과 기록.

실패 시 생성 전이면 IP를 격리 후 해제한다. clone 이후 실패하면 VM 존재 여부와 설정을 검사하여 안전한 자동 정리가 가능한 경우만 보상 삭제한다. 데이터 손실 가능성이 있거나 결과가 불명확하면 `NEEDS_ATTENTION`으로 남긴다.

## 5. VMID와 리소스 식별

- 외부 API는 내부 UUID `workload_id`를 사용한다.
- PVE 호출에 필요한 VMID는 워크로드와 cluster 관계에서 얻는다.
- DB는 `(cluster_id, vmid)`만 유일하게 강제한다.
- 자동 VMID 선택은 클러스터별 advisory lock 또는 별도 allocation 레코드로 직렬화하고 PVE의 현재 리소스 목록과 교차 확인한다.
- 이름은 식별자로 사용하지 않는다.

## 6. 동기 및 비동기 API 경계

동기 처리:

- 로그인/로그아웃, 목록/상세 조회.
- 사용자/메타데이터 변경처럼 외부 PVE 장기 호출이 없는 짧은 작업.
- 클러스터 연결 시험은 제한된 timeout 내에서 동기 실행할 수 있으나 저장과 분리한다.

비동기 처리:

- 인벤토리 전체 동기화.
- 모든 PVE 전원 작업.
- PBS 워크로드 백업 제출과 UPID 추적.
- VM 생성/복제/설정/시작과 취소.
- 오래 걸리는 자격 증명 검증이나 대량 작업.

비동기 응답은 `202 Accepted`, operation 리소스 URL, 현재 상태를 반환한다. UI는 짧은 폴링으로 시작하며 필요 시 SSE를 후속 도입한다.

## 7. 배포 구조

Docker Compose의 논리 서비스:

- `web`: Next.js, 외부 진입점 또는 reverse proxy 뒤에서 실행.
- `api`: FastAPI ASGI 서버, 수평 확장 가능.
- `worker-operations`, `worker-inventory`: 큐와 동시성 분리.
- `scheduler`: Celery beat 단일 인스턴스.
- `postgres`: 영속 볼륨, 운영에서는 관리형 DB도 가능.
- `redis`: 인증/TLS가 가능한 전용 인스턴스.
- 선택적 `proxy`: TLS 종료, 요청 크기/속도 제한, 보안 헤더.

컨테이너는 non-root, read-only root filesystem을 지향하고 비밀은 이미지나 Compose 파일에 포함하지 않는다. Alembic 마이그레이션은 API 시작 때 여러 인스턴스가 경쟁 실행하지 않도록 별도 one-shot 배포 단계에서 수행한다.

## 8. 관측성

구조화 로그 공통 필드:

- `timestamp`, `level`, `service`, `request_id`, `trace_id`
- `actor_id`(해시/UUID), `operation_id`, `cluster_id`, `workload_id`
- 오류 분류와 latency; 토큰/쿠키/Cloud-Init 비밀은 제외

핵심 지표:

- 클러스터별 마지막 성공 동기화 시각과 지연.
- PVE API latency/error/rate limit.
- 큐 깊이, operation 대기/실행 시간, 상태별 건수.
- UPID polling age와 stuck task 수.
- IP 풀 사용률과 격리/고갈 상태.
- 로그인/MFA/권한 거부율.

감사는 애플리케이션 로그와 별도 데이터 경로로 취급하며 `docs/security-model.md`의 무결성 정책을 따른다.

## 9. 장애 처리

- PVE 단절: 마지막 인벤토리를 stale로 제공하고 변경 작업은 명확히 실패/재시도 상태로 둔다.
- Redis 단절: 새 operation enqueue 실패를 숨기지 않고 DB에 `PENDING`으로 남겨 dispatcher가 재전송한다. transactional outbox 도입을 권장한다.
- 워커 종료: ack-late와 멱등 상태 전이로 재전달을 안전하게 처리한다.
- PostgreSQL 단절: 변경 요청을 거부한다. Redis만으로 계속 처리하지 않는다.
- API 재시작: operation/UPID 상태는 DB에서 복구한다.
- 동기화 부분 실패: absent 표시를 수행하지 않고 실패 범위를 기록한다.

DB commit과 Celery publish 사이 유실을 막기 위해 초기 구현부터 `operation_outbox` 테이블을 두거나 Celery publish 실패를 재전송하는 DB dispatcher를 사용한다.

## 10. 확장 결정

- 읽기 부하는 PostgreSQL 인덱스와 페이지네이션으로 우선 대응한다.
- 클러스터별 큐 라우팅/동시성 제한으로 한 장애 클러스터가 전체 작업을 고갈시키지 않게 한다.
- 대량 인벤토리는 generation upsert를 배치 처리한다.
- 향후 SSE/WebSocket은 operation 이벤트 투영을 소비하며 권한 검사를 동일하게 적용한다.
- 콘솔 WebSocket은 Redis의 일회용 grant와 사용자·workload별 활성 연결 lock만 사용하며 업무 상태로 저장하지 않는다. 연결 시 DB 역할 권한과 PVE 대상을 다시 확인하고, 고객은 활성 조직 멤버십과 현재 워크로드 할당까지 재검증한다.
- 다중 API/워커 인스턴스에서도 DB 제약과 advisory lock이 정확성의 기준이며 프로세스 메모리 락에 의존하지 않는다.
