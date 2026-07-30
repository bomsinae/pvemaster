# Operation 센터와 복구 흐름 검증 보고서

## 문서 상태

- 상태: 구현·로컬 검증 완료
- 기준일: 2026-07-24
- 범위: `docs/tasks/04-operation-center-recovery.md`

## 검증 대상

- 관리자 통합 목록, 필터, 상세 timeline과 안전한 UPID reference
- queued 취소와 실행 후 취소 거부
- safe retry와 중복 retry 멱등성
- 담당자 지정·확인·수동 해결과 version conflict
- worker heartbeat 만료 redelivery와 불명확 결과 `NEEDS_ATTENTION`
- 고객 작업 새로고침 복원, 고객 간 IDOR와 과거 소유 차단
- API 역할 제한과 raw 내부 정보 비노출
- Alembic upgrade/downgrade 왕복
- 관리자/고객 브라우저 여정과 접근성

## 결과

| 검사 | 결과 |
|---|---|
| Backend pytest (PostgreSQL 통합 포함) | 135 passed |
| Ruff format/check | 통과 |
| mypy strict | 90 source files, 오류 없음 |
| Alembic `0017 → 0016 → head` 왕복 | 통과 |
| Alembic model/schema drift 검사 | 변경 누락 없음 |
| Frontend ESLint / TypeScript | 통과 |
| Frontend mock 계약 테스트 | 9 passed |
| Next.js production build | 통과 |
| Playwright Chromium/Firefox/WebKit | 48 passed |

모든 PVE 동작 테스트는 fake client 또는 HTTP mock을 사용했으며 실제 PVE 서버를
호출하지 않았다. 신규 브라우저 여정은 실패 operation의 원인·timeline 확인과 확인
처리를 세 브라우저에서 검증했다. 고객 여정은 작업 완료 뒤 서버 목록을 다시 읽어
새로고침 복원 상태가 유지되는지 확인했다.

## 의존 후속

대량 export는 단계 5의 MFA challenge, step-up token과 만료 URL 저장소를 사용해
구현한다. 해당 보안 경계가 준비되기 전에는 export API를 노출하지 않는다.
