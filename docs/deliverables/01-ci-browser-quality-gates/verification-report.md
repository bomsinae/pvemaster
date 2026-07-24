# CI와 실제 사용자 여정 품질 게이트 검증 보고서

## 문서 상태

- 상태: 로컬 구현·검증 완료, 원격 CI 및 branch protection 적용 확인 대기
- 기준일: 2026-07-24
- 범위: `docs/tasks/01-ci-browser-quality-gates.md`

## 구현 결과

### CI와 공급망

- Backend pytest, Ruff format/check, mypy를 독립 required check로 구성했다.
- Frontend lint, typecheck, 기존 mock suite, production build를 구성했다.
- 실제 Playwright suite를 기존 `frontend/tests/*.e2e.test.ts`와 분리해
  `frontend/browser-tests/`에서 실행한다.
- PostgreSQL 17에서 Alembic `upgrade head → downgrade base → upgrade head`를
  재현한다.
- Python/Node dependency audit, 신규 dependency review, 고신뢰 secret pattern,
  backend/frontend image Trivy 검사, SPDX JSON SBOM 생성을 구성했다.
- Dependabot이 Python, npm, GitHub Actions dependency를 매주 확인한다.

### 실제 브라우저

자동화한 핵심 여정:

- SUPER_ADMIN 로그인, 클러스터 등록, 연결 시험, 인벤토리 가져오기, 조직 할당
- 고객 로그인, 자기 VM과 IP 조회, 전원 작업 요청, 비동기 완료 및 최종 상태 반영
- 다른 조직 VM, 과거 소유 VM을 404로 은닉하고 비활성 조직을 403으로 거부
- desktop, tablet, mobile reflow와 가로 overflow 방지
- 느린 목록 API의 loading 안내, timeout 오류 안내, 새로고침 후 복구
- dialog/drawer focus trap, Escape 닫기, 닫힌 뒤 원래 trigger로 focus 복귀
- axe WCAG 2 A/AA serious/critical 위반 차단, reduced motion 검증

Playwright는 세 browser project와 개발 서버를 한 설정에서 관리한다. 공식 문서의
[`webServer`](https://playwright.dev/docs/test-webserver) 및
[`projects`](https://playwright.dev/docs/test-projects) 구성을 따른다. axe 검사는
[`@axe-core/playwright` 공식 예시](https://playwright.dev/docs/accessibility-testing)를
따르며, 자동 검사가 모든 접근성 문제를 발견하지 못하므로 수동 keyboard·zoom·실제
보조기술 검증을 대체하지 않는다.

### 접근성 UI 보완

- 공용 dialog focus hook을 추가해 첫 control focus, Tab 순환, Escape, focus 복귀를
  구현했다.
- 관리자 drawer에 명시적 dialog 이름과 닫기 버튼 이름을 추가했다.
- 전원 작업 완료와 관리자 알림을 live region으로 전달한다.
- 고객 초기 목록 loading을 status live region으로 전달한다.
- reduced motion과 forced-colors 환경의 focus/border 표현을 보완했다.

## 검증 결과

| 검사 | 결과 |
|---|---|
| Backend Ruff format | 117 files 통과 |
| Backend Ruff check | 통과 |
| Backend mypy | 75 source files 통과 |
| Backend pytest (PostgreSQL/Redis integration 포함) | 114 passed |
| Frontend ESLint | 통과 |
| Frontend TypeScript | 통과 |
| 기존 mock frontend suite | 9 passed |
| Production build | 통과 |
| Playwright Chromium/Firefox/WebKit | 39 passed |
| Alembic 왕복 | 14 revisions upgrade/downgrade/re-upgrade 통과 |
| Python dependency audit | 알려진 취약점 없음 |
| Node dependency audit | `npm install` audit 기준 취약점 없음 |
| secret scan | 통과 |
| workflow YAML parse | 통과 |

CI backend job은 먼저 Alembic schema를 적용하고 PostgreSQL·Redis service와
`AUTH_TEST_DATABASE_URL`, `AUTH_TEST_REDIS_URL`을 제공해 opt-in integration 검사까지
항상 활성화한다. 이 구성을 임시 PostgreSQL 17·Redis 7.4 컨테이너에서 재현해
114개 전체 검사가 통과하는 것을 확인했다.

## 기존 보안·복구 검사의 CI 편입

다음 세부 경로는 기존 backend integration/unit test가 이미 검증하며, 새 backend
required check가 이를 병합 차단 범위에 포함한다.

- 관리자 QEMU/LXC 및 고객 QEMU 콘솔 scope, 일회용 grant, 만료, 동시 연결 제한
- IP 예약·할당 중복과 격리
- 프로비저닝 성공, 실패, timeout, idempotency 및 재개
- 백업·복구 성공, 실패, timeout, 중복 요청
- 고객 객체 소유권, 과거 소유권과 비활성 조직 차단

실제 PVE를 CI에서 호출하지 않으며 fake/mock client를 사용한다.

## 남은 적용 확인

1. GitHub dependency graph를 활성화하고 dependency review를 required check로
   승격한다.
2. dependency audit와 image scan에서 새로 발견되는 실제 취약점을 지속적으로
   triage한다.
3. [branch protection 운영 기준](branch-protection.md)을 repository settings에
   적용하고 실패 check의 병합 차단을 확인한다.
4. staging에서 실제 PVE/PBS와 console WebSocket을 포함한 smoke test를 수행한다.
5. 수동 200% zoom, Windows High Contrast, VoiceOver/NVDA keyboard 검증을 수행한다.

최초 원격 Python audit에서 `cryptography 46.0.7`의
`GHSA-537c-gmf6-5ccf`가 발견되어 최소 버전을 `48.0.1`로 상향했다. 로컬
환경에서의 Python audit와 원격 재검사는 모두 알려진 취약점 없이 통과했다.

최초 원격 production Node audit에서는 `next 16.2.10`, `postcss 8.5.10`,
`sharp 0.34.5`에 high severity 취약점 3건이 발견됐다. Next.js와 ESLint 구성을
`16.2.11`, PostCSS를 `8.5.12`, Sharp를 `0.35.0`으로 상향했다. 갱신 후
`npm install` audit은 355 packages에서 취약점 0건을 보고했다.
