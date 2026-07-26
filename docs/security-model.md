# 보안 모델

## 1. 보안 목표와 신뢰 경계

우선 보호 대상은 다음과 같다.

1. 고객 간 VM/CT 격리와 전원 제어 권한.
2. PVE API 토큰과 애플리케이션 암호화 키.
3. Cloud-Init 자격 증명과 고객 개인정보.
4. VM 생성, 전원, 할당, IP 변경의 무결성.
5. 감사 기록의 완전성·무결성·추적 가능성.

신뢰 경계:

- 브라우저와 인터넷은 신뢰하지 않는다.
- API는 인증된 사용자도 자신에게 허용된 객체와 동작만 요청한다고 가정하지 않는다.
- Redis/Celery 메시지는 중복·지연·재전송될 수 있으며 메시지 자체를 권한의 근거로 신뢰하지 않는다.
- PVE는 인프라 상태의 기준이지만 응답 지연, 부분 실패, 운영자에 의한 외부 변경이 가능하다.
- DB 운영자 또는 DB 덤프 접근자가 애플리케이션 암호화 키까지 가진다고 가정하지 않는다.

## 2. 인증

### 사용자 로그인

- 비밀번호를 사용할 경우 Argon2id로 해시하고 사용자별 salt와 현재 권고 파라미터를 적용한다.
- access token은 짧은 수명의 서명된 JWT이며 `Authorization: Bearer` 헤더로 전달한다.
- refresh token은 CSPRNG로 생성한 opaque 값이며 DB에는 SHA-256 해시만 저장한다. 원문은 로그·감사·DB에 기록하지 않는다.
- refresh 요청마다 token을 회전한다. 이미 교체되거나 폐기된 token의 재사용이 감지되면 같은 token family와 기존 access token을 모두 무효화한다.
- 명시적 로그아웃, 사용자 비활성화, 비밀번호 변경 시 refresh family를 폐기하고 `session_epoch`를 증가시킨다.
- 브라우저 클라이언트는 refresh token을 JavaScript 또는 local storage에 영속 저장하지 않고, 운영 배포에서는 same-origin BFF의 `HttpOnly`, `Secure`, `SameSite` 쿠키 사용을 권장한다.
- 실패 횟수 기반 사용자/출발지 속도 제한을 적용하되 계정 존재 여부가 드러나지 않는 동일한 오류를 반환한다.

### MFA

- 운영 환경에서 관리자 MFA 등록을 필수로 한다.
- 우선 WebAuthn 또는 TOTP를 지원하고 복구 코드는 일방향 해시로 저장한다.
- MFA 등록/해제와 복구 코드 사용은 재인증 및 감사 대상이다.
- 클러스터 등록, 자격 증명 회전, 사용자 권한 변경, 강제 stop 같은 고위험 작업에 최근 MFA 인증(step-up)을 요구할 수 있다.
- TOTP secret은 MFA 전용 HKDF context로 파생한 AES-GCM 키와 사용자/method AAD로
  암호화하고, 복구 코드는 keyed hash만 저장한다.
- WebAuthn은 고정 RP ID, HTTPS origin과 user verification을 검증한다.
- access token의 session family ID를 매 요청의 활성 refresh family와 대조해 개별
  session revoke를 즉시 반영한다.
- step-up token은 사용자, session epoch와 action에 묶고 다른 action 재사용을
  거부한다.
- `production` 환경의 SUPER_ADMIN/OPERATOR MFA는 비활성화할 수 없다.
- 비밀번호 변경은 현재 비밀번호 재검증을 요구한다. 전체 session 종료를 선택하지
  않아도 현재 family를 제외한 다른 refresh family는 모두 폐기한다.
- 고객 session 조회와 revoke는 token family의 사용자 소유권을 서비스 계층에서
  검사하며 다른 사용자의 UUID는 존재 여부를 숨기는 `404`로 처리한다.
- 고객 알림 설정은 활성 조직 멤버십을 서비스 계층에서 재검사하고 이메일 수신지는
  마스킹한다. 조직의 필수 알림 정책은 고객 opt-out보다 우선한다.
- 알림 enqueue와 실제 전달 사이에 설정·멤버십이 바뀔 수 있으므로 발송 직전 다시
  권한과 유효 설정을 검사한다. 메일 오류에는 주소, 본문, SMTP 응답 원문을 기록하지
  않는다.

### CSRF와 브라우저 보호

- 현재 API의 Bearer header 인증은 브라우저가 자동 첨부하지 않으므로 cookie 기반 CSRF 대상이 아니다. BFF가 refresh cookie를 사용하면 해당 경로에 CSRF token과 `Origin` 검사를 적용한다.
- CORS는 기본 비활성 또는 정확한 same-origin만 허용한다. wildcard와 credential 조합을 금지한다.
- Content Security Policy, `frame-ancestors`, HSTS, `X-Content-Type-Options`, 엄격한 referrer policy를 적용한다.
- Next.js가 사용자/PVE 문자열을 렌더링할 때 HTML 삽입을 피하고 CSP nonce 정책을 사용한다.

## 3. 권한 모델

### 역할

| 기능/API | SUPER_ADMIN | OPERATOR | CUSTOMER |
|---|---:|---:|---:|
| 로그인, refresh, logout, `/auth/me`, 비밀번호 변경 | 허용 | 허용 | 허용 |
| 사용자·조직 목록 조회 | 허용 | 허용 | 금지 |
| 사용자 생성·역할/활성 상태 변경 | 허용 | 금지 | 금지 |
| 조직 생성·구성원 추가·제거 | 허용 | 조회만 허용 | 금지 |
| 클러스터/자격 증명 CRUD와 연결 시험 | 허용 | 허용 | 금지 |
| 전체 노드/VM/CT/스토리지 조회 | 허용 | 허용 | 금지 |
| inventory sync·freshness·reconciliation finding 관리 | 허용 | 허용 | 금지 |
| 워크로드 할당/회수 | 허용 | 허용 | 금지 |
| 템플릿/프로비저닝/IPAM 정책 변경 | 허용 | 금지 | 금지 |
| 자신의 활성 할당 워크로드 조회 | 허용 | 허용 | 허용 |
| 허용된 전원 작업 | 허용 | 허용 | 자신의 활성 할당만 |
| VM/CT 사양 변경과 삭제 | 허용 | 금지 | 금지 |
| 감사 로그 전체 조회/내보내기 | 허용 | 금지 | 금지 |

모든 `/admin` 권한은 API 및 서비스 계층에서 deny-by-default로 검사한다. `OPERATOR`도 PVE 내부 root 권한을 의미하지 않으며 플랫폼 기능별 최소 권한 PVE token을 사용한다.

### 객체 권한

고객 워크로드 접근은 반드시 다음 조건을 포함하는 DB 쿼리로 결정한다. 현재 구현의 소유권 단위는 활성 조직과 조직 멤버십이다.

```text
workload.id = requested_id
AND workload.is_present = true
AND workload.organization_id IS NOT NULL
AND EXISTS organization_member(
  organization_id = workload.organization_id
  AND user_id = current_user.id
)
AND organization.is_active = true
```

- 먼저 객체를 가져온 뒤 애플리케이션에서만 소유자를 비교하는 패턴을 피한다.
- 비소유, 존재하지 않음, 과거 소유는 고객에게 모두 `404`로 응답해 존재 여부를 숨긴다.
- operation 조회도 대상 워크로드의 현재 소유권과 `requested_by`를 모두 확인한다.
- 고객 operation의 요청 당시 조직과 workload의 현재 조직이 다르면 과거 소유로 보고
  목록과 상세에서 모두 제외한다.
- 작업 enqueue 전에 검사하고 워커 실행 직전에도 다시 검사한다. 할당이 회수된 고객 작업은 시작 전 취소한다.
- 이미 PVE에서 실행 중인 작업 중 할당이 회수되면 안전하게 중단할 수 있는 경우 취소하고, 아니면 완료 결과를 관리자에게 노출하되 고객의 후속 접근은 차단한다.
- UUID는 추측 방지 보조 수단일 뿐 권한 통제가 아니다.

### 전원 작업 정책

- 고객 포털 허용 목록은 명시적으로 `start`, `shutdown`, `stop`, `reboot`만 둔다.
- 입력 문자열을 PVE 경로/명령으로 직접 연결하지 않고 enum-to-adapter 매핑을 사용한다.
- 강제 `stop`은 현재 조직 소유 QEMU에만 허용하고, UI 위험 재확인과 API의 `confirm_forced=true`를 모두 요구하며 `FORCED` 감사 기록을 남긴다.
- 관리자 `reset`도 강제 작업으로 표시하며 고객 역할에는 허용하지 않는다.
- 고객 콘솔은 현재 활성 조직에 할당된 실행 중 워크로드에만 허용한다. 설정 변경, migrate, clone, delete, snapshot은 고객에게 노출하지 않는다.
- 고객 설정 변경은 PVE 직접 실행 API로 제공하지 않고 유형별 구조화
  `service_request`로만 접수한다. 고객 생성, 관리자 승인과 실행 직전에 현재
  assignment·조직·quota를 각각 다시 검사한다.
- SSH key endpoint는 공개키 형식만 허용하고 private key 표식, 줄바꿈과 비정상
  encoding을 거부한다. 고객 response에는 내부 조직 UUID를 포함하지 않는다.
- security group은 임의 명령 문자열 대신 direction/action/protocol/CIDR/port
  allowlist schema를 사용하며 다른 조직 정책 참조는 404로 숨긴다.
- vCPU/RAM/disk 변경은 증가만 허용하고 disk 축소를 항상 거부한다. restore는 기존
  VM 덮어쓰기 대상 필드를 제공하지 않는다.
- 재설치와 restore 요청은 typed confirmation, MFA 등록과 action-bound step-up을
  모두 요구한다. 승인 후 취소를 허용하지 않고 실패는 `NEEDS_ATTENTION`으로
  operation·감사와 함께 보존한다.
- 콘솔은 REST 인증 후 발급되는 30초 TTL 일회용 token을 WebSocket subprotocol로 전달한다. PVE 단기 콘솔 ticket은 RFB 인증을 위해 `no-store` 응답으로만 전달하며 브라우저 메모리 밖에 보존하지 않는다. access token, PVE ticket과 PVE endpoint를 URL에 넣지 않는다.
- WebSocket 연결 시 허용 Origin, 역할, 사용자 활성/session epoch, workload 실행 상태를 다시 검사한다. 고객 역할은 조직 활성 상태, 멤버십과 현재 할당도 재검증한다. 사용자·workload 조합별 한 연결과 최대 세션 시간을 강제한다.
- 대상의 현재 상태를 PVE에서 확인하고 무의미하거나 위험한 전이는 `409`로 거부한다.
- 마지막 관측이 stale 기준을 넘은 고객 워크로드의 전원 작업은 서버에서
  `503 INVENTORY_STALE`로 거부한다. 고객 응답에는 관측 시각과 stale 여부만
  제공하고 cluster, node, sync generation, finding 상세는 노출하지 않는다.

## 4. PVE 자격 증명 보호

### 저장 형식

PVE API token은 일반적으로 token ID와 secret으로 나뉜다.

- token ID/realm/user 식별 부분은 연결에 필요한 비민감 메타데이터로 저장할 수 있다.
- secret은 AEAD(AES-256-GCM 또는 XChaCha20-Poly1305)로 암호화한다.
- 각 레코드마다 새 nonce를 사용하고 `cluster_id`, `credential_id`, `key_version`을 associated data로 묶어 레코드 치환을 막는다.
- 저장 필드는 `ciphertext`, `nonce`, `key_version`, `created_at`, `last_used_at`, `rotated_at`로 구성한다.
- 복호화 실패는 fail closed하고 상세 암호 오류를 외부에 반환하지 않는다.

### 키 관리와 회전

- 마스터 키는 DB, 소스 저장소, Docker 이미지, 일반 `.env` 예제에 넣지 않는다.
- 운영에서는 KMS/Vault/Docker secret 같은 별도 비밀 경로에서 런타임에 주입한다.
- 키 버전을 저장하여 새 키로 쓰고 이전 키로 읽는 단계적 회전을 지원한다.
- 회전은 새 자격 증명 연결 시험 → 활성 전환 → 이전 토큰 폐기 순서로 수행해 중단을 줄인다.
- 애플리케이션 메모리에서 복호화한 secret의 생존 시간을 최소화하고 예외/디버그 로그에 객체 전체를 출력하지 않는다.
- 백업에는 암호문만 포함하며 키 백업과 DB 백업의 접근 주체/위치를 분리한다.

### PVE 최소 권한

- 가능하면 클러스터별 전용 API token을 사용한다.
- 인벤토리 전용과 변경 작업 전용 토큰 분리를 후속 강화안으로 둔다.
- PVE ACL은 필요한 path와 권한(`VM.Audit`, 필요한 전원/clone/config 권한 등)만 부여한다.
- token 권한 분리를 활성화하고 root 사용자 토큰 사용을 금지한다.
- 등록 시 기능별 권한 probe 결과를 보여주되 더 넓은 권한을 자동 요구하지 않는다.

### PBS 백업 권한

- PVE Master는 초기 버전에서 PBS credential을 직접 저장하거나 PBS API를 호출하지 않는다.
- 각 PVE cluster에는 별도 PBS API token과 전용 namespace를 사용한다.
- PBS token은 특정 datastore/namespace의 `DatastoreBackup` 최소 권한만 부여한다.
- 백업 client token에 삭제/prune 권한을 부여하지 않고 retention은 PBS prune job에서 실행한다.
- 복구 API는 SUPER_ADMIN에게만 허용하고 성공한 snapshot volume ID를 서버에서 선택한다.
- target node와 VMID는 API와 worker에서 다시 검증하며 기존 VMID 덮어쓰기와 `force`는 금지한다.
- PVE token에는 복구에 필요한 VM allocate와 대상 storage allocate 권한만 추가하고 PBS 삭제 권한은 부여하지 않는다.
- 백업 제출 timeout이나 결과 불명확 오류는 자동 재제출하지 않는다. 기존 UPID와 storage
  content를 먼저 확인하여 중복 백업을 방지한다.
- 고객 역할은 백업 목록과 실행 API에 접근할 수 없다. 조직 재할당 이전 snapshot에는 과거
  고객 데이터가 포함될 수 있으므로 고객 백업 접근은 별도 보안 설계 전까지 금지한다.

## 5. 네트워크 및 SSRF 방어

클러스터 endpoint는 관리자 입력이라도 SSRF 입력으로 취급한다.

- `https`만 허용하고 userinfo, fragment, 비정상 포트, URL 리디렉션을 거부한다.
- 배포 정책의 명시적 PVE 관리망 CIDR/hostname allowlist에 속하는 대상만 허용한다.
- DNS 이름은 해석된 모든 IP를 검사하고 연결 직전 다시 검증하여 DNS rebinding을 줄인다.
- loopback, link-local, multicast, cloud metadata, Unix socket과 정책 외 사설망을 거부한다.
- HTTP client가 프록시 환경 변수를 자동 신뢰하지 않도록 설정한다.
- 연결·응답 timeout, 응답 크기 제한, 클러스터별 동시성/속도 제한을 적용한다.
- TLS hostname과 체인을 검증한다. 사설 CA는 관리자 승인으로 별도 trust bundle에 등록한다.
- 운영에서는 PVE 관리망으로의 egress만 허용하는 네트워크 정책을 마지막 방어선으로 둔다.

## 6. 비동기 작업 보안

- Celery 메시지에는 비밀을 넣지 않고 내부 UUID와 correlation metadata만 넣는다.
- 워커는 operation을 DB에서 읽어 상태, actor, 대상, 현재 할당을 다시 검증한다.
- 큐 이름과 task 이름을 allowlist로 고정하고 pickle serializer를 금지하며 JSON 같은 안전한 serializer만 사용한다.
- Redis에는 인증, TLS/격리망, 최소 ACL, 외부 노출 금지를 적용한다.
- 작업 인자/결과의 기본 영속화를 최소화하고 만료 시간을 둔다. 업무 결과는 DB에 저장한다.
- `acks_late`와 재시도를 사용하더라도 DB 상태 전이와 멱등성 제약이 중복 실행을 막아야 한다.
- 클러스터/워크로드 단위 잠금은 TTL Redis 락만 신뢰하지 않고 DB advisory/row lock과 상태 compare-and-set을 사용한다.
- inventory 큐에는 `sync_runs.id`만 전달하고 PVE endpoint, token, 원시 응답은 넣지
  않는다. 전체 응답이 완전한 경우에만 누락 tombstone을 적용하며, 외부 삭제가
  감지되어도 조직 할당과 IP를 자동 해제하지 않는다.
- PVE UPID는 로그 주입을 막도록 구조화 필드로 저장하고 UI에서는 필요한 범위만 표시한다.

## 7. Cloud-Init과 고객 데이터

- SSH 공개키 기반 로그인을 기본으로 한다.
- 관리 UI의 새 키 생성 기능은 브라우저 Web Crypto에서 RSA 3072 키 쌍을 만들고 개인키를 PEM 파일로 즉시 한 번만 다운로드한다. 개인키는 서버 API로 전송하거나 브라우저 저장소, DB, Redis, Celery payload, 로그, 감사 로그에 저장하지 않으며 공개키와 fingerprint만 화면에 남긴다. 키 생성은 HTTPS 또는 localhost의 secure context에서만 제공한다.
- Cloud-Init user-data/network-data는 허용된 구조화 필드에서 서버가 생성한다. 임의 스크립트 user-data는 초기 버전에서 허용하지 않는다.
- 평문 비밀번호, private SSH key, API key를 DB/감사/로그/Celery 결과에 저장하지 않는다.
- 초기 비밀번호가 필요하면 CSPRNG로 생성하고 강한 해시 또는 외부 secret store만 보존하며 사용자에게 한 번만 전달한다.
- 템플릿에는 고정 호스트 키, machine-id, 이전 cloud-init instance data가 남지 않도록 템플릿 준비 체크리스트를 둔다.
- VM 이름, 설명, SSH key comment 등 PVE로 전달되는 문자열은 길이와 문자 집합을 제한한다.

## 8. 감사 로깅

Notification channel 구성은 PVE credential/MFA와 분리된 HKDF context의 AES-GCM으로
암호화한다. Webhook은 HTTPS, public 목적지 allowlist, DNS 재검증, redirect/proxy
금지 정책을 통과해야 하며 delivery UUID를 멱등 key로 사용한다. 고객 payload와 API
조회에는 현재 조직 범위의 일반화된 사건만 포함하고 cluster/node/UPID/endpoint를
노출하지 않는다.

### 감사 대상

- 로그인 성공/실패, 로그아웃, MFA 등록/해제/실패.
- 사용자 생성/변경/비활성화, 역할 변경, 세션 폐기.
- 클러스터 등록/변경/삭제, 연결 시험, 자격 증명 회전.
- 워크로드 할당/회수, 전원 명령, 생성/복제/취소/삭제.
- 템플릿과 IP 풀/예약/할당 변경.
- 권한 거부, 위험 정책 변경, 감사 로그 조회/내보내기.
- 비동기 작업의 접수와 최종 성공/실패. 중간 단계는 operation/pve_task에 연결한다.

### 필드

- `occurred_at`, `event_type`, `action`, `outcome`, `severity`.
- `actor_user_id`, 당시 역할 snapshot, 세션 식별 해시.
- `target_type`, `target_id`, `cluster_id`, `workload_id`.
- `request_id`, `trace_id`, `operation_id`, `pve_task_id`.
- 정규화/축약된 source IP와 user agent.
- 비민감 변경 전/후 값 또는 변경 필드 목록, 실패 분류.

### 무결성과 개인정보

- 애플리케이션 DB 사용자는 감사 행 `INSERT`만 가능하고 `UPDATE/DELETE`를 금지한다.
- 일자별 파티션, 제한된 보존 작업 역할, 외부 WORM/SIEM 전송을 권장한다.
- 행별 `prev_hash`/`entry_hash` 또는 외부 서명 checkpoint로 삭제·변조 탐지를 강화한다.
- 토큰, 쿠키, 비밀번호, 전체 authorization header, private key, 전체 Cloud-Init payload는 절대 기록하지 않는다.
- 사용자에게 보여주는 감사 정보에서도 IP/사용자 agent 등 개인정보 접근을 최소화한다.
- 감사 기록 실패 시 고위험 변경은 fail closed한다. 조회 같은 비변경 작업은 경보를 발생시키고 정책에 따라 제한적으로 계속할 수 있다.

## 9. 주요 위협과 통제

| 위협 | 공격 예 | 예방/탐지 통제 |
|---|---|---|
| 수평 권한 상승 | 고객 A가 고객 B의 workload UUID 요청 | 소유권 포함 쿼리, 404, 워커 재검증, 권한 매트릭스 테스트 |
| 수직 권한 상승 | 고객이 `/admin` API 직접 호출 | 서버 역할 가드, deny-by-default, 역할 변경 감사 |
| 자격 증명 탈취 | DB 덤프/로그에서 PVE secret 획득 | AEAD, 키 분리, redaction, 최소 권한/회전 |
| SSRF | 클러스터 URL로 metadata 접근 | endpoint allowlist, DNS/IP 재검증, redirect/proxy 금지, egress ACL |
| 작업 재전송 | 같은 clone 요청 반복 | `Idempotency-Key`, 유일 제약, operation 상태 머신, 결과 조회 |
| TOCTOU | 권한 확인 후 할당 회수 | 실행 직전 소유권 재검사, 상태 CAS, DB lock |
| 상태 혼동 | stale 캐시를 실행 중으로 표시 | `observed_at`, stale 표시, 변경 전 PVE 재조회 |
| 명령/데이터 주입 | VM 이름이 PVE 명령/로그를 오염 | typed adapter, allowlist, 구조화 로그, 길이 제한 |
| IP 탈취/충돌 | 병렬 생성이 같은 주소 선택 | DB row lock/unique active allocation, PVE 검증 |
| 감사 회피 | 실패 작업만 로그 누락 | 접수/결과 양쪽 기록, append-only, 외부 전송/경보 |
| 공급망 공격 | 악성 Python/npm 의존성 | lockfile/hash, 최소 의존성, SBOM, 이미지 서명/스캔 |

## 10. 운영 보안 기준

- 모든 서비스 간 트래픽은 격리망을 사용하고 외부에는 reverse proxy만 노출한다.
- DB와 Redis는 공개 포트를 열지 않으며 서로 다른 자격 증명과 최소 권한 계정을 사용한다.
- API/worker 컨테이너는 non-root, 최소 Linux capability, read-only filesystem, 제한된 secret mount를 사용한다.
- 개발/시험/운영 PVE 토큰과 DB를 분리하고 운영 데이터를 테스트 fixture로 복사하지 않는다.
- 의존성과 base image를 고정하고 정기적으로 취약점 스캔/패치한다.
- 암호화 키, PVE 토큰, 관리자 세션, 백업 복구에 대한 사고 대응 및 회전 런북을 유지한다.
- 백업은 암호화하고 복구 훈련을 수행한다. 복구된 감사 로그의 hash/checkpoint도 검증한다.
- 기본적으로 민감 오류 상세를 클라이언트에 숨기고 request ID로 운영 로그와 연결한다.
- 자동 백업 정책은 PVE에 등록된 PBS storage만 참조하며 PBS credential, encryption
  key와 prune 권한을 저장하지 않는다.
- policy 변경·skip과 restore drill에는 최근 MFA 기반 step-up을 요구하고, schedule
  dispatch는 생성자가 여전히 활성 SUPER_ADMIN인지 다시 확인한다.
- 고객 backup/snapshot API는 과거 소유권 경계 검증이 별도 승인되기 전까지 deny한다.
- 고객 VM 상세, 작업 이력과 metric은 활성 멤버십·현재 조직·미회수 assignment를
  매 요청에 재검사한다. metric에는 수집 당시 조직 snapshot과 assignment 시작 시각을
  함께 적용해 재할당 전 값을 차단한다.
- 고객 작업 실패 문구는 허용된 일반화 메시지만 반환하고 PVE 응답, UPID,
  cluster/node, endpoint를 노출하지 않는다.
- 플랫폼 역할과 조직 역할을 분리한다. 조직 역할은 명시적 permission allowlist만
  부여하며 고객 token으로 `/admin` API를 호출할 수 없다.
- 조직 멤버십의 활성 상태와 만료는 요청과 비동기 실행 시점에 모두 검사한다.
  마지막 `ORG_OWNER`의 강등·제거는 transaction 안에서 거부한다.
- 조직 초대 원문은 생성 응답에서 한 번만 노출하고 DB에는 hash만 저장한다. 본인
  이메일, 만료, 폐기와 replay를 모두 검사한다.
- quota 검증과 예약은 조직 row lock을 획득한 동일 transaction에서 수행하며,
  현재 사용량과 모든 활성 예약을 함께 계산해 병렬 초과 예약을 막는다.

## 11. 출시 전 보안 완료 조건

- 역할 × API × 객체 소유권 권한 테스트가 자동화되어 모두 통과한다.
- 다른 고객, 과거 할당, 임의 UUID, operation ID를 이용한 IDOR 테스트가 통과한다.
- DB dump, 애플리케이션 로그, Celery payload에서 PVE secret과 Cloud-Init 비밀이 발견되지 않는다.
- TLS/SSRF 정책을 우회하는 URL, DNS rebinding, redirect 시험이 거부된다.
- 관리자 MFA, 세션 폐기, CSRF, 로그인 rate limit이 검증된다.
- 감사 로그가 모든 고위험 성공/실패 작업을 포함하고 일반 앱 계정으로 수정/삭제할 수 없다.
- 실제 staging PVE token이 필요한 기능만 수행할 수 있고 불필요한 PVE 권한은 거부된다.
