# 단계 2 — Scheduler, Outbox와 정기 유지보수 기반 (P0)

## 목표

수동 실행이나 worker 시작 이벤트에 의존하지 않고 inventory, operation, 보존 작업을
지속적으로 수행할 기반을 마련한다.

## 데이터 모델

- `operation_outbox`: publish 대상, 상태, attempt, next_attempt_at, last_error_code
- `scheduler_leases`: 단일 실행이 필요한 작업의 lease와 fencing token
- 기존 `sync_runs`를 실제 inventory 작업 기준으로 사용
- 필요 시 `maintenance_runs`: 작업 종류, 시작/완료, 처리 건수, 오류 코드

## Worker와 배포

- `inventory`, `operations`, `maintenance` queue를 분리한다.
- 단일 Celery beat 또는 동등한 scheduler 서비스를 추가한다.
- outbox dispatcher가 미발행 operation을 재전송하게 한다.
- scheduler 중복 실행은 PostgreSQL advisory lock과 fencing으로 방지한다.
- worker 시작 복구 외에 정기 watchdog을 실행한다.
- 클러스터별 동시성 제한과 backoff를 적용한다.

## 정기 작업

- inventory sync dispatch
- 미완료 PVE UPID polling
- stuck operation 탐지
- outbox 재전송
- IP quarantine 만료 후보 탐지
- audit/session/result 보존
- backup snapshot metadata 재조정
- credential/backup/sync 상태 점검

## 보안

- Celery 인자에는 내부 UUID만 전달한다.
- scheduler와 worker 로그에 token, URL credential, Cloud-Init payload를 남기지 않는다.
- maintenance 작업도 서비스 계층과 동일한 상태 전이 규칙을 사용한다.

## 테스트

- scheduler 이중 실행
- Redis publish 실패 후 outbox 재전송
- worker 종료·재시작과 중복 메시지
- 동일 operation 동시 소비
- scheduler lease 만료와 소유권 이전
- PostgreSQL/Redis 부분 장애

## 완료 조건

- DB commit 후 queue publish 유실이 자동 복구된다.
- scheduler 또는 worker 재시작이 중복 PVE 작업을 만들지 않는다.
- 정기 작업의 마지막 성공 시각과 실패가 운영 지표에 노출된다.


---

[전체 작업 인덱스로 돌아가기](README.md)
