# 단계 14 — Staging 검증 계획

## 문서 상태

- 상태: 실행 대기
- 기준일: 2026-07-26
- 대상: 운영과 격리된 staging PVE/PBS, PostgreSQL, Redis와 전용 테스트 자원

## 사전 조건

- 운영 데이터가 없는 전용 조직, VMID 범위, IP pool과 PBS namespace를 사용한다.
- PVE/PBS token은 기능별 최소 권한이고 등록된 CA로 TLS를 검증한다.
- DB snapshot과 복구 지점, 시험 담당자, 중단 조건과 rollback 담당자를 기록한다.
- 실제 credential, endpoint, UPID와 고객 데이터는 결과 문서에 복사하지 않는다.
- 각 시나리오는 시작 시각, 담당자, build SHA, request/operation 식별자, 관측 결과와
  정리 결과를 증거 저장소에 남긴다.

## E2E 시나리오

| ID | 흐름 | 통과 기준 |
|---|---|---|
| STG-01 | 클러스터 등록과 최소 권한 probe | TLS 우회 없이 연결되고 초과 권한이 거부된다 |
| STG-02 | 자동 sync, 삭제 관측과 tombstone | 고객 소유권을 잃지 않고 stale/removed 상태가 일관된다 |
| STG-03 | 상품·템플릿·IP·node 정책 설정 | 잘못된 조합이 preview/validation에서 차단된다 |
| STG-04 | VM 생성과 IP 할당 | 멱등 요청, quota, IP·VMID 예약과 보상이 일치한다 |
| STG-05 | 조직 할당·회수 | 감사 이력과 고객 가시성이 즉시 일치한다 |
| STG-06 | 고객 조회·전원·콘솔 | IDOR가 차단되고 token/UPID/node가 노출되지 않는다 |
| STG-07 | PBS backup과 새 VMID 복구 | 원본을 덮어쓰지 않고 복구 후 수동 검토 상태가 된다 |
| STG-08 | 실패·취소·worker 재시작 | 중복 제출 없이 recovery 또는 `NEEDS_ATTENTION`으로 수렴한다 |
| STG-09 | 경보 전달과 incident 처리 | dedupe, retry, acknowledge/resolve와 전달 이력이 일치한다 |
| STG-10 | 사용자 비활성화·회수·session revoke | 기존 access/refresh/console이 모두 재검사되어 거부된다 |

## 장애 주입과 비기능 시나리오

| ID | 시험 | 필수 관측 |
|---|---|---|
| NF-01 | PostgreSQL 연결 차단·복구 | readiness 503, 데이터 손상 없음, 재연결 후 정상 |
| NF-02 | Redis 중단·복구 | readiness/worker 경보, outbox 보존, 복구 후 중복 없음 |
| NF-03 | worker 강제 재시작 | lease/watchdog 회수와 UPID 기반 재개 |
| NF-04 | PVE timeout·TLS·401/403 | 분류된 오류, TLS 우회 없음, 비밀정보 redaction |
| NF-05 | 장기 UPID와 queue backlog | SLO 내 관측, 경보와 처리량·지연 기록 |
| NF-06 | PostgreSQL point-in-time 복구 | RPO/RTO 측정, migration revision과 감사 연속성 확인 |
| NF-07 | PBS 표본 복구 | 새 VMID 복구, 네트워크 격리, 데이터 검증과 정리 |
| NF-08 | 권한·IDOR·SSRF·CSRF·WebSocket | deny-by-default와 객체 소유권 재검사 |
| NF-09 | 브라우저·접근성 | Chromium/Firefox/WebKit, keyboard, focus, 320px reflow |
| NF-10 | 부하 | API p95, queue 대기, DB connection과 error rate 기록 |

## 중단과 정리

고객 격리 위반, 원본 VM 변경, credential 노출, 감사 누락 또는 복구 불가능한 상태가
관측되면 즉시 시험을 중단하고 출시 blocker로 등록한다. 시험 종료 후 VM, IP 예약,
backup, token과 임시 계정을 명시적 목록으로 정리하고 inventory reconciliation이
정상임을 확인한다.
