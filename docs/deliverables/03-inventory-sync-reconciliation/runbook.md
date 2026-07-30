# Inventory Sync와 Reconciliation 운영 Runbook

## 정상 확인

관리자 `동기화와 재조정` 화면 또는 다음 API를 확인한다.

- `/api/v1/admin/inventory/freshness`
- `/api/v1/admin/inventory/sync-runs`
- `/api/v1/admin/inventory/reconciliation/findings`

Prometheus 주요 지표:

- `pvemaster_inventory_stale_clusters`
- `pvemaster_reconciliation_findings{status,severity}`
- `pvemaster_job_queue_length{queue="inventory"}`

## 부분 실패

1. sync run의 `partial_failure`, `error_code`, `partial_errors`를 확인한다.
2. 실패 scope가 있는 run에서는 누락 리소스가 tombstone 되지 않았음을 확인한다.
3. 인증·권한·TLS 오류는 credential과 CA를 먼저 복구한다.
4. timeout, rate limit, upstream 오류는 Celery backoff 또는 다음 정기 sync를
   기다린다. 수동 PVE 변경으로 맞추지 않는다.

## 누락 workload

1. finding의 cluster, workload, 마지막 관측과 변경 요약을 확인한다.
2. assignment와 IP가 유지되는지 확인한다.
3. PVE에서 실제 삭제인지 일시적 권한/응답 누락인지 판별한다.
4. 일시 누락이면 전체 sync로 재등장을 확인한다.
5. 실제 삭제면 단계 4의 복구·정리 흐름에 따라 소유권/IP를 별도 승인으로 처리하고
   해결 근거를 기록한다.

## Queue 또는 worker 장애

sync 요청은 `QUEUED` run으로 PostgreSQL에 남는다. inventory dispatcher가 정기적으로
미실행 run을 다시 publish한다. Redis 복구 뒤 같은 run UUID가 재전송되는 것은
정상이며 lease와 generation 제약이 중복 반영을 막는다.

## 배포와 rollback

1. Alembic `20260724_0016` 적용
2. inventory worker와 maintenance worker 배포
3. API 배포
4. frontend 배포
5. 첫 전체 sync의 generation, stale 해제와 finding을 확인

rollback 전 scheduler와 inventory worker를 중지한다. downgrade는
`QUEUED`/`PARTIAL` run을 `FAILED`로 정규화하고 단계 3 투영·finding 데이터를
삭제하므로 운영 사본을 먼저 보관한다.
