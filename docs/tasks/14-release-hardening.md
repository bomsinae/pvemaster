# 단계 14 — 출시 Hardening과 운영 인수 (P0 최종)

## Staging E2E

다음 전체 흐름을 실제 staging PVE와 전용 테스트 자원으로 검증한다.

1. 클러스터 등록과 최소 권한 probe
2. 자동 sync와 tombstone
3. 상품·템플릿·IP·node 정책 설정
4. VM 생성과 IP 할당
5. 조직 할당
6. 고객 조회·전원·콘솔
7. backup과 새 VMID 복구
8. 작업 실패·취소·worker 재시작 복구
9. 경보 전달과 incident 처리
10. 사용자 비활성화·할당 회수·session revoke

## 비기능 검증

- 부하와 queue backlog
- PostgreSQL, Redis, worker, PVE 장애 주입
- 장기 UPID와 worker 재시작
- DB/PBS 복구 훈련
- dependency/container/security scan
- 권한·IDOR·SSRF·CSRF·WebSocket 보안 검토
- 감사 로그 완전성·redaction·외부 보관
- 브라우저 호환성과 접근성

## 운영 인수

- dashboard와 alert rule 배포
- on-call runbook과 escalation
- credential/암호화 키 회전 훈련
- backup restore 훈련 결과 기록
- 데이터 보존 job 확인
- 기능별 kill switch와 rollback 절차
- 관리자·고객 공지 template

## 완료 조건

- 정의된 SLO와 RPO/RTO를 staging 훈련으로 검증했다.
- P0 보안·일관성·복구 문제에 미해결 고위험 항목이 없다.
- 장애, credential 침해, PVE 단절, DB 복구 runbook이 승인되었다.
- 운영 담당자가 관리자 operation·alert·reconciliation 화면으로 장애를 처리할 수 있다.


---

[전체 작업 인덱스로 돌아가기](README.md)
