# 단계 7 설계 결정

- 상태: 구현 완료
- 기준일: 2026-07-26

## 책임 경계

PVE Master는 PVE API를 통해 PVE에 등록된 PBS storage로 백업과 새 VMID 복구를
요청한다. PBS credential, encryption key와 snapshot 삭제 권한은 저장하지 않는다.
보존과 prune의 기준 시스템은 PBS이며 `retention_reference`는 운영자가 PBS 정책을
식별하기 위한 참조값이다.

고객 backup/snapshot API는 제품 정책에 따라 제공하지 않는다. 과거 소유 기간의
snapshot 경계가 검증되고 별도 승인되기 전까지 관리자 기능으로만 유지한다.

## 정책과 실행

- 5-field cron과 IANA timezone을 저장하고 UTC 분 단위로 다음 실행을 계산한다.
- DST gap은 존재하는 다음 local 시각으로 넘어가며 반복 시각은 각각의 UTC occurrence로
  구분한다.
- 정책은 조직 또는 개별 workload에 할당한다. 조직 범위는 실행 직전 현재 인벤토리로
  확장한다.
- schedule occurrence, assignment, workload를 결합한 idempotency key와 기존
  workload active operation 제약으로 중복 백업을 막는다.
- 다른 cluster의 PBS target은 preview에 제외 사유로 표시하고 dispatch하지 않는다.
- 다음 1회 skip은 예정 시각과 version을 함께 저장한다.

## 검증

성공 snapshot은 PVE storage content를 다시 조회해 volume ID와 VMID를 확인한다.
분기 기본 주기의 restore drill이 도래하면 `DUE` 기록을 만들며 SUPER_ADMIN이 격리
node와 비어 있는 새 VMID를 명시해 실행한다. 기존 restore runner의 충돌, TLS,
timeout, 권한 검사를 그대로 재사용하고 결과를 `backup_verifications`에 보존한다.
