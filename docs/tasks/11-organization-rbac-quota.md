# 단계 11 — 조직 관리자, 팀 권한, Quota와 승인 (P2)

## 목표

단일 CUSTOMER 역할을 조직 단위 협업과 제한된 관리 권한으로 확장한다.

## 역할 설계

- 플랫폼 역할과 조직 역할을 분리한다.
- 예: `ORG_OWNER`, `ORG_ADMIN`, `ORG_OPERATOR`, `ORG_VIEWER`, `BILLING_VIEWER`
- 한 사용자가 조직마다 다른 역할을 가질 수 있게 한다.
- 역할별 permission을 명시적 allowlist로 정의한다.

## 데이터 모델

- `organization_memberships`에 조직 역할·초대 상태·만료
- `organization_invitations`: token hash, role, expires_at, accepted_at
- `organization_quotas`: vCPU, RAM, disk, VM 수, IP, backup
- `quota_usage_snapshots`
- `approval_policies`

## API와 UI

- 조직 구성원 초대·역할 변경·제거
- quota와 현재 사용량 조회
- 관리자 quota 정책 설정
- quota 초과 요청과 승인
- 조직 감사/활동 보기

## 보안

- 조직 관리자는 플랫폼 관리자 API에 접근할 수 없다.
- 마지막 ORG_OWNER 제거를 금지한다.
- 초대 token은 hash만 저장하고 1회·만료를 강제한다.
- quota 확인과 자원 예약을 같은 DB transaction 경계에서 처리한다.

## 테스트

- 조직 간 수평 권한 상승
- 마지막 owner 제거
- 초대 replay·만료·이메일 불일치
- 동시 provisioning quota 경쟁
- 역할 변경 중 진행 작업

## 완료 조건

- 조직이 자체적으로 팀을 운영하되 플랫폼 경계를 넘을 수 없다.
- 동시 요청에서도 quota가 초과 예약되지 않는다.


---

[전체 작업 인덱스로 돌아가기](README.md)
