# 단계 4 — 관리자 Operation 센터와 복구 흐름 (P0)

## 목표

관리자가 비동기 작업의 진행, 실패, 취소, 재시도와 수동 복구를 한 화면에서 처리한다.

## 데이터 모델

- 기존 `operations`, `pve_tasks`, provisioning/backup/restore run을 기준으로 사용
- `operation_events`: 상태 전이, step, 안전한 메시지, 발생 시각
- `operation_assignments`: 담당 관리자, 확인 시각, 해결 메모
- 재시도는 새 operation과 원본 operation 관계로 추적

## API

- 관리자 operation 목록·상세·event timeline
- 상태, 종류, cluster, organization, actor, 기간, error_code 필터
- 가능한 작업만 노출하는 `available_actions`
- cancel, retry, acknowledge, assign, resolve-manually
- provisioning request cancel과 안전한 retry
- 대량 export는 비동기 생성, step-up MFA와 만료 URL 사용

## Worker

- PVE task 장기 polling과 lease heartbeat
- stuck/heartbeat timeout 탐지
- 결과 불명확 상태의 대상 재조회
- retry 가능/불가능 오류 분류
- assignment 회수나 사용자 비활성화 시 실행 전 권한 재검사
- 안전하지 않은 실패는 자동 삭제 대신 `NEEDS_ATTENTION`

## 관리자 UI

- 상태·종류별 operation queue
- 전체 step과 UPID의 안전한 요약
- 원인, 영향 대상, 다음 권장 조치
- 취소·재시도·담당자 지정·수동 해결
- 관련 workload, organization, audit, backup으로 이동
- 새로고침 후에도 진행 상태 복구

## 고객 UI

- 고객 자신이 요청했고 현재 소유한 작업만 조회
- 내부 UPID, cluster, node, PVE 오류 원문은 숨김
- 고객이 취할 수 있는 재시도 또는 문의 안내만 표시

## 테스트

- 성공, 실패, timeout, cancel, cancel 불가, 중복 retry
- worker 중단 후 복구
- 결과 불명확 submission
- 과거 소유 operation 접근
- 고객 간 IDOR
- 담당자 동시 변경과 version conflict

## 완료 조건

- 운영자가 실패 작업을 로그 원문 없이도 분류하고 처리할 수 있다.
- 고객 작업이 새로고침이나 재로그인 후에도 안전하게 조회된다.
- 취소와 재시도가 원본 작업과 감사 로그에 연결된다.


---

[전체 작업 인덱스로 돌아가기](README.md)
