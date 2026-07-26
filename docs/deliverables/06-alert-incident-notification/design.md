# 단계 6 설계 결정 — 경보, Incident와 Notification

- 상태: 구현 완료
- 기준일: 2026-07-26

## 지속 경보

5분 scheduler의 control-plane 평가가 worker/queue, cluster 연결·자격증명,
inventory freshness, scheduler, operation 수동 검토, backup 실패, IP pool 부족,
최근 고객 VM 상태 변화를 계산한다. `type + resource_type + resource_id`의 SHA-256
fingerprint로 하나의 `alerts` 행을 유지하며 관측마다 `alert_events`에 OPEN, REPEAT,
REOPEN, SUPPRESSED, AUTO_RESOLVE를 남긴다.

관리자는 optimistic `version`으로 acknowledge, assign, silence, resolve를 실행한다.
원인이 계속되면 수동 resolve 뒤 다음 평가에서 REOPEN된다. 고객 조회는 현재 조직
membership에 포함된 `organization_id` 경보만 반환하고 system/다른 고객 경보는
404 또는 목록 제외로 처리한다.

## Maintenance

시간 범위와 `ALL` 또는 resource type/ID 대상의 maintenance window를 저장한다.
활성 window의 `suppress_notifications`가 켜져 있으면 경보는 SILENCED로 기록하고
delivery를 만들지 않는다. window 종료 후 원인이 계속되면 OPEN 상태로 복귀한다.

## 전달

channel 구성 전체를 notification 전용 HKDF/AES-GCM key context와 channel ID AAD로
암호화한다. API 응답에는 configured 여부만 있고 URL, email, signing secret은 없다.

- Webhook: HTTPS만, userinfo/fragment/redirect/proxy를 금지하고 DNS의 모든 주소를
  public allowlist로 재검증한다. delivery UUID를 `Idempotency-Key`로 사용하고 선택적
  HMAC-SHA256 서명을 보낸다.
- Email: TLS SMTP와 서버 환경 secret만 사용하며 recipient만 channel 암호문에 둔다.
- 실패: 안전한 오류 코드만 저장하고 지수 backoff, 최대 시도 후 FAILED로 전환한다.
- quiet hours: UTC 시간 범위에서 non-critical delivery를 억제한다.
- escalation: 설정 시간이 지난 REPEAT event만 다시 전달한다.

고객 payload는 alert/workload/organization UUID와 일반화된 message만 포함하고
cluster, node, PVE UPID, endpoint와 credential을 포함하지 않는다.
