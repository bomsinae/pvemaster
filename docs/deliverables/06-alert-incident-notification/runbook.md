# 단계 6 운영 runbook — Alert와 Notification

- 상태: 운영 인수 가능
- 기준일: 2026-07-26

## 배포

1. migration `20260726_0019`를 적용한다.
2. Webhook 목적지의 정확한 public hostname 또는 CIDR만
   `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS/NETWORKS`에 넣는다.
3. 이메일을 사용하면 TLS SMTP host, port, username/password, from address를 secret
   manager에서 backend와 maintenance worker에 주입한다.
4. P0/CRITICAL system rule에 최소 두 개의 독립 channel을 등록하고 test delivery가
   DELIVERED인지 확인한다.
5. maintenance worker heartbeat와 `control_plane_state_check` 최근 성공을 확인한다.

## 대응

- OPEN: 영향을 확인하고 담당자를 assign한다.
- ACKNOWLEDGED: 조사 중임을 표시하되 원인 관측과 escalation은 계속된다.
- SILENCED: 승인된 maintenance에만 사용하고 종료 시간을 반드시 둔다.
- RESOLVED: 근거 note를 남긴다. 원인이 남아 있으면 자동 REOPEN된다.
- delivery RETRY/FAILED: `last_error_code`와 channel 상태를 확인한다. 저장된 secret이나
  전체 payload를 로그에 출력하지 않는다.

## 안전 점검

- Webhook redirect는 허용하지 않는다.
- DNS/allowlist 오류를 우회하기 위해 CIDR을 광범위하게 열지 않는다.
- 고객 경보 조회에서 organization scope가 없는 system alert는 노출되지 않아야 한다.
- planned maintenance window가 실제 장애를 가리는지 종료 직후 OPEN 경보를 확인한다.
