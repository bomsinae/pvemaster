# Scheduler와 Outbox 운영 Runbook

## 정상 확인

`GET /api/v1/admin/operations/status`의 `scheduler` 배열에서 각 작업의 최신 상태,
마지막 성공 시각, 처리 건수와 안전한 오류 코드를 확인한다.

Prometheus에서는 다음 지표를 수집한다.

- `pvemaster_scheduler_job_last_success_timestamp_seconds{job=...}`
- `pvemaster_scheduler_job_failed{job=...}`
- `pvemaster_job_queue_length{queue="operations|inventory|maintenance"}`

최신 scheduler 실행이 `FAILED`면 관리자 상태 API에
`SCHEDULER_JOB_FAILED` 경보가 표시된다.

## 장애별 대응

### Redis publish 실패

1. operation이 DB에 `QUEUED`로 남았는지 확인한다.
2. 연결된 outbox가 `PENDING`, `last_error_code=BROKER_UNAVAILABLE`인지 확인한다.
3. Redis 복구 뒤 최대 5분 backoff 안에 attempt가 증가하고 `PUBLISHED`가 되는지
   확인한다.
4. 수동으로 동일 PVE 명령을 실행하지 않는다. watchdog과 worker 멱등성에 맡긴다.

### Scheduler 중복 또는 lease 정체

1. `scheduler_leases.lease_expires_at`과 최신 maintenance run을 확인한다.
2. 정상 실행 중인 owner가 있으면 기다린다.
3. 만료 뒤 새 owner의 fencing token이 증가하는지 확인한다.
4. lease 행을 임의 삭제하거나 fencing token을 낮추지 않는다.

### Stuck operation

operation watchdog은 오래된 `QUEUED`, heartbeat가 만료된 `RUNNING` operation의
outbox를 재활성화한다. PVE task/UPID가 있으면 기존 task를 poll하며 새 명령을 제출하지
않는다. 반복 실패 시 operation과 PVE 실제 상태를 대조하고 다음 단계의 operation
복구 흐름으로 인계한다.

### PostgreSQL 장애

DB가 기준 시스템이므로 scheduler와 worker는 실행을 계속하지 않는다. Redis 메시지만
근거로 PVE를 변경하지 않는다. PostgreSQL 복구와 migration 확인 뒤 maintenance
worker를 먼저 열어 outbox/watchdog 상태를 복구한 후 operations worker를 개방한다.

## 배포 순서

1. Alembic `20260724_0015` 적용
2. `worker-maintenance` 배포
3. `worker-operations`, `worker-inventory` 배포
4. 단일 `scheduler` 배포
5. API/frontend 배포
6. 운영 상태 API와 Prometheus 지표 확인

rollback은 scheduler를 먼저 중지하고 worker를 중지한 뒤 애플리케이션을 되돌린다.
`0015` downgrade는 새 테이블 데이터를 삭제하므로 운영 데이터가 생성된 뒤에는 사본을
보관하고 승인된 점검 창에서만 수행한다.
