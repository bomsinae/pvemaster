# 단계 9 운영 runbook

- 상태: 운영 인수 준비
- 기준일: 2026-07-26

## 배포

1. migration `20260726_0022`를 적용하고 Alembic head와 schema drift를 확인한다.
2. SMTP host/from 주소와 timeout을 secret 관리 경로로 설정한다. 인증서를 우회하지
   않는다.
3. scheduler에 고객 알림 전달 작업이 30초 주기로 등록되고 worker heartbeat가
   정상인지 확인한다.
4. 고객 계정으로 MFA, 복구 코드, session 종료, 비밀번호 변경과 조직별 알림 설정을
   확인한다.
5. 필수 조직 알림이 UI에서 잠겨 있고 API opt-out도 409인지 확인한다.

## 전달 장애 대응

1. `PENDING`/`RETRY` backlog, 최근 실행 시각과 worker heartbeat를 확인한다.
2. SMTP DNS/TLS/timeout과 발신자 설정을 점검하되 주소, 본문, 비밀번호와 SMTP
   인증 정보를 로그에 출력하지 않는다.
3. 최대 재시도 후 `FAILED`는 원인 코드를 해결한 뒤 별도 운영 절차로 재queue한다.
   같은 event key를 임의 변경해 중복 발송하지 않는다.
4. 고객이 opt-out했거나 멤버십이 회수된 `CANCELLED` row는 재전송하지 않는다.

## 보안 사고 확인

1. 다른 사용자 session UUID 접근이 404인지 확인한다.
2. 비밀번호 변경 선택에 따라 현재 session 유지/종료가 정확한지 확인한다.
3. 로그인·감사·전달 오류에 token, 비밀번호, 전체 이메일과 내부 SMTP 원문이 없는지
   확인한다.
4. 계정 탈취가 의심되면 해당 사용자의 전체 session을 폐기하고 MFA 복구 코드를
   재발급한 뒤 감사 request ID로 변경 이력을 조사한다.
