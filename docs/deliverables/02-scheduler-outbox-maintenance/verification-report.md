# Scheduler, Outbox와 정기 유지보수 검증 보고서

## 문서 상태

- 상태: 로컬 구현·검증 완료
- 기준일: 2026-07-24
- 범위: `docs/tasks/02-scheduler-outbox-maintenance.md`

## 구현 결과

- operation과 outbox의 동일 DB transaction 저장
- 즉시 publish 실패 뒤 지수 backoff 재전송
- stale operation과 provisioning 정기 watchdog
- PostgreSQL advisory lock, lease 만료와 fencing token
- operations, inventory, maintenance worker 및 beat 분리
- 활성 클러스터 scheduled inventory sync와 `sync_runs`
- 만료 IP quarantine 원자적 해제
- audit/session/outbox/sync/maintenance run 보존
- PBS snapshot metadata 후속 재조정
- credential/backup 상태 점검
- 관리자 상태 API와 Prometheus scheduler 지표

## 검증 결과

| 검사 | 결과 |
|---|---|
| Alembic upgrade → downgrade → upgrade | 통과 |
| Redis publish 실패 후 outbox 재전송 | 통과 |
| stale operation watchdog | 통과 |
| scheduler 중복 실행 거부 | 통과 |
| lease 만료 뒤 소유권 이전/fencing 증가 | 통과 |
| IP quarantine address/allocation 동시 해제 | 통과 |
| scheduled inventory generation 기록 | 통과 |
| Backend pytest | 123 passed |
| Ruff check | 통과 |
| mypy | 통과 |
| Frontend lint/typecheck/mock/build | 통과 |
| Playwright (Chromium/Firefox/WebKit) | 39 passed |

실제 PVE는 호출하지 않았으며 scheduled inventory는 주입한 fake loader로 검증했다.
외부 HTTP 경로는 기존 Proxmox client의 TLS 검증과 timeout 계약을 그대로 사용한다.

## 남은 운영 확인

- staging에서 세 worker와 scheduler 장시간 실행
- 실제 Redis 중단·복구 중 backoff 및 queue depth 관찰
- inventory 규모에 따른 cluster별 주기와 동시성 조정
- Alertmanager의 `SCHEDULER_JOB_FAILED` 라우팅 연결
