# 단계 5 — MFA, Session과 고위험 작업 보호 (P0)

## 목표

관리자 MFA를 필수화하고 고객 MFA, session 관리, step-up 인증을 제공한다.

## 데이터 모델

- `mfa_methods`: type, credential/public metadata, enrolled_at, last_used_at, disabled_at
- `mfa_challenges`: purpose, expires_at, attempts, consumed_at
- `recovery_codes`: 일방향 hash, used_at
- refresh session에 device label, created IP 요약, last_seen_at, expires_at
- 최근 MFA 인증 시각과 assurance level

## API

- MFA 등록 시작·검증·해제
- TOTP와 WebAuthn 지원
- 복구 코드 생성·재발급·사용
- MFA challenge와 step-up token
- 활성 session 목록과 개별/전체 session revoke
- 본인 로그인 이력 조회
- 관리자 MFA 정책 조회·변경

## 정책

- 운영 환경의 SUPER_ADMIN/OPERATOR는 MFA 등록을 필수로 한다.
- cluster credential, 사용자 역할, backup restore, 강제 stop, 감사 export에 step-up을 요구한다.
- MFA 해제와 복구 코드 재발급은 재인증·감사 대상이다.
- 비활성화·비밀번호 변경·위험 session revoke 시 `session_epoch`를 갱신한다.
- 복구 코드는 원문을 다시 표시하지 않는다.

## UI

- 로그인 MFA challenge
- 보안 설정과 MFA 등록 wizard
- 복구 코드 1회 표시
- 현재/다른 session 목록과 종료
- 관리자 정책 준수 상태
- 고위험 action의 step-up dialog

## 테스트

- MFA 우회, challenge replay, 만료, 시도 제한
- WebAuthn origin/RP ID 검증
- 복구 코드 재사용
- session revoke 즉시성
- 역할 변경 중 기존 session
- step-up 만료와 다른 action 재사용

## 완료 조건

- MFA 미등록 관리자는 보호된 관리자 기능을 사용할 수 없다.
- session과 MFA 변경이 모두 감사 로그에 남는다.
- 고객은 정책에 따라 MFA를 자율 또는 필수로 사용할 수 있다.


---

[전체 작업 인덱스로 돌아가기](README.md)
