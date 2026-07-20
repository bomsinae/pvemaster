# 백업 및 복구 가이드

## 백업 범위

PostgreSQL은 사용자, 조직, 소유권, 암호화된 PVE credential, IPAM, 작업, 프로비저닝, 감사 기록의 기준이다. Redis는 Celery 전달과 단기 heartbeat용이므로 업무 백업 대상으로 보지 않는다. `.env`, 애플리케이션 암호화 키, PostgreSQL/Redis 암호는 DB 백업과 분리된 secret manager에 보관한다. 키가 없으면 암호화된 PVE token을 복구할 수 없다.

DB 백업은 암호화된 저장소에 보관하고 전송 구간 TLS, 최소 권한, 접근 감사, 보존 정책을 적용한다. 감사 로그 장기 사본은 DB 백업과 별도로 WORM 저장소에 둔다.

## 백업 점검

아래 예시는 값 자체를 명령행에 넣지 않고 표준 PostgreSQL 환경 또는 secret file을 사용한다.

```bash
pg_dump --format=custom --no-owner --no-acl --file=/secure-backup/pvemaster.dump "$DATABASE_URL"
pg_restore --list /secure-backup/pvemaster.dump
sha256sum /secure-backup/pvemaster.dump
```

백업마다 생성 시각, 애플리케이션 버전, Alembic revision, 암호화 키 버전, checksum을 별도 manifest에 기록한다. 백업 로그에는 URL의 비밀번호를 남기지 않는다.

## 복구 절차

1. 원본과 격리된 새 PostgreSQL 인스턴스를 준비한다.
2. 애플리케이션 배포 버전과 Alembic revision을 manifest에 맞춘다.
3. secret manager에서 동일한 애플리케이션 암호화 키를 안전하게 주입한다.
4. 빈 DB에 백업을 복원하고 migration 상태를 확인한다.
5. API/worker를 외부 PVE 호출이 차단된 상태로 시작해 DB 무결성, 감사 로그 수, 암호 복호화 가능 여부를 확인한다.
6. Redis를 비운 새 인스턴스로 시작하고 미완료 작업 recovery가 중복 실행을 일으키지 않는지 검토한다.
7. 운영 전환 전에 클러스터 연결은 하나씩 시험하고 큐를 단계적으로 개방한다.

```bash
createdb pvemaster_restore
pg_restore --exit-on-error --no-owner --no-acl --dbname=pvemaster_restore /secure-backup/pvemaster.dump
docker compose exec backend alembic current
curl --fail http://localhost:8000/api/v1/health/ready
```

## 복구 검증

- 사용자·조직·VM 소유권·IP 할당 수를 백업 manifest와 비교한다.
- 진행 중 operation/provisioning request와 UPID를 PVE에서 재조정한 뒤 worker를 연다.
- `audit_logs` UPDATE/DELETE가 거부되는지 확인하고 외부 감사 사본과 기간별 건수를 대조한다.
- 임의 credential 한 건의 연결 시험으로 암호화 키 일치를 확인하되 token을 출력하지 않는다.
- RPO/RTO를 기록하고 실패한 단계와 개선 작업을 남긴다.

복구 훈련은 최소 분기마다 수행한다. 운영 DB에 덮어쓰는 복구는 사고 책임자의 명시적 승인 없이 실행하지 않는다.
