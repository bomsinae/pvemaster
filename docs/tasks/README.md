# 관리자·고객 기능 확장 작업 계획

## 1. 목적

이 문서는 현재 PVE Master의 통합 인벤토리, 조직별 소유권, 전원 제어, 콘솔,
프로비저닝, IPAM, PBS 수동 백업 기능을 기반으로 운영 출시와 고객 셀프서비스에
필요한 후속 작업을 단계별로 정의한다.

`docs/tasks/`에는 작업 계획만 저장한다. 각 단계를 진행하면서 작성하는 정책,
설계 결정, 검증 보고서와 운영 문서는
[산출물 인덱스](../deliverables/README.md)의 규칙에 따라
`docs/deliverables/<단계 번호>-<단계 이름>/`에 저장한다.

실제 구현 순서는 이 문서보다 `PLAN.md`, `docs/architecture.md`,
`docs/security-model.md`, `docs/api-design.md`의 기존 계약을 우선한다. 아래 단계에서
새 API나 모델이 필요하더라도 기존 `/api/v1` 계약과 권한 정책을 임의로 변경하지
않으며, DB 변경에는 Alembic migration을 작성한다.

## 2. 범위와 우선순위

| 우선순위 | 의미 | 출시 판단 |
|---|---|---|
| P0 | 운영 안정성, 보안, 데이터 일관성 | 운영 출시 전에 완료 |
| P1 | 관리자 운영 효율과 고객 핵심 경험 | 정식 서비스 초기에 완료 |
| P2 | 고급 PVE 기능과 확장된 셀프서비스 | 핵심 운영 안정화 후 진행 |
| P3 | 상용 호스팅/외부 시스템 확장 | 사업 모델 확정 후 선택 |

권장 실행 순서는 다음과 같다.

```text
제품 정책 확정
  → 품질 게이트
  → scheduler/outbox 기반
  → inventory sync/reconciliation
  → operation 센터
  → MFA/session 보안
  → 경보·알림
  → 백업 자동화
  → 고객 이력·성능·알림
  → 고객 셀프서비스·조직 기능
  → 고급 PVE 운영
  → 상용 기능
  → staging 출시 검증
```

각 단계는 이전 단계의 완료 조건을 충족한 뒤 시작하는 것을 기본으로 한다. UI만 먼저
노출하거나 frontend 상태만으로 권한과 정책을 강제하지 않는다.


## 단계별 작업 문서

1. [단계 0 — 제품 정책과 운영 기준 확정](00-product-policy.md) — 완료
2. [단계 1 — CI와 실제 사용자 여정 품질 게이트](01-ci-browser-quality-gates.md)
3. [단계 2 — Scheduler, Outbox와 정기 유지보수 기반](02-scheduler-outbox-maintenance.md)
4. [단계 3 — 자동 Inventory Sync와 Reconciliation](03-inventory-sync-reconciliation.md)
5. [단계 4 — 관리자 Operation 센터와 복구 흐름](04-operation-center-recovery.md)
6. [단계 5 — MFA, Session과 고위험 작업 보호](05-mfa-session-security.md)
7. [단계 6 — 경보, Incident와 Notification](06-alert-incident-notification.md)
8. [단계 7 — PBS 백업 자동화와 복구 검증](07-pbs-backup-automation.md)
9. [단계 8 — 고객 작업 이력, VM 상세와 성능 지표](08-customer-history-metrics.md)
10. [단계 9 — 고객 계정 보안과 알림 설정](09-customer-security-notifications.md)
11. [단계 10 — 제한된 고객 Self-service](10-customer-self-service.md)
12. [단계 11 — 조직 관리자, 팀 권한, Quota와 승인](11-organization-rbac-quota.md)
13. [단계 12 — 관리자 고급 PVE 운영](12-advanced-pve-operations.md)
14. [단계 13 — 상용 호스팅 기능](13-commercial-hosting.md)
15. [단계 14 — 출시 Hardening과 운영 인수](14-release-hardening.md)

## 3. 권장 Release 묶음

### Release A — 운영 출시 기반

- 단계 0 정책 확정
- 단계 1 CI/실제 E2E
- 단계 2 scheduler/outbox
- 단계 3 inventory/reconciliation
- 단계 4 operation 센터
- 단계 5 관리자 MFA/session
- 단계 14 중 P0 staging 검증

### Release B — 운영 자동화와 고객 가시성

- 단계 6 경보/알림
- 단계 7 백업 자동화
- 단계 8 고객 이력/성능
- 단계 9 고객 보안/알림

### Release C — 고객 셀프서비스

- 단계 10 제한된 self-service
- 단계 11 조직 역할/quota/승인

### Release D — 고급 인프라 운영

- 단계 12 snapshot, migration, HA, maintenance, bulk, 상세 구성, firewall/SDN

### Release E — 상용화

- 단계 13 주문·과금·지원·고객 API

## 4. 공통 완료 정의

각 기능은 다음 조건을 모두 충족해야 완료로 본다.

1. 모델 변경에 Alembic upgrade와 downgrade 정책이 있다.
2. API schema와 오류 계약이 `docs/api-design.md`에 반영되어 있다.
3. 서비스 계층에서 역할과 객체 소유권을 강제한다.
4. 외부 HTTP와 PVE 호출에 timeout, TLS 검증, 오류 분류가 있다.
5. 비동기 변경은 operation, PVE task, 감사 로그에 연결된다.
6. 성공, 실패, timeout, 권한, 중복, 재시작 경로를 테스트한다.
7. 실제 PVE는 단위 테스트에서 호출하지 않고 fake/mock client를 사용한다.
8. 고객 응답과 로그에 token, UPID, cluster endpoint 등 내부 정보가 노출되지 않는다.
9. 빈 상태, loading, stale, 부분 실패, retry UI가 정의되어 있다.
10. keyboard, focus, reflow, 상태 전달 접근성을 검증한다.
11. 운영 지표, 경보, runbook과 rollback 절차가 있다.
12. 전체 pytest, Ruff format/check, mypy, frontend lint/typecheck, Playwright, build가 통과한다.

## 5. 계획 관리 규칙

- 단계별 착수 전에 API와 데이터 모델 설계를 먼저 리뷰한다.
- 단계별 문서 산출물은 `docs/deliverables/<단계 번호>-<단계 이름>/`에 저장한다.
- 하나의 PR에는 하나의 상태 머신 또는 하나의 사용자 여정만 포함한다.
- P0 기능은 feature flag로 숨긴 채 미완성 상태로 운영에 노출하지 않는다.
- 고위험 작업은 read-only 조회 → preview → 제한된 실행 순서로 확장한다.
- 각 Release 종료 시 위협 모델, 권한 매트릭스, 운영 문서와 테스트 매트릭스를 갱신한다.
- 일정 압박이 있어도 inventory 일관성, MFA, 고객 격리, 감사, 복구 검증을 후순위로 미루지 않는다.
