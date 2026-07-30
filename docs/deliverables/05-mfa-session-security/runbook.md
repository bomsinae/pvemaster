# 단계 5 운영 runbook — MFA와 Session

- 상태: 운영 인수 가능
- 기준일: 2026-07-26

## 배포 전 설정

1. `APP_SECRET_KEY`를 32자 이상의 secret manager 값으로 설정한다. 변경하면 기존
   MFA secret을 복호화할 수 있으므로 계획 없는 회전을 금지한다.
2. 운영 portal 기준으로 `WEBAUTHN_RP_ID`와 HTTPS `WEBAUTHN_ORIGIN`을 설정한다.
3. `ADMIN_MFA_REQUIRED=true`를 명시한다. `ENVIRONMENT=production`도 정책을 강제한다.
4. migration `20260726_0018`을 적용하고 `alembic current`가 head인지 확인한다.
5. break-glass SUPER_ADMIN 두 명이 각각 MFA와 복구 코드를 등록했는지 확인한 뒤
   정책을 활성화한다.

## 정책 활성화

- 정책 변경 전에 현재 SUPER_ADMIN이 MFA를 등록하고 step-up을 완료해야 한다.
- 복구 코드는 암호화된 운영 금고에 저장하며 ticket, chat, 로그에 붙여 넣지 않는다.
- WebAuthn만 등록할 경우 분실에 대비해 두 개 이상의 물리 키 또는 복구 코드를
  준비한다.

## 사고 대응

- 의심 session: 보안 설정에서 해당 session을 즉시 종료한다.
- 계정 탈취: 사용자를 비활성화하거나 비밀번호를 변경해 `session_epoch`를 올리고,
  모든 refresh family가 무효화됐는지 감사 로그에서 확인한다.
- MFA 장치 분실: 미사용 복구 코드로 로그인해 새 method를 등록한 뒤 복구 코드를
  재발급한다. 기존 원문은 다시 조회할 수 없다.
- WebAuthn origin/RP 오류: 정책을 끄지 말고 reverse proxy host/origin과 환경 값을
  대조한다.
- 암호화 키 노출: 로그인과 고위험 작업을 제한하고 키 교체·MFA 재등록 계획을
  수립한다. 기존 암호문의 무계획 폐기는 금지한다.

## 관측할 감사 action

`AUTH_LOGIN`, `AUTH_LOGIN_MFA_CHALLENGE`, `MFA_ENROLLED`, `MFA_DISABLED`,
`MFA_CHALLENGE_FAILED`, `MFA_STEP_UP`, `MFA_RECOVERY_CODES_REGENERATED`,
`AUTH_SESSION_REVOKED`, `AUTH_OTHER_SESSIONS_REVOKED`, `MFA_POLICY_UPDATED`.

