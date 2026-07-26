# 운영 가이드

## 상태 확인과 지표

- `GET /api/v1/health/live`: 프로세스 liveness. 외부 의존성을 확인하지 않는다.
- `GET /api/v1/health/ready`: PostgreSQL과 Redis readiness. 의존성 장애 시 `503`이다.
- `GET /api/v1/admin/operations/status`: `SUPER_ADMIN`과 `OPERATOR`가 worker heartbeat,
  목적별 큐 길이, scheduler 작업의 최근 실행·성공·실패, 클러스터 연결 상태,
  VM/CT 할당 현황, 활성·전체 사용자와 조직 수, 활성 경보를 조회한다.
- `GET /metrics`: Prometheus text exposition endpoint. worker, Celery 큐, scheduler 최근
  성공 시각·실패 여부, 클러스터 연결, 작업 상태, IP 풀 가용 주소,
  stale inventory cluster와 reconciliation finding 지표를 제공한다.
  운영에서는 reverse proxy 또는 네트워크 정책으로 Prometheus만 접근하도록 제한한다.

Worker는 Redis의 TTL heartbeat를 갱신한다. heartbeat가 없으면 `WORKER_DOWN`, 대기 작업 합계가 `QUEUE_BACKLOG_ALERT_THRESHOLD` 이상이면 `JOB_QUEUE_BACKLOG` 경보가 발생한다. 다음 조건도 운영 상태 응답에 포함된다.

- 활성 클러스터의 최근 연결 실패
- 설정 시간창 내 프로비저닝 실패 또는 manual review 반복
- IP 풀 가용 주소 부족. CIDR 크기에서 네트워크 주소, IPv4 broadcast, gateway,
  제외 범위와 `RESERVED`/`ASSIGNED`/`QUARANTINED`/`DISABLED` 주소를 중복 없이
  차감하며, sparse IPv6 풀도 주소를 열거하지 않고 계산한다.
- 마지막 전체 inventory 성공이 stale 기준을 넘긴 클러스터
- 해결되지 않은 critical reconciliation finding

조직 미할당 VM/CT는 정상적인 관리 인벤토리일 수 있으므로 운영 경보가 아니라 상태 응답의 할당 현황(`assigned`, `unassigned`, `total`)으로 제공한다. 이 집계에는 활성 클러스터에서 현재 관측되는 비템플릿 VM/CT만 포함한다.

상태 응답은 경보 전달 자체를 대신하지 않는다. Prometheus/Alertmanager가 `/metrics`와 상태 API를 수집하고 PagerDuty, Slack 같은 운영 채널로 라우팅하도록 구성한다.

## 감사 로그

`SUPER_ADMIN`만 `GET /api/v1/admin/audit-logs`와 상세 endpoint를 조회할 수 있다. action, actor, organization, result 필터와 limit/offset을 지원한다. 변경·삭제 API는 없다.

감사 행은 DB trigger로 UPDATE/DELETE가 차단된다. 비밀번호, refresh/access/PVE token,
Authorization, 쿠키, private key, CA bundle 키는 중첩된 before/after에서도
`[REDACTED]`로 저장된다. 온라인 보존 기간의 기본값은 365일이다. 삭제는
maintenance queue의 `app.tasks.scheduler.run_retention`이 하루 한 번 실행한다.
장기 보존은 별도 WORM/SIEM으로 전송한다.

## 배포 및 점검 명령

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl --fail http://localhost:8000/api/v1/health/live
curl --fail http://localhost:8000/api/v1/health/ready
curl --fail http://localhost:8000/metrics
docker compose exec backend alembic current
docker compose exec worker-operations celery -A app.worker:celery_app inspect ping --timeout 5
docker compose exec worker-inventory celery -A app.worker:celery_app inspect ping --timeout 5
docker compose exec worker-maintenance celery -A app.worker:celery_app inspect ping --timeout 5
docker compose exec redis redis-cli --no-auth-warning LLEN operations
docker compose exec redis redis-cli --no-auth-warning LLEN inventory
docker compose exec redis redis-cli --no-auth-warning LLEN maintenance
```

인증이 필요한 운영 상태는 access token을 shell history에 직접 쓰지 말고 안전한 비밀 주입 방식으로 전달한다. 로그 수집 설정에서 Authorization, Cookie, request body를 제외한다.

## 테스트와 품질 점검

통합 테스트에는 운영 DB가 아닌 전용 PostgreSQL을 사용한다.

```bash
cd backend
AUTH_TEST_DATABASE_URL='postgresql+asyncpg://<test-user>:<password>@<host>/<test-db>' .venv/bin/pytest
.venv/bin/ruff check app tests
.venv/bin/mypy
cd ../frontend
npm run lint
npm run typecheck
npm run test:e2e
npm run build
```

실제 PVE는 단위·통합 테스트에서 호출하지 않는다. staging 점검에서는 최소 권한 전용 token, 등록된 CA, 별도 테스트 VMID/IP 대역을 사용한다.

전체 로컬 release gate는 저장소 루트에서 다음 명령으로 실행한다.

```bash
./scripts/verify-release.sh
```

이 명령은 secret scan, backend format/lint/type/test/migration drift, frontend
lint/type/mock/build와 Chromium·Firefox·WebKit 여정을 차례로 실행한다. 설정한 `.env`로
Compose 렌더링까지 확인하려면 `VERIFY_COMPOSE_CONFIG=1`을 사용한다. 로컬 gate 통과는
실제 staging PVE 시험이나 운영 승인 결과를 대신하지 않는다.

API는 모든 응답에 `nosniff`, frame 차단, referrer·권한 제한과 `no-store`를 적용한다.
`ENVIRONMENT=production`에서는 HSTS도 적용한다. 프론트엔드는 frame 차단,
cross-origin 격리와 브라우저 권한 제한 헤더를 적용한다. TLS 종료 reverse proxy는
HTTPS redirect와 HSTS가 최종 응답에서도 유지되는지 배포 후 확인한다.

## 정기 점검

- 매일: worker/queue/cluster 경보, 실패한 프로비저닝, manual review 확인.
- 매주: 미할당 VM/CT를 PVE 실제 상태와 대조하고 소유권 할당 또는 유지 결정을 기록.
- 매월: 감사 로그 외부 보관 확인, 복원 표본 시험, PVE token 권한 검토.
- 분기: 전체 복구 훈련, 암호화 키 및 token 회전 절차 시험, incident runbook tabletop.

## PBS 워크로드 백업 점검

- PVE에서 PBS storage를 먼저 등록하고 `content=backup`, 활성 상태, 대상 node 접근성을 확인한다.
- cluster마다 별도 PBS API token과 namespace를 사용하고 token에는 삭제 권한을 부여하지 않는다.
- PVE Master의 `백업` 화면에서 storage 검색 후 대상으로 등록한다.
- 실패 내역의 `error_code`와 연결된 operation/PVE task를 확인하되 UPID와 credential을 외부에 공유하지 않는다.
- 제출 단계 timeout은 결과가 불명확하므로 수동 재실행 전에 PVE task와 PBS snapshot 존재 여부를 확인한다.
- `NEEDS_ATTENTION` 또는 `MANUAL_REVIEW` 작업은 Operation 센터에서 담당자를 지정하고
  안전한 reference와 대상 실제 상태를 대조한 뒤 해결 근거를 기록한다.
- 보존 정책과 prune은 PBS에서 관리한다. PVE Master MVP는 snapshot을 삭제하지 않는다.
- 복구는 성공 백업 상세에서 새 VMID로만 실행한다. 요청 전에 대상 node의 용량과 VMID가 비어 있는지 확인한다.

## 고급 PVE 운영 기능 flag

고급 기능은 배포 환경별로 다음 값을 개별 활성화한다. 초기값은 모두 `false`다.

- `ADVANCED_SNAPSHOT_ENABLED`
- `ADVANCED_MIGRATION_ENABLED`
- `ADVANCED_HA_ENABLED`
- `ADVANCED_NODE_MAINTENANCE_ENABLED`
- `ADVANCED_BULK_ENABLED`
- `ADVANCED_GUEST_CONFIG_ENABLED`
- `ADVANCED_FIREWALL_SDN_ENABLED`

활성화 전 staging에서 해당 PVE token의 최소 ACL, storage/network/guest capability,
step-up MFA와 customer downtime 알림 절차를 확인한다. Firewall/SDN flag는 현재
read-only 투영만 활성화하며 변경 권한을 token에 추가하지 않는다.

긴급 중지는 관련 flag 하나만 `false`로 바꾸고 backend를 재기동한다. 이미 제출된
PVE task는 flag 변경으로 취소되지 않으므로 Operation 센터에서 UPID의 실제 상태를
확인하고 재조정한다. DB 행이나 queue를 직접 삭제하지 않는다.
- 복구 작업은 전원을 켜지 않은 상태로 완료된다. inventory를 갱신한 뒤 NIC, IP와 조직 할당을 검토하고 수동으로 기동한다.
- PVE API token에 VM.Allocate, Datastore.AllocateSpace와 복구 원본을 읽을 권한이 없으면 작업은 권한 오류로 종료된다.
