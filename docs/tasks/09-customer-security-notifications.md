# 단계 9 — 고객 계정 보안과 알림 설정 (P1)

## 목표

고객이 자기 계정과 알림을 직접 안전하게 관리한다.

## 기능

- 고객 MFA와 recovery code
- 활성 session·기기 조회와 revoke
- 최근 로그인과 보안 활동
- 비밀번호 변경과 모든 session 종료 선택
- VM down, 작업 완료, backup 실패, maintenance 알림 설정
- 조직 정책이 강제한 채널/이벤트는 고객이 해제할 수 없게 한다.

## 테스트

- 다른 고객 session 접근
- 이메일 변경이 추가될 경우 재검증과 기존 주소 알림
- 알림 opt-in/out 경쟁
- security event redaction
- recovery code와 session revoke

## 완료 조건

- 고객이 지원 요청 없이 MFA, session, 알림을 관리할 수 있다.
- 보안 변경은 모두 감사되고 고위험 변경은 재인증된다.


---

[전체 작업 인덱스로 돌아가기](README.md)
