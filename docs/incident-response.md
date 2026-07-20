# 사고 대응 가이드

## 공통 절차

1. 탐지 시각, 담당자, 영향 범위, request ID/job ID/cluster ID를 사건 기록에 남긴다.
2. 비밀값을 채팅·티켓·로그에 복사하지 않는다. 증거는 접근 통제된 저장소에 보존한다.
3. 추가 피해를 막는 최소 조치를 적용한다. DB와 감사 로그 원본은 수정하지 않는다.
4. PVE 실제 상태, PostgreSQL 업무 상태, Celery 전달 상태를 분리해 확인한다.
5. 복구 후 감사 로그와 외부 관측 데이터를 대조하고 재발 방지 작업을 등록한다.

## 클러스터 연결 실패

- `/api/v1/admin/operations/status`의 error code와 마지막 성공 시각을 확인한다.
- DNS, route, 방화벽, 인증서 만료/체인, PVE API 상태를 확인한다. TLS 검증을 끄거나 `verify=False`로 우회하지 않는다.
- `401/403`이면 token 식별자·권한·만료/회전을 PVE에서 확인한다. token 원문은 출력하지 않는다.
- 복구 후 연결 시험과 제한된 read API를 먼저 실행하고 작업 큐를 연다.

## Worker 중단 또는 큐 적체

- worker heartbeat와 `celery inspect ping`, Redis 연결, container restart 원인을 확인한다.
- 무작정 큐를 삭제하지 않는다. DB의 QUEUED/RUNNING 작업과 PVE UPID를 대조한다.
- worker를 한 개만 먼저 복구해 recovery/idempotency 동작을 확인한 후 확장한다.
- poison message 또는 반복 실패는 작업 ID를 보존하고 격리하며 최대 재시도 정책을 유지한다.

## 프로비저닝 반복 실패

- 실패 단계, error code, clone 제출 여부, 예약 VMID/IP를 확인한다.
- clone 이후 실패는 VM을 자동 삭제하지 말고 `MANUAL_REVIEW`에서 PVE와 DB를 조정한다.
- `WAIT_CLONE` 완료 후 실패한 VM은 조직에 할당하지 않은 `UNKNOWN` 상태 workload로 자동 기록해 관리자 VM 목록에서 확인·격리·정리할 수 있게 한다. 고객 접근은 `ASSIGN_ORGANIZATION` 성공 전까지 허용하지 않는다.
- IP/VMID를 임의 해제하지 않는다. PVE에 생성된 VM, UPID, organization assignment를 먼저 확인한다.
- 원인이 template/Cloud-Init/node capacity이면 해당 template 또는 node를 비활성화해 신규 요청을 차단한다.

## IP 풀 부족과 orphaned VM

- IP 부족 시 예약·격리·할당 이력을 확인한다. 격리 시간을 우회하거나 주소를 직접 AVAILABLE로 변경하지 않는다.
- orphaned VM은 PVE의 실제 VMID/노드와 로컬 workload, 진행 중 provisioning을 대조한다.
- 소유권 확인 후 관리자 workflow로 할당·회수하고 감사 사건을 남긴다. 근거 없이 삭제하지 않는다.

## Credential 또는 계정 침해

- 사용자 계정을 비활성화하고 refresh token family/session epoch를 폐기한다.
- 영향받은 PVE API token을 PVE에서 먼저 폐기한 뒤 새 token을 최소 권한으로 발급해 애플리케이션에서 회전한다.
- 애플리케이션 암호화 키 노출 가능성이 있으면 모든 저장 credential을 회전하고 새 키 버전으로 재암호화한다.
- 감사 로그에서 로그인, 역할 변경, cluster token 변경, VM/IP 작업을 시간순으로 조사한다.

## 점검 명령

```bash
docker compose ps
docker compose logs --since=30m backend worker
docker compose exec worker celery -A app.worker:celery_app inspect active --timeout 5
docker compose exec worker celery -A app.worker:celery_app inspect reserved --timeout 5
curl --fail http://localhost:8000/api/v1/health/live
curl --fail http://localhost:8000/api/v1/health/ready
curl --fail http://localhost:8000/metrics
```

로그를 공유하기 전에 Authorization, Cookie, password, token, Cloud-Init payload가 포함되지 않았는지 확인한다. 사고 종료 조건은 서비스 복구뿐 아니라 소유권·IP·작업 상태 재조정, credential 회전, 감사 증거 보존, 후속 작업 책임자 지정까지 포함한다.
