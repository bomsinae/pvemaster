# 단계 5 설계 결정 — MFA, Session과 고위험 작업 보호

- 상태: 구현 완료
- 기준일: 2026-07-26

## 인증 흐름

비밀번호 검증 뒤 활성 MFA가 있으면 access/refresh token을 발급하지 않고 만료 시간과
시도 제한이 있는 `LOGIN` challenge를 반환한다. TOTP, WebAuthn 또는 아직 사용하지
않은 복구 코드가 challenge를 통과한 뒤에만 MFA assurance session을 만든다.

TOTP secret은 애플리케이션 주 키에서 MFA 전용 HKDF context로 파생한 AES-GCM 키로
암호화한다. 사용자와 MFA method ID를 AAD에 포함해 다른 계정이나 method로 암호문을
옮길 수 없게 한다. 복구 코드는 keyed hash만 저장하고 등록·재발급 응답에서 한 번만
원문을 표시한다.

WebAuthn은 배포 환경의 고정 RP ID와 origin을 검증하고 user verification을 요구한다.
운영 배포에서는 `WEBAUTHN_RP_ID`, `WEBAUTHN_ORIGIN`을 실제 HTTPS portal과 일치시켜야
한다.

## Session과 폐기

access token의 `sid`는 refresh token family ID다. 모든 인증 요청에서 활성 refresh
family와 사용자 `session_epoch`를 함께 검사하므로 개별 session 종료와 전체 epoch
증가가 기존 access token에 즉시 반영된다. session에는 기기 이름, 생성 IP, user
agent, 최근 사용 시각, 만료와 assurance level을 보관한다.

## 관리자 정책과 step-up

관리자 MFA 정책은 `security_policies` singleton row에 영속화하며 환경 설정으로
강제할 수 있다. `production` 환경은 설정과 무관하게 SUPER_ADMIN/OPERATOR MFA가
필수이며 UI에는 미준수 상태가 표시된다. 미등록 관리자는 등록 API 외 보호 작업을
수행할 수 없다.

다음 작업은 정책 활성 시 `X-Step-Up-Token`을 요구한다.

| action | 보호 범위 |
|---|---|
| `CLUSTER_CREDENTIAL_WRITE` | cluster 등록·수정·삭제 |
| `USER_SECURITY_WRITE` | 사용자 생성·역할/상태 변경·삭제·비밀번호 reset |
| `BACKUP_RESTORE` | backup restore 접수 |
| `FORCED_STOP` | 관리자 강제 stop |
| `MFA_POLICY_WRITE` | 관리자 MFA 정책 변경 |

step-up token은 사용자, `session_epoch`, action, 짧은 만료에 서명되어 다른 action에
재사용할 수 없다. challenge는 성공 후 소비되고 실패 횟수와 만료를 검사한다.

## UI

로그인은 TOTP/복구 코드 challenge를 처리한다. 관리자와 고객 모두 보안 설정에서
TOTP 또는 WebAuthn을 등록하고, 복구 코드의 1회 표시, 활성 session 조회·종료, 최근
로그인 이력을 사용할 수 있다. 보호 API가 `STEP_UP_REQUIRED`를 반환하면 공통
step-up dialog가 인증을 마친 뒤 같은 요청을 action-bound token으로 한 번 재시도한다.

