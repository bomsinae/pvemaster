# 단계 11 운영 runbook

- 상태: 운영 인수 준비
- 기준일: 2026-07-26

## 배포

1. migration `20260726_0024`를 적용하고 Alembic head와 schema drift를 확인한다.
2. 기존 조직의 최초 멤버가 `ORG_OWNER`, 나머지가 `ORG_OPERATOR`로 변환됐는지
   검토한다.
3. 조직별 현재 workload, IP와 backup 사용량을 확인한 뒤 quota를 설정한다.
4. 고객 owner로 구성원 초대·수락·역할 변경·제거와 활동 기록을 확인한다.
5. 병렬 provisioning과 증설 요청에서 활성 reservation 합계가 상한 이하인지
   확인한다.

## 운영 대응

- `ORGANIZATION_QUOTA_EXCEEDED`: 사용량과 활성 reservation을 확인하고 중복 요청을
  정리한다. 상한 증가는 실제 용량과 승인 절차를 확인한 뒤 version으로 적용한다.
- 만료·정지 멤버십: 실행 중 작업의 고객 권한을 재검토하고 새 변경 실행을 차단한다.
- 초대 분실: 기존 초대를 폐기하고 새 초대를 발행한다. 원문 token은 복구하지 않는다.
- owner 교체: 새 owner를 먼저 지정한 뒤 기존 owner를 강등하거나 제거한다.
- `MANUAL_REVIEW` 예약: 실제 PVE/IP 상태를 대조해 소비 또는 해제 여부를 결정한다.
