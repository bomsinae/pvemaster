# Operation 센터 복구 Runbook

## 실패 작업 분류

1. 관리자 `Operation 센터`에서 상태, 종류, 오류 코드로 범위를 좁힌다.
2. 영향 workload와 organization, 마지막 heartbeat, event timeline을 확인한다.
3. `retryable=true`라도 cluster와 대상 실제 상태를 먼저 확인한다.
4. `NEEDS_ATTENTION` 또는 `MANUAL_REVIEW`는 담당자를 지정하고 확인 처리한다.
5. PVE/PBS의 실제 결과와 소유권을 확인한 뒤 해결 근거를 남겨 수동 해결한다.

원본 UPID, PVE 오류 원문, token과 endpoint는 티켓이나 고객 메시지에 복사하지 않는다.
화면의 `upid:<reference>`와 operation UUID로 내부 로그를 연결한다.

## 취소

`CANCEL`은 `QUEUED`에서만 노출된다. 이미 `RUNNING`이면 API가
`409 OPERATION_CANCEL_UNSAFE`를 반환하며 강제 종료로 바꾸지 않는다. 취소 후 outbox가
남아 worker에 전달되더라도 worker는 terminal `CANCELLED`를 확인하고 PVE 호출을 하지
않는다.

## 안전한 재시도

재시도 버튼이 없으면 새 요청을 직접 만들지 않는다.

- timeout 이후 UPID가 존재하면 기존 task polling을 복구한다.
- 제출 결과가 불명확하면 PVE 대상과 task/snapshot 존재 여부를 확인한다.
- 프로비저닝 clone 제출 또는 IP 예약 뒤 실패는 자동 재시도하지 않는다.
- 중복 retry 요청은 이미 생성된 같은 child ID를 반환한다.

## worker heartbeat 만료

maintenance watchdog는 오래된 `QUEUED|RUNNING` operation을 outbox에 다시 등록하고
`STUCK_DETECTED` event를 한 번 기록한다. 같은 operation UUID의 중복 배달은 정상이며,
worker는 기존 PVE task를 조회해 polling을 재개한다. reboot/reset처럼 안전한 결과
판별이 불가능하면 `NEEDS_ATTENTION`으로 전환한다.

## 배포와 rollback

1. operation worker와 maintenance worker를 일시 중지한다.
2. Alembic `20260724_0017`을 적용한다.
3. API, operation/maintenance worker, frontend 순서로 배포한다.
4. 기존 작업의 `IMPORTED` event와 queue 목록을 확인한다.
5. worker를 재개하고 stale 작업의 redelivery와 heartbeat를 확인한다.

downgrade 전 새 상태 작업을 모두 terminal 처리하고 `operation_events`,
`operation_assignments`를 보관한다. downgrade는 `CANCELLED|NEEDS_ATTENTION`을
`FAILED`로 정규화하고 단계 4의 timeline/assignment 데이터를 삭제한다.
