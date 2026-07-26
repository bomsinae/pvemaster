# 단계 11 설계 결정

- 상태: 구현 완료
- 기준일: 2026-07-26

## 권한 경계

플랫폼 역할과 조직 역할을 분리했다. 고객 조직 역할은 `ROLE_PERMISSIONS`의 명시적
allowlist로만 구성하고, 모든 조직 API는 활성·미만료 멤버십과 요청 조직을 함께
검사한다. 읽기 역할과 workload 변경 역할도 분리해 `ORG_VIEWER`와
`BILLING_VIEWER`가 변경 작업을 실행하지 못한다.

`ORG_OWNER`와 `ORG_ADMIN`은 팀 운영을 위임받지만 플랫폼 관리자 API 권한은 얻지
않는다. 마지막 owner 강등·제거는 금지하고, role 변경은 version compare-and-set으로
동시 수정을 방어한다.

## 초대와 정책

초대 원문 token은 생성 응답에 한 번만 반환하며 서버에는 SHA-256 hash만 저장한다.
수락 시 로그인 이메일, 만료, revoke와 기존 사용 여부를 검사한다. 승인 정책은
조직·요청 유형별로 승인 필요 여부와 최소 승인 역할을 저장한다.

## Quota 예약

조직 quota는 vCPU, RAM, disk, VM, IP와 backup 상한을 다룬다. 사용량과 활성 예약을
분리해 UI에 표시한다. Provisioning과 self-service 증설은 organization row를 잠근
같은 transaction에서 quota 확인과 예약을 수행하므로 병렬 요청도 상한을 넘지 않는다.
성공은 예약을 소비하고 확정 실패는 해제한다. 부분 적용 가능성이 있는 수동 검토
상태는 이중 할당을 피하기 위해 예약을 유지한다.
