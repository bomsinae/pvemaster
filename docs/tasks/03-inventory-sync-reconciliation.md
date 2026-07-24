# 단계 3 — 자동 Inventory Sync와 Reconciliation (P0)

## 목표

PVE 실제 상태를 주기적으로 관측하고 로컬 인벤토리, 소유권, IP, 작업 상태의 drift를
안전하게 탐지한다.

## 데이터 모델

- `nodes`, `workloads`에 `observed_at`, `sync_generation`, `is_present`를 일관되게 사용
- `sync_runs`: cluster, scope, generation, status, partial_failure, counts, error_code
- `reconciliation_findings`: 종류, 심각도, 대상, 최초/최근 관측, 상태, 해결 정보
- 필요 시 `workload_change_events`: 외부 변경 감지 요약

## API

- 클러스터 전체/부분 sync 요청과 `operation_id` 반환
- sync run 목록·상세와 변경 건수 조회
- reconciliation finding 목록·상세·확인·해결 처리
- stale 임계치와 마지막 전체 성공 시각 노출
- 수동 재조정 요청

## Worker

1. 클러스터별 sync generation을 생성한다.
2. node, QEMU, LXC, template, storage를 제한된 batch로 조회한다.
3. `(cluster_id, natural_key)` 기준으로 upsert한다.
4. 전체 sync가 성공한 경우에만 누락 리소스를 `is_present=false`로 바꾼다.
5. 부분 실패에서는 누락을 삭제로 판정하지 않는다.
6. 외부 삭제, node 이동, 사양 drift, 예상하지 못한 전원 상태를 finding으로 만든다.
7. 중요 작업 완료 직후 대상 workload의 부분 sync를 실행한다.

## 관리자 UI

- 클러스터별 마지막 성공/실패/소요 시간과 stale 상태
- 수동 sync와 진행 상태
- 추가·변경·누락된 리소스 요약
- drift finding 목록, 심각도, 담당자와 해결 상태
- VM/CT 상세의 PVE 관측값과 로컬 메타데이터 구분

## 고객 UI

- 마지막 확인 시각과 stale 경고를 명확히 표시한다.
- stale 상태에서는 위험한 전원 작업을 서버 정책에 따라 제한한다.
- 내부 cluster/node/sync 상세는 노출하지 않는다.

## 테스트

- 같은 VMID가 여러 클러스터에 존재
- 전체 성공 후 누락 tombstone
- 부분 응답에서는 tombstone 금지
- 삭제된 workload 재등장
- 외부 node 이동과 사양 변경
- sync와 할당/전원/프로비저닝의 경쟁
- 인증, TLS, timeout, rate limit, 잘못된 PVE 응답

## 완료 조건

- 모든 활성 클러스터가 설정 주기로 자동 동기화된다.
- PVE 단절 시 마지막 정상 상태와 stale 여부가 정확히 표시된다.
- 외부 삭제나 변경이 소유권/IP를 자동 해제하지 않고 finding으로 남는다.
- 누락 workload가 고객 목록과 신규 작업 대상에서 안전하게 제외된다.


---

[전체 작업 인덱스로 돌아가기](README.md)
