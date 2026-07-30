# Scheduler, Outbox와 정기 유지보수 설계 결정

## 문서 상태

- 상태: 구현 완료
- 기준일: 2026-07-24
- 범위: `docs/tasks/02-scheduler-outbox-maintenance.md`

## 전달 보장

사용자 operation 생성 트랜잭션은 `operations`, append-only audit,
`operation_outbox`를 함께 commit한다. API는 commit 뒤 즉시 publish를 시도하지만
Redis 오류나 publish 뒤 프로세스 종료가 발생해도 outbox dispatcher가 복구한다.

전달 의미는 at-least-once다. publish 성공 뒤 DB 완료 표시가 실패하면 같은
`celery_task_id`가 재전송될 수 있다. 작업 워커는 operation UUID를 다시 읽고 terminal
상태, PVE task 존재 여부, heartbeat lease를 확인하므로 중복 PVE 명령을 만들지 않는다.

outbox payload는 `operation_id` 하나만 포함한다. PVE endpoint, token, UPID,
Cloud-Init 입력과 사용자 request body를 넣지 않는다.

## Scheduler와 fencing

Celery beat는 정기 메시지를 생성하며 실제 단일 실행 보장은 PostgreSQL이 담당한다.

1. 작업 이름의 transaction advisory lock을 획득한다.
2. `scheduler_leases` 행을 잠그고 미만료 타 소유자를 거부한다.
3. lease 획득·이전 시 fencing token을 증가시킨다.
4. 장시간 inventory 네트워크 호출은 DB transaction 밖에서 수행한다.
5. 최종 DB 반영 전에 owner와 fencing token을 다시 확인한다.

따라서 beat 메시지 중복, worker 재시작과 lease 만료 후 소유권 이전을 정상 경로로
처리한다.

## Queue와 주기

| Queue | 책임 |
|---|---|
| `operations` | 전원, VM 구성·삭제, provisioning, backup·restore |
| `inventory` | 활성 클러스터 UUID 기반 scheduled inventory sync |
| `maintenance` | outbox, watchdog, retention, quarantine, metadata reconciliation |

기본 주기:

- outbox dispatch 5초
- operation/provisioning watchdog 30초
- inventory dispatch와 IP quarantine 해제 60초
- backup metadata reconciliation 및 상태 점검 5분
- 보존 작업 1일

## 보존

- audit: 365일
- 만료 refresh token: 만료 뒤 30일
- 완료 outbox와 성공/skip maintenance run: 7일
- sync run 상세: 30일

보존 기간은 환경 변수로 변경할 수 있으며 audit 삭제는 DB transaction flag를 설정한
maintenance 경로에서만 허용한다.
