# 단계 7 운영 runbook

- 상태: 운영 인수 준비
- 기준일: 2026-07-26

## 정책 등록

1. PVE에서 PBS storage와 PBS prune job이 정상인지 먼저 확인한다.
2. PVE Master의 백업 대상이 해당 cluster에서 사용 가능인지 확인한다.
3. 정책 이름, cron, IANA timezone, 조직 또는 workload 범위와 PBS 보존 정책 참조를
   입력한다.
4. 대상 preview에서 cluster mismatch와 백업 제외 workload가 없는지 확인한다.
5. `다음 실행`, 최근 성공과 연속 실패를 확인한다.

## 누락·실패 대응

1. `BACKUP_SCHEDULE_MISSED` 경보에서 scheduler와 maintenance queue 상태를 확인한다.
2. 같은 workload의 활성 operation이 있으면 완료 또는 복구한 뒤 `run-now`를 사용한다.
3. 제출 timeout은 자동 재제출하지 않고 PVE task와 PBS snapshot을 먼저 확인한다.
4. 성공 run에 snapshot ID가 없으면 metadata 재조정을 실행한다.
5. PBS에서 prune된 snapshot은 실행 이력을 삭제하지 않고 관측 가용성만 갱신한다.

## 복구 훈련

1. 운영 VMID와 겹치지 않는 격리 node·VMID를 준비하고 네트워크 영향을 검토한다.
2. 성공 snapshot 상세에서 metadata 검증을 먼저 실행한다.
3. step-up MFA 후 restore drill을 요청한다.
4. 복구 완료 뒤 inventory, 부팅 가능성, 예상 데이터와 RTO를 확인한다.
5. 훈련 VM 정리는 PVE 변경 절차에 따라 별도로 승인·수행한다. PVE Master는 자동
   삭제하지 않는다.
