# 단계 10 운영 runbook

- 상태: 운영 인수 준비
- 기준일: 2026-07-26

## 배포

1. migration `20260726_0023`을 적용하고 Alembic head와 schema drift를 확인한다.
2. 조직별 VM 상한과 활성 요청 quota를 설정한다. row가 없으면 보수적 기본값이
   적용되는지 확인한다.
3. global/조직 security group의 CIDR, protocol, port와 기본 차단 정책을 검토한다.
4. 고객 계정으로 preview → 제출 → 승인 전 취소를 확인한다.
5. 관리자 계정으로 승인 → 실행 시작 → 성공/실패 결과와 operation/audit 연결을
   확인한다.

## 승인 절차

1. 요청자가 현재 조직 멤버이고 workload assignment가 유지되는지 확인한다.
2. preview의 현재/요청 사양, quota와 데이터·네트워크 영향을 검토한다.
3. restore는 기존 VM을 덮어쓰지 않는 별도 격리 대상인지 확인한다.
4. 재설치/restore는 고객 MFA step-up과 typed confirmation 기록을 확인한다.
5. 승인 사유를 남기고 변경 창에서 실행을 시작한다. 외부 PVE/DNS 작업이 완료되면
   inventory 또는 외부 조회로 검증한 뒤에만 성공 완료한다.

## 실패 대응

1. `NEEDS_ATTENTION` request와 연결 operation의 일반화된 error code를 확인한다.
2. 소유권 변경이면 실행하지 않고 새 소유자에게 과거 입력을 노출하지 않는다.
3. 부분 적용 가능성이 있으면 재실행 전에 실제 PVE/DNS/firewall 상태를 조회한다.
4. private key, token, 전체 PVE 응답과 내부 endpoint를 지원 ticket이나 감사에
   복사하지 않는다.
