# 단계 1 — CI와 실제 사용자 여정 품질 게이트 (P0)

## 목표

현재 단위·통합·mock frontend 테스트를 실제 브라우저와 배포 품질 게이트로 확장한다.

## 작업

### CI

- Backend pytest, Ruff format/check, mypy를 CI 필수 검사로 등록한다.
- Frontend lint, typecheck, 실제 Playwright, production build를 필수 검사로 등록한다.
- Alembic upgrade와 downgrade 또는 명시된 비가역 migration 검사를 추가한다.
- dependency, container image, secret, SBOM 검사를 추가한다.
- 품질 검사 실패가 병합을 차단하도록 branch protection 기준을 문서화한다.

### 실제 브라우저 E2E

- SUPER_ADMIN 로그인 → 클러스터 등록 → 연결 시험 여정을 검증한다.
- 인벤토리 sync → VM 가져오기 → 조직 할당 여정을 검증한다.
- 고객 로그인 → 자기 VM 조회 → 전원 작업 → 작업 완료 여정을 검증한다.
- 다른 조직 VM UUID 접근, 과거 소유 VM, 비활성 조직 접근이 모두 거부되는지 검증한다.
- 관리자 QEMU/LXC 콘솔과 고객 QEMU 콘솔의 권한·만료·중복 연결을 검증한다.
- 프로비저닝, IP 예약, 백업, 복구의 주요 성공·실패 흐름을 검증한다.
- 모바일, 태블릿, 데스크톱 반응형 동작을 검증한다.

### 접근성

- 키보드만으로 로그인, 탐색, dialog, drawer, table action을 사용할 수 있게 한다.
- dialog focus trap, 닫힘 후 focus 복귀, 오류 요약과 field 연결을 검증한다.
- 상태 변화와 비동기 완료를 `aria-live`로 전달한다.
- 색상 외에 텍스트·아이콘으로 상태와 위험을 구분한다.
- 확대, reflow, reduced motion, 고대비 환경을 검증한다.

## 테스트

- Chromium, Firefox, WebKit 핵심 여정
- axe 기반 자동 접근성 검사와 수동 키보드 검사
- 느린 네트워크, API timeout, 부분 실패, 새로고침 복구
- CI에서 migration과 frontend build 재현

## 완료 조건

- 실제 브라우저 E2E가 mock 테스트와 구분되어 실행된다.
- 핵심 관리자/고객 여정과 고객 간 격리가 CI에서 자동 검증된다.
- P0/P1 화면에 중대한 키보드·focus·대비 문제가 없다.
- 모든 필수 품질 게이트가 CI에서 강제된다.


---

[전체 작업 인덱스로 돌아가기](README.md)
