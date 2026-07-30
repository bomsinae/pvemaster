# 단계 12 운영 runbook

- 상태: 운영 인수 준비
- 기준일: 2026-07-26

## 단계적 활성화

1. migration `20260726_0025`와 schema drift를 확인한다.
2. 모든 고급 flag가 비활성인지 확인하고 PVE token ACL을 feature별로 검토한다.
3. Snapshot read/create부터 staging에서 활성화하고 storage의 memory snapshot 지원을
   확인한다.
4. Migration은 shared/local disk, network, passthrough, HA와 replication 조합별로
   preview 및 실제 결과를 확인한다.
5. Node drain은 backup 상태와 고객 downtime 알림을 확인한 유지보수 창에서만 실행한다.
6. HA는 fencing과 quorum runbook을 검토한 담당자만 변경한다.
7. Firewall/SDN은 read-only 결과와 scope가 맞는지만 확인하며 write ACL을 부여하지 않는다.

## 장애 대응

- `TARGET_STATE_CHANGED`: inventory를 동기화하고 새 preview를 만든다.
- `OPERATION_CONFLICT`: Operation 센터에서 기존 작업과 bulk target lock을 확인한다.
- `PVE_TASK_TIMEOUT`: 기존 UPID를 조회해 완료 여부를 재조정한다.
- `PVE_TIMEOUT` + `NEEDS_ATTENTION`: 제출 여부가 불명확하므로 자동 재실행하지 않는다.
- 부분 성공 bulk/drain: `observed_state`의 성공 대상을 제외하고 남은 대상만 새
  preview로 요청한다.
- maintenance 복귀 전 HA requested/observed state와 실제 node 배치를 확인한다.
