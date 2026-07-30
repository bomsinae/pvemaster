# 단계 8 운영 runbook

- 상태: 운영 인수 준비
- 기준일: 2026-07-26

## 배포

1. migration `20260726_0021`을 적용하고 `alembic current`가 head인지 확인한다.
2. inventory worker가 workload uptime을 갱신하는지 확인한다.
3. scheduler와 maintenance worker에서 1분 수집 및 5분 rollup·보존 작업이 등록됐는지
   확인한다.
4. 고객 계정으로 자기 VM 상세와 day/month/year 지표를 확인한다.

## Metric 누락 대응

1. 화면의 `마지막 확인`, stale 경고와 `partial` 상태를 구분한다.
2. cluster 연결과 inventory 최신성, worker heartbeat, maintenance queue를
   확인한다.
3. PVE RRD 권한과 API timeout을 확인하되 token이나 PVE 원문 응답을 로그로 남기지
   않는다.
4. assignment 시작 직후의 부분 지표는 정상일 수 있다. 과거 조직의 지표를 채워 넣지
   않는다.
5. 보존 작업 재실행은 bucket upsert로 안전하지만 실행 전 DB 여유 공간과 backlog를
   확인한다.

## 소유권 사고 확인

1. 사용자를 조직에서 제거하거나 workload를 재할당한 즉시 기존 상세·작업 URL이
   404인지 확인한다.
2. 새 소유자에게 assignment 이전 operation과 metric이 보이지 않는지 확인한다.
3. 의심 응답에는 cluster/node/UPID/endpoint와 내부 오류 원문이 없는지 확인한다.
4. 경계 위반이 의심되면 고객 이력 API를 우선 차단하고 감사 request ID를 기준으로
   조사한다. 과거 metric 행을 임의로 새 조직에 재귀속하지 않는다.
