# 자동 Inventory Sync와 Reconciliation 설계 결정

## 문서 상태

- 상태: 구현 완료
- 기준일: 2026-07-24
- 범위: `docs/tasks/03-inventory-sync-reconciliation.md`

## 동기화 경계

각 요청은 `sync_runs`의 generation 하나를 사용한다. Celery에는 sync run UUID만
전달하며 PVE endpoint, token, 응답 원문을 넣지 않는다.

PVE nodes, guests, storages 조회는 DB transaction 밖에서 병렬 수행한다. 각 scope는
독립적으로 검증하며 일부 scope가 실패하면 성공한 투영만 갱신하고 run을 `PARTIAL`로
기록한다. 누락 tombstone은 `FULL` run에서 해당 scope 응답이 완전한 경우에만
적용한다.

클러스터별 lease와 fencing token을 DB 반영 전 다시 확인한다. 활성 전체 sync는
클러스터당 하나, 대상 sync는 workload당 하나로 제한한다.

## Drift와 소유권

다음 외부 변경은 finding과 change event로 기록한다.

- 전체 응답에서 workload 누락
- node 이동
- 이름·종류·template·CPU·메모리·disk 변경
- 활성 로컬 operation 없는 전원 상태 변경

누락 workload는 `is_present=false`로 바뀌어 고객 목록과 새 작업 대상에서 제외된다.
현재 organization assignment, assignment 이력과 IP allocation은 유지한다. 재등장하면
같은 workload UUID를 복원하고 외부 삭제 finding을 자동 해결한다.

## Freshness

클러스터 stale 기준은 `max(INVENTORY_STALE_AFTER_SECONDS,
sync_interval_seconds × 3)`이다. 관리자 API는 마지막 전체 성공과 최근 run 상태를
제공한다. 고객 API는 안전한 workload 관측 시각과 stale 여부만 제공하며 stale
workload의 전원 작업을 서버에서 거부한다.

## 호환성

기존 `/workloads/import`와 `workloads.node` 계약은 유지한다. 수동 import로 재등장한
workload의 `missing_since`는 제거하지만 generation 의미는 scheduled sync가 관리한다.
비동기 sync 응답의 `operation_id`는 VM operation이 아니라 `sync_runs.id`다.
