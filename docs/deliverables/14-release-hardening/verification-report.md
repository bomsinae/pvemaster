# 단계 14 — 출시 준비 검증 보고서

## 문서 상태

- 상태: **출시 차단 — staging·운영 승인 증거 대기**
- 기준일: 2026-07-26
- 로컬 기준 branch: `agent/ci-browser-quality-gates`

## 저장소 내 완료 항목

- backend와 frontend 응답 보안 헤더 기본값 추가
- production API HSTS 활성화와 회귀 테스트 추가
- secret, backend, migration drift, frontend와 3개 브라우저를 묶은
  `scripts/verify-release.sh` 추가
- 고급 PVE 기능별 kill switch를 `.env.example`과 Compose backend에 연결
- staging E2E·장애 주입 시험 계획과 운영 인수·공지·rollback 체크리스트 작성
- 기존 GitHub Actions의 dependency, secret, SBOM과 container scan 구성을 재확인

## 로컬 검증 결과

- Alembic fresh upgrade와 schema drift 검사: 통과
- Backend Ruff format/check, mypy와 PostgreSQL 통합 전체 160개: 통과
- Frontend ESLint, TypeScript, mock 계약 46개와 production build: 통과
- Chromium, Firefox, WebKit 실제 브라우저 여정 78개: 통과
- committed secret scan: 통과
- Python dependency audit: 알려진 취약점 0건
- Node production dependency audit: 알려진 취약점 0건
- backend/frontend production container image build와 Trivy HIGH/CRITICAL scan: 통과

개발 도구 체인의 ESLint plugin 하위 `brace-expansion`에는 현재 upstream에서 호환되는
수정 경로가 없는 DoS advisory가 남아 있다. production dependency와 runtime image에는
포함되지 않고 저장소가 통제하는 lint glob만 처리하므로 출시 runtime 위험과 분리해
추적한다. 호환되는 ESLint plugin 갱신이 제공되면 lockfile을 갱신하고 전체 gate를
재실행한다.

## 출시 blocker

- 실제 staging PVE/PBS에서 STG-01~10을 실행한 증거가 없다.
- PostgreSQL, Redis, worker와 PVE 장애 주입 NF-01~05 결과가 없다.
- DB/PBS 복구 훈련에서 측정한 RPO/RTO 결과가 없다.
- 부하 시험, 보안 검토, 접근성 점검과 감사 외부 보관 증거가 없다.
- dashboard/alert rule 배포 및 on-call·보안·release manager 승인이 없다.

따라서 이 문서는 단계 14의 자동화 가능한 hardening 완료를 기록하지만 운영 출시
승인을 의미하지 않는다. 위 blocker가 모두 해소되고 고위험 미해결 항목이 없을 때만
상태를 `출시 승인`으로 변경한다.
