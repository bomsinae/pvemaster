# 단계 14 — 운영 인수 체크리스트

## 문서 상태

- 상태: 승인 대기
- 기준일: 2026-07-26

## 배포 전

- release SHA, SBOM, dependency/container/secret scan 결과를 보존한다.
- Alembic upgrade/downgrade rehearsal과 DB snapshot 복구 가능 여부를 확인한다.
- `ENVIRONMENT=production`, HTTPS, secure cookie, MFA와 정확한 CORS/PVE allowlist를
  검토한다.
- 고급 PVE 기능 flag는 모두 비활성으로 배포하고 검증된 기능만 하나씩 활성화한다.
- dashboard와 alert rule의 owner, on-call 순번, escalation 연락망을 승인한다.

## 운영 인수

- on-call이 Operation 센터에서 실패·timeout·재시작 복구와 재조정을 수행한다.
- Alert/Incident에서 전달 실패, dedupe, acknowledge와 resolve를 처리한다.
- Inventory stale/tombstone과 reconciliation finding을 PVE 실제 상태와 대조한다.
- credential·암호화 키 회전 훈련은 이전 키 폐기, session revoke와 재연결까지 확인한다.
- PostgreSQL/PBS 복구 훈련은 측정한 RPO/RTO와 데이터 검증 결과를 남긴다.
- 감사 로그 redaction, 불변성, 보존 job과 외부 WORM/SIEM 전달을 확인한다.

## Kill switch와 rollback

- 고급 기능은 `ADVANCED_*_ENABLED=false`로 신규 요청을 차단한다.
- 고객 self-service 이상은 조직 approval policy를 승인 필요 상태로 강화하고 신규
  실행을 중단한다. 진행 중 operation은 삭제하지 않는다.
- 경보 전달 장애 시 채널을 비활성화하기보다 실패 delivery와 incident를 보존하고
  검증된 대체 채널로 전환한다.
- DB migration rollback은 해당 revision의 downgrade rehearsal과 데이터 영향 승인을
  받은 경우에만 수행한다. 이미 PVE에 제출된 작업은 Operation 센터에서 별도 조정한다.

## 공지 템플릿

### 계획 작업

> PVE Master의 계획 작업을 `[시작]`부터 `[종료 예상]`까지 진행합니다. 영향 범위는
> `[기능]`이며 기존 VM은 삭제되지 않습니다. 상태 변경 시 이 공지를 갱신합니다.

### 장애

> `[탐지 시각]`부터 `[기능]`의 지연/오류를 조사 중입니다. 기존 VM 상태는
> `[확인 결과]`이며 중복 요청을 피해주세요. 다음 갱신은 `[시각]`입니다.

### 복구

> `[복구 시각]`에 `[기능]`이 복구되었습니다. `[영향 범위]`을 재조정했으며 후속
> 분석과 예방 조치를 별도로 공유하겠습니다.

## 승인

Release manager, 서비스 owner, 보안 담당자와 on-call 담당자가 staging 증거,
미해결 위험과 rollback 가능성을 검토한 뒤 서명한다. 한 항목이라도 증거가 없으면
승인으로 간주하지 않는다.
