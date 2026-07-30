# 단계 10 설계 결정

- 상태: 구현 완료
- 기준일: 2026-07-26

## 승인 기반 변경 경계

제품 정책에 따라 고객 변경은 PVE 직접 실행 권한이 아니라 `service_request`로만
제공한다. SSH 공개키, hostname/설명, rDNS, 승인 security group, backup/restore,
증설과 재설치를 유형별 schema로 제한한다. preview와 생성 시 현재 QEMU 소유권,
조직 활성 상태, assignment와 조직 quota를 검사한다.

승인 전 고객 취소가 가능하고, 승인 후에는 관리자 실행 단계로만 전이한다. 승인
시점과 실행 시작 시점에도 소유권을 다시 검사한다. 승인에는 수정된 allowlist
입력을 선택적으로 전달할 수 있지만 동일 유형 검증을 다시 수행한다.

## 데이터 손실·격리 통제

- CPU/RAM/disk는 현재 값 이상만 허용하고 disk 축소를 금지한다.
- restore 요청에는 기존 VM 덮어쓰기 대상을 표현하는 필드가 없다.
- 재설치와 restore는 VM 이름 typed confirmation, MFA 등록과 step-up이 필요하다.
- SSH private material, 임의 Cloud-Init과 firewall 명령 문자열은 schema에 없다.
- security group은 정규화 CIDR, protocol과 port allowlist만 사용한다.
- key와 security group은 현재 workload 조직 또는 global scope만 참조할 수 있다.

## 실행과 복구

관리자 승인 시 `SERVICE_REQUEST` operation을 1:1 생성한다. 실행 시작은 다른 활성
VM operation과 충돌하면 거부한다. 성공/실패 요약은 service request, operation과
감사에 함께 남는다. 실패는 삭제하거나 성공으로 위장하지 않고
`NEEDS_ATTENTION`으로 보존해 관리자 후속 처리를 요구한다.
