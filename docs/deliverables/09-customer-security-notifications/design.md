# 단계 9 설계 결정

- 상태: 구현 완료
- 기준일: 2026-07-26

## 고객 보안 Self-service

단계 5에서 제공한 TOTP/WebAuthn, 복구 코드, 활성 session과 최근 로그인 기능을
고객 포털의 보안 설정에서 함께 제공한다. session 조회·폐기는 현재 사용자 소유
family만 허용한다. 복구 코드 재발급과 MFA 해제는 MFA 재인증을 요구하고 모든
변경을 감사한다.

비밀번호 변경은 현재 비밀번호를 다시 확인한다. 기본 동작은 현재 session을 포함한
전체 session 종료다. 고객이 현재 session 유지를 명시하면 현재 family만 유지하고
다른 모든 family를 폐기한다. 이 선택은 요청마다 서버에 전달되며 감사 상세에는
비밀값 없이 전체 session 종료 여부만 기록한다.

## 고객 이메일 알림

알림은 활성 조직별 `VM_DOWN`, `OPERATION_COMPLETED`, `BACKUP_FAILED`,
`MAINTENANCE` 네 event를 지원한다. 고객 설정이 없으면 이메일을 활성화한다.
조직의 `email_required` 정책은 고객 설정보다 우선하며 UI와 API 모두에서 해제를
거부한다. 고객 화면에는 수신 이메일을 마스킹해서 표시한다.

설정 변경은 version을 사용해 탭 간 갱신 경쟁을 감지한다. 알림 row는 사용자와
event key로 멱등 enqueue하고 dispatcher가 발송 직전에 활성 계정, 현재 조직
멤버십과 유효 opt-in을 다시 확인한다. enqueue 뒤 opt-out 또는 멤버십 회수는
전송하지 않고 `CANCELLED`로 보존한다.

SMTP 연결은 timeout과 기본 TLS 인증서 검증을 사용한다. 오류는 일반화된 코드만
저장하며 이메일 주소, 본문, 인증 정보와 SMTP 원문을 로그나 감사에 남기지 않는다.
