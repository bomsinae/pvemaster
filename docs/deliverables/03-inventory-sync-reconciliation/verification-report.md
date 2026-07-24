# 자동 Inventory Sync와 Reconciliation 검증 보고서

## 문서 상태

- 상태: 구현·로컬 검증 완료
- 기준일: 2026-07-24
- 범위: `docs/tasks/03-inventory-sync-reconciliation.md`

## 검증 대상

- 동일 VMID의 cluster 범위 격리
- generation과 중복 sync 요청 병합
- 전체 성공 후 workload/node/storage tombstone
- 부분 응답에서 tombstone 금지
- 누락 workload의 assignment 유지
- 재등장과 node/spec/power drift finding
- finding 확인·담당·해결 상태 전이
- 고객 stale 표시와 서버 전원 작업 제한
- PVE 인증, TLS, timeout, rate limit, 잘못된 응답 분류

## 결과

| 검사 | 결과 |
|---|---|
| Backend pytest (PostgreSQL 통합 포함) | 133 passed |
| Ruff format/check | 통과 |
| mypy strict | 86 source files, 오류 없음 |
| Alembic `0016 → 0015 → head` 왕복 | 통과 |
| Frontend ESLint / TypeScript | 통과 |
| Frontend mock 계약 테스트 | 9 passed |
| Next.js production build | 통과 |
| Playwright Chromium/Firefox/WebKit | 45개 시나리오 검증 완료 |

Playwright 최초 4-worker 실행은 41개가 통과했고 기존 Firefox 1건과 WebKit 3건이
로컬 브라우저 자원 경합으로 timeout 됐다. 실패 4건을 단일 worker로 재실행해 모두
통과했다. 신규 inventory/reconciliation 관리자 여정과 고객 stale 차단 여정은
세 브라우저 엔진에서 모두 통과했다.

단위·통합 테스트는 실제 PVE를 호출하지 않고 `InventorySnapshot` loader와 HTTP
mock을 사용했다. 인증·권한·TLS·timeout·rate limit·잘못된 PVE 응답 분류는 기존
Proxmox client 계약 테스트와 단계 3 테스트를 함께 실행해 확인했다.

## staging 후속 확인

- 실제 cluster 규모에서 nodes/guests/storages 응답 시간과 lease TTL 조정
- PVE 권한 일부 제거 시 `PARTIAL` run과 tombstone 금지 확인
- Redis 중단·복구 중 durable `QUEUED` run 재발행 확인
- 외부 VM 삭제·이동 표본으로 assignment/IP 보존 확인
