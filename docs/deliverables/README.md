# 작업 산출물 인덱스

## 목적

`docs/tasks/`의 단계별 작업을 수행하면서 작성되는 정책, 상세 설계, 결정 기록,
검증 결과와 운영 인수 문서를 이 디렉토리에 보관한다.

작업 계획과 산출물을 다음처럼 분리한다.

```text
docs/
├── tasks/                         # 단계별 작업 계획
└── deliverables/                  # 작업 수행 결과
    ├── README.md                  # 산출물 인덱스와 저장 규칙
    └── 00-product-policy/         # 단계 0 산출물
        └── product-policy.md
```

애플리케이션 소스, migration과 테스트 코드는 기존 `backend/`, `frontend/` 경로에
둔다. `docs/deliverables/`에는 문서 형태의 산출물만 저장한다.

## 저장 규칙

1. 단계별 디렉토리 이름은 `NN-영문-슬러그` 형식을 사용한다.
2. 하나의 산출물이 여러 단계에서 사용되더라도 최초 소유 단계 디렉토리에 저장하고
   다른 문서에서는 링크로 참조한다.
3. 정책과 API 계약의 최종 기준은 계속 `PLAN.md`와 `docs/`의 기존 설계 문서다.
4. 산출물이 기존 API, 보안 모델 또는 DB 모델을 변경하면 해당 기준 문서도 함께
   갱신한다.
5. 초안과 확정본을 중복 저장하지 않고 문서 상단에 상태와 기준일을 표시한다.
6. 비밀번호, token, private key, 실제 고객 데이터와 비공개 PVE endpoint를 저장하지
   않는다.
7. screenshot이나 fixture가 필요하면 같은 단계 디렉토리 아래 `images/`,
   `fixtures/`처럼 용도가 명확한 하위 디렉토리를 사용한다.
8. 단계가 완료되면 이 인덱스에 대표 산출물과 상태를 추가한다.

## 단계별 산출물

| 단계 | 상태 | 대표 산출물 |
|---|---|---|
| 0 — 제품 정책과 운영 기준 | 완료 | [제품 정책과 운영 기준](00-product-policy/product-policy.md) |
| 1 — CI와 실제 사용자 여정 품질 게이트 | 원격 적용 확인 대기 | [검증 보고서](01-ci-browser-quality-gates/verification-report.md), [branch protection 기준](01-ci-browser-quality-gates/branch-protection.md) |
| 2 — Scheduler, Outbox와 정기 유지보수 기반 | 구현·검증 완료 | [설계 결정](02-scheduler-outbox-maintenance/design.md), [운영 runbook](02-scheduler-outbox-maintenance/runbook.md), [검증 보고서](02-scheduler-outbox-maintenance/verification-report.md) |
| 3 — 자동 Inventory Sync와 Reconciliation | 구현·검증 완료 | [설계 결정](03-inventory-sync-reconciliation/design.md), [운영 runbook](03-inventory-sync-reconciliation/runbook.md), [검증 보고서](03-inventory-sync-reconciliation/verification-report.md) |
| 4 — 관리자 Operation 센터와 복구 흐름 | 구현·로컬 검증 완료 | [설계 결정](04-operation-center-recovery/design.md), [운영 runbook](04-operation-center-recovery/runbook.md), [검증 보고서](04-operation-center-recovery/verification-report.md) |
| 5 — MFA, Session과 고위험 작업 보호 | 구현·로컬 검증 완료 | [설계 결정](05-mfa-session-security/design.md), [운영 runbook](05-mfa-session-security/runbook.md), [검증 보고서](05-mfa-session-security/verification-report.md) |
| 6 — 경보, Incident와 Notification | 구현·로컬 검증 완료 | [설계 결정](06-alert-incident-notification/design.md), [운영 runbook](06-alert-incident-notification/runbook.md), [검증 보고서](06-alert-incident-notification/verification-report.md) |
| 7 — PBS 백업 자동화와 복구 검증 | 구현·로컬 검증 완료 | [설계 결정](07-pbs-backup-automation/design.md), [운영 runbook](07-pbs-backup-automation/runbook.md), [검증 보고서](07-pbs-backup-automation/verification-report.md) |
| 8 — 고객 작업 이력, VM 상세와 성능 지표 | 구현·로컬 검증 완료 | [설계 결정](08-customer-history-metrics/design.md), [운영 runbook](08-customer-history-metrics/runbook.md), [검증 보고서](08-customer-history-metrics/verification-report.md) |
| 9 — 고객 계정 보안과 알림 설정 | 구현·로컬 검증 완료 | [설계 결정](09-customer-security-notifications/design.md), [운영 runbook](09-customer-security-notifications/runbook.md), [검증 보고서](09-customer-security-notifications/verification-report.md) |
| 10 — 제한된 고객 Self-service | 구현·로컬 검증 완료 | [설계 결정](10-customer-self-service/design.md), [운영 runbook](10-customer-self-service/runbook.md), [검증 보고서](10-customer-self-service/verification-report.md) |
| 11 — 조직 관리자, 팀 권한, Quota와 승인 | 구현·로컬 검증 완료 | [설계 결정](11-organization-rbac-quota/design.md), [운영 runbook](11-organization-rbac-quota/runbook.md), [검증 보고서](11-organization-rbac-quota/verification-report.md) |
| 12 — 관리자 고급 PVE 운영 | 구현·로컬 검증 완료 | [설계 결정](12-advanced-pve-operations/design.md), [운영 runbook](12-advanced-pve-operations/runbook.md), [검증 보고서](12-advanced-pve-operations/verification-report.md) |
| 13 — 상용 호스팅 기능 | 정책상 보류 | [착수 결정](13-commercial-hosting/decision.md) |
| 14 — 출시 Hardening과 운영 인수 | 로컬 hardening 완료·staging 승인 대기 | [Staging 검증 계획](14-release-hardening/staging-test-plan.md), [운영 인수](14-release-hardening/operations-handover.md), [검증 보고서](14-release-hardening/verification-report.md) |
