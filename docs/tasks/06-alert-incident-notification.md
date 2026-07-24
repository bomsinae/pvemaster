# 단계 6 — 경보, Incident와 Notification (P1)

## 목표

현재 상태 계산을 지속되는 경보 사건과 실제 전달·대응 흐름으로 확장한다.

## 데이터 모델

- `alerts`: type, severity, fingerprint, status, first/last_seen_at, resolved_at
- `alert_events`: open, repeat, acknowledge, assign, silence, resolve
- `notification_channels`: organization/system scope, type, encrypted configuration
- `notification_rules`: event, severity, recipient, quiet hours, escalation
- `notification_deliveries`: 상태, attempt, 안전한 오류 코드
- `maintenance_windows`: 대상, 시작/종료, suppress 정책

## 경보 대상

- cluster 연결 실패와 inventory stale
- worker down, queue backlog, scheduler failure
- stuck/failed operation과 repeated manual review
- IP pool 부족
- backup 누락·실패·복구 검증 실패
- credential 만료·권한 부족
- 고객 VM 상태 변화와 예정된 maintenance

## API와 UI

- 경보 목록·상세·확인·담당자·해결·silence
- maintenance window CRUD
- notification channel test와 rule CRUD
- 대시보드에서 열린 경보와 최근 변화 표시
- 고객은 자기 조직/VM에 허용된 알림만 조회
- 고객별 이메일/Webhook 수신 설정

## 보안

- Webhook URL은 SSRF allowlist/denylist와 DNS 재검증을 적용한다.
- channel secret은 암호화하고 응답·로그에 반환하지 않는다.
- 고객 알림 payload에 내부 cluster/node/UPID를 넣지 않는다.
- notification 재시도는 멱등 delivery ID를 사용한다.

## 테스트

- 같은 원인의 경보 중복 억제와 재발
- acknowledge 후 재발
- maintenance window suppress
- 이메일/Webhook timeout과 재시도
- 고객 간 알림 데이터 격리
- secret redaction

## 완료 조건

- P0 운영 장애가 대시보드 조회 없이도 지정 채널로 전달된다.
- 경보 담당자와 처리 이력이 감사 가능하다.
- 고객 maintenance와 작업 완료 알림이 조직 범위로 제한된다.


---

[전체 작업 인덱스로 돌아가기](README.md)
