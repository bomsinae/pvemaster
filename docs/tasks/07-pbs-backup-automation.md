# 단계 7 — PBS 백업 자동화와 복구 검증 (P1)

## 목표

수동 백업을 조직·워크로드 정책 기반 자동 백업, 보존, 검증 흐름으로 확장한다.

## 선행 결정

- PBS prune job을 기준으로 할지 PVE Master가 prune을 요청할지 확정한다.
- 고객에게 snapshot 목록을 노출할 때 과거 소유자 데이터가 섞이지 않도록 소유권 기준을 확정한다.

## 데이터 모델

- `backup_policies`: scope, schedule, mode, retention reference, is_enabled
- `backup_policy_assignments`: organization/workload와 policy 관계
- `backup_verifications`: snapshot, metadata check, restore drill 결과
- 기존 `backup_runs`, `restore_runs`, `operations` 재사용

## API

- backup policy CRUD와 대상 preview
- 다음 실행 시각, 최근 성공, 연속 실패 조회
- 수동 skip/run-now
- snapshot metadata 재조정
- restore 검증 요청
- 고객에게 허용할 경우 자기 VM backup 상태와 복구 요청

## Worker

- timezone을 명시한 schedule dispatch
- 같은 workload의 중복 백업 방지
- missed schedule 탐지
- snapshot content 재조회와 metadata 보정
- 격리된 restore 대상에 대한 정기 복구 검증
- retention/prune은 확정된 책임 경계에서만 실행

## UI

- 관리자 backup calendar와 policy 적용 범위
- 실패, 누락, 보존 상태, 복구 검증 결과
- restore 전에 node/VMID/storage/IP/조직 영향 preview
- 고객에게는 허용된 snapshot 시각과 요청 상태만 제공

## 테스트

- DST/timezone, scheduler 중복, missed run
- backup 충돌과 결과 불명확
- 조직 재할당 전후 snapshot 접근
- retention 경계와 보호 snapshot
- restore drill 실패
- PBS/PVE 인증, 권한, TLS, timeout

## 완료 조건

- 모든 보호 대상의 최근 성공과 다음 예정 시각을 확인할 수 있다.
- 누락·실패가 경보로 연결된다.
- 복구 가능성을 정기 검증하고 결과를 보존한다.


---

[전체 작업 인덱스로 돌아가기](README.md)
