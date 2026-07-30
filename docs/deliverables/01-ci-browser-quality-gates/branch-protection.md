# Branch protection 운영 기준

## 문서 상태

- 상태: 적용 준비 완료
- 기준일: 2026-07-24
- 대상 브랜치: `main`
- 관련 workflow: `.github/workflows/quality.yml`, `.github/workflows/security.yml`

## 보호 규칙

GitHub repository settings에서 `main`에 다음 ruleset 또는 branch protection을 적용한다.

1. pull request 없이 `main`에 직접 push하지 못하게 한다.
2. 승인 1명 이상과 최신 커밋 기준 재승인을 요구한다.
3. 모든 review thread 해결을 요구한다.
4. branch가 최신 `main`을 포함하도록 요구한다.
5. 관리자 우회를 허용하지 않는다. 긴급 우회가 필요하면 사후 감사 이슈와
   incident 기록을 남긴다.
6. force push와 branch 삭제를 금지한다.
7. 아래 status check가 모두 성공해야 병합할 수 있게 한다.

## 필수 status check

| Workflow | Required check |
|---|---|
| Quality gates | `Backend / pytest, Ruff, mypy` |
| Quality gates | `Database / Alembic round trip` |
| Quality gates | `Frontend / lint, types, mock, build` |
| Quality gates | `Browser / Playwright (Chromium, Firefox, WebKit)` |
| Security and supply chain | `Security / committed secrets` |
| Security and supply chain | `Security / dependency vulnerabilities` |
| Security and supply chain | `Supply chain / SBOM` |
| Security and supply chain | `Security / container images` |

`Security / dependency review`는 pull request에서 보조 검사로 실행하지만, 현재
repository는 Dependency graph가 비활성화되어 있어 required check에 포함하지
않는다. Dependency graph를 활성화한 뒤 action의 성공을 확인하고 required check로
승격한다. 그 전까지 Python `pip-audit`과 production Node `npm audit`가
high-severity dependency를 병합 차단한다.

## 실패 처리

- 테스트와 정적 검사 실패는 코드를 수정한 뒤 다시 실행한다.
- dependency 또는 image 취약점은 영향을 확인하고 업데이트·완화·예외 만료일을
  security review에 기록한다.
- false positive secret은 탐지 규칙 전체를 끄지 않고, 비밀값이 아닌 근거가 명확한
  파일만 최소 범위로 제외한다.
- Playwright 실패 artifact는 14일 보관하며 trace, screenshot, video로 재현한다.
- SBOM은 매 push 및 주간 보안 검사에서 SPDX JSON으로 생성한다.

## 근거

- Playwright의 공식 CI 예시는 브라우저와 OS 의존성 설치 후 테스트 실행 및 실패
  artifact 업로드를 권장한다:
  [Playwright CI](https://playwright.dev/docs/ci)
- 하나의 suite를 Chromium, Firefox, WebKit project로 실행하는 구성은 공식 project
  기능을 따른다:
  [Playwright projects](https://playwright.dev/docs/test-projects)
- GitHub 공식 action은 현재 checkout v6, setup-node v6, setup-python v6 계열을
  사용한다:
  [checkout](https://github.com/actions/checkout),
  [setup-node](https://github.com/actions/setup-node),
  [setup-python](https://github.com/actions/setup-python)

## 적용 확인

workflow 파일만으로 GitHub의 `main` 보호 규칙이 자동 적용되지는 않는다. repository
관리자가 위 규칙을 설정한 뒤, 보호된 PR에서 각 check가 required로 표시되고 실패한
check가 병합을 차단하는 화면을 확인해야 최종 적용 완료로 본다.
