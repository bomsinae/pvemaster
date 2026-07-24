# PVE Master 제품 정책과 운영 기준

## 1. 문서 상태

- 상태: 확정된 초기 운영 기준
- 기준일: 2026-07-24
- 적용 범위: Release A부터 Release C까지
- 우선 문서: `PLAN.md`, `docs/architecture.md`, `docs/security-model.md`,
  `docs/api-design.md`

이 산출물은 `docs/tasks/00-product-policy.md`의 단계 0 결과다. 기존 설계에서 정한
권한과 보안 경계를 구체적인 제품 정책과 수치 기준으로 고정한다. 이 문서의 수치
기준은 최초 운영 기본값이며 부하 시험, 복구 훈련, 실제 PVE 관측 결과를 근거로 더
엄격하게 조정할 수 있다. 보안이나 고객 격리 수준을 낮추는 변경은 별도 설계 검토와
승인이 필요하다.

## 2. 제품 경계

PVE Master의 현재 제품 경계는 **한 운영 조직이 여러 고객 조직의 PVE 자원을
관리하는 멀티테넌트 인프라 운영 포털**이다.

포함:

- 여러 PVE 클러스터의 통합 인벤토리와 상태 관측
- 관리자 중심의 VM/CT 수명주기, IPAM, 프로비저닝, 백업·복구
- 조직 단위 workload 소유권과 고객 간 격리
- 고객의 자기 QEMU VM 조회, 전원 작업, 콘솔
- 운영 작업, 경보, 감사, 보안 통제
- 제한된 고객 셀프서비스와 조직별 승인·quota의 후속 확장

현재 제외:

- 주문, 가격, 결제, invoice, 세금과 정산
- SLA credit 자동 계산
- 고객 지원 ticket 시스템
- reseller와 다단계 tenant
- 외부 고객용 public API와 API token

제외 항목은 `docs/tasks/13-commercial-hosting.md`의 Release E 범위다. 사업 모델이
상용 호스팅으로 확정되기 전에는 기존 운영·소유권 모델에 결제 상태를 결합하지 않는다.

## 3. 결정 등록부

| 번호 | 결정 | 초기 운영 정책 | 후속 확장 |
|---|---|---|---|
| 1 | 고객 workload 종류 | QEMU만 제공 | LXC는 별도 보안·터미널 UX 검증 후 결정 |
| 2 | 고객 백업 권한 | 목록·실행·복구 모두 금지 | Release B/C에서 안전한 조회·승인 요청 검토 |
| 3 | 조직 관리자 | 플랫폼 역할을 추가하지 않음 | organization membership 역할로 확장 |
| 4 | 고객 사양 변경 | 직접 변경 금지 | 승인 기반 service request로 제공 |
| 5 | 고객 성능 지표 | 1분·5분·1시간 downsample 정책 | 저장 비용과 실제 사용량에 따라 조정 |
| 6 | 알림 채널 | 운영자 Slack·이메일, 고객 이메일 | 고객 Slack/Webhook은 opt-in 후속 기능 |
| 7 | 백업 보존·prune | PBS가 기준 시스템 | PVE Master는 정책 참조와 관측만 수행 |
| 8 | 제품 형태 | 과금 없는 멀티테넌트 운영 포털 | 상용 기능은 Release E에서 별도 승인 |
| 9 | SLO/RPO/RTO | 이 문서의 초기 수치 적용 | 분기별 검토와 훈련 결과로 강화 |
| 10 | 데이터 보존 | 데이터 종류별 최소 보존 적용 | 법무·개인정보 정책 확정 시 조정 |

## 4. 역할과 객체 소유권 정책

### 4.1 플랫폼 역할

- `SUPER_ADMIN`: 플랫폼 보안·정책·자격 증명·복구를 포함한 전체 관리
- `OPERATOR`: 일상 운영과 허용된 PVE 작업, 정책·보안 변경은 금지
- `CUSTOMER`: 활성 조직을 통해 현재 할당된 자기 QEMU VM만 사용

`ADMIN`은 설명용 상위 개념이며 실제 권한 판정은 `SUPER_ADMIN`과 `OPERATOR`를
구분한다. 권한은 frontend 메뉴가 아니라 API와 서비스 계층에서 deny-by-default로
검사한다.

### 4.2 기능별 권한 매트릭스

| 기능 | SUPER_ADMIN | OPERATOR | CUSTOMER |
|---|---:|---:|---:|
| 사용자·조직 목록 조회 | 허용 | 허용 | 금지 |
| 사용자 생성·역할·활성 상태 변경 | 허용 | 금지 | 금지 |
| 조직 생성·구성원 변경 | 허용 | 조회만 | 금지 |
| 클러스터 등록·자격 증명 변경 | 허용 | 허용 | 금지 |
| 클러스터 연결 시험·인벤토리 조회 | 허용 | 허용 | 금지 |
| 인벤토리 sync·reconciliation 처리 | 허용 | 허용 | 금지 |
| 전체 VM/CT 조회 | 허용 | 허용 | 금지 |
| workload 조직 할당·회수 | 허용 | 허용 | 금지 |
| 관리자 전원 작업 | 허용 | 허용 | 금지 |
| QEMU/CT 사양 변경·삭제 | 허용 | 금지 | 금지 |
| 템플릿·상품·프로비저닝 정책 | 허용 | 조회만 | 금지 |
| VM 프로비저닝 실행 | 허용 | 정책에 따라 허용 | 금지 |
| IPAM 정책과 주소 관리 | 허용 | 조회만 | 금지 |
| PBS 대상 등록·정책 변경 | 허용 | 조회만 | 금지 |
| 수동 백업 | 허용 | 허용 | 금지 |
| 새 VMID 복구 | 허용 | 금지 | 금지 |
| operation 처리·재시도 | 허용 | 허용된 작업만 | 자기 작업 조회만 |
| 전체 감사 로그·export | 허용 | 금지 | 금지 |
| 자기 QEMU VM 목록·상세 | 허용 | 허용 | 현재 조직 소유만 |
| 자기 QEMU 전원 작업 | 허용 | 허용 | `start`, `shutdown`, `stop`, `reboot` |
| 자기 QEMU 콘솔 | 허용 | 허용 | 현재 조직 소유·실행 중 VM만 |
| 자기 비밀번호·MFA·session | 허용 | 허용 | 자기 계정만 |

정책 변경, credential 회전, restore, 강제 작업, 감사 export에는 단계 5 구현 후 최근
MFA 인증을 요구한다.

### 4.3 고객 객체 소유권

고객 workload 접근의 기준은 사용자가 아닌 조직이다. 다음 조건을 하나의 DB query
또는 동등한 원자적 정책 검사로 강제한다.

```text
user.is_active = true
AND user.role = CUSTOMER
AND organization.is_active = true
AND current organization membership exists
AND workload has a current active assignment to that organization
AND workload.is_present = true
AND workload.kind = QEMU
```

추가 규칙:

- 비소유, 과거 소유, 존재하지 않는 UUID는 모두 고객에게 `404`로 반환한다.
- operation은 요청자와 대상 workload의 현재 소유권을 모두 만족해야 조회할 수 있다.
- API 접수 시점과 worker 실행 직전에 권한과 소유권을 각각 검사한다.
- 할당 회수나 조직 비활성화 직후 고객 목록·상세·작업·콘솔 접근을 차단한다.
- workload UUID는 권한 통제 수단으로 간주하지 않는다.

## 5. QEMU와 LXC 고객 제공 정책

### 5.1 초기 정책

- 고객 포털에는 QEMU VM만 노출한다.
- 고객 QEMU는 상태·사양·할당 IP·관측 시각·허용 action을 제공한다.
- 실행 중인 고객 QEMU에 noVNC 콘솔을 제공한다.
- 고객 전원 action은 `start`, `shutdown`, `stop`, `reboot`로 제한한다.
- `stop`은 명시적 위험 확인과 서버의 `confirm_forced=true`가 필요하다.
- 고객에게 reset, delete, migrate, snapshot, clone, config 변경을 제공하지 않는다.

관리자는 QEMU와 LXC를 모두 관리한다. LXC에는 QEMU 전용 reset/noVNC를 제공하지 않고
허용된 전원 작업과 xterm.js terminal만 제공한다.

### 5.2 LXC 고객 제공 보류 조건

다음 조건을 모두 충족하기 전에는 고객 LXC를 활성화하지 않는다.

1. 고객 LXC 소유권·전원·terminal IDOR 테스트가 QEMU와 동등하게 통과한다.
2. termproxy ticket과 PVE 사용자 식별자가 브라우저에 노출되지 않는다.
3. container 특유의 host 영향과 privileged/unprivileged 정보를 안전하게 추상화한다.
4. LXC 상품, quota, backup, restore 정책이 결정되어 있다.
5. 고객 UI가 QEMU와 다른 capability를 명확히 표시한다.

## 6. 고객 노출 정보 정책

### 6.1 고객에게 제공

- PVE Master workload UUID
- 안전하게 정규화한 VM 이름
- 현재 전원 상태와 stale 여부
- vCPU, RAM, disk 할당 사양
- 현재 할당된 IP 주소
- 안전한 조직 이름
- 마지막 관측 시각
- 현재 허용된 action
- 자신이 요청한 작업의 안전한 상태, 오류 분류와 재시도 가능 여부
- 정책 적용 후 CPU, RAM, disk, network 집계 지표
- 예정된 maintenance와 허용된 알림 상태

### 6.2 고객에게 숨김

- PVE cluster UUID, 이름, endpoint와 관리망 주소
- PVE node 이름과 내부 storage ID
- PVE VMID가 업무상 필요하지 않은 경우의 원본 식별자
- PVE API/PBS token, ticket, credential metadata
- PVE UPID와 원본 task log
- 다른 조직·사용자·할당·작업·백업 정보
- 내부 queue, worker, trace와 상세 오류
- 사설 CA와 네트워크 allowlist
- 과거 소유 기간의 activity, metric, snapshot

고객 오류는 안전한 오류 코드, 사용자가 취할 수 있는 조치와 request ID만 제공한다.

## 7. 고객 변경과 승인 정책

고객은 초기 운영 범위에서 인프라 사양을 직접 변경하지 않는다. Release C에서 다음
항목을 승인 기반 `service_request`로 제공한다.

- SSH 공개키 변경
- hostname 또는 허용된 설명 변경
- rDNS 요청
- 승인된 security group 적용
- backup 또는 restore 요청
- vCPU, RAM, disk 증설 요청
- 재설치 요청

정책:

- 변경 요청은 현재 소유권, product 제한, organization quota를 검사한다.
- disk 축소와 기존 VM 덮어쓰기 복구는 금지한다.
- 재설치·restore·강제 작업은 typed confirmation과 step-up MFA를 요구한다.
- 관리자가 승인한 시점과 worker 실행 시점에 소유권·quota·대상 상태를 다시 검사한다.
- 외부 주문/결제 연동은 Release E 전까지 승인 근거로 사용하지 않는다.

## 8. 성능 지표 보존과 해상도

고객 성능 지표의 초기 정책은 다음과 같다.

| 기간 | 해상도 | 용도 |
|---|---:|---|
| 최근 24시간 | 1분 | 장애 확인과 단기 추세 |
| 최근 30일 | 5분 | 일·주 단위 사용 패턴 |
| 최근 365일 | 1시간 | 장기 추세와 용량 검토 |

규칙:

- 원본 PVE RRD가 더 낮은 해상도나 짧은 기간만 제공하면 값을 보간하지 않는다.
- 누락·미지원 값은 0이 아니라 `null`과 명시적 상태로 제공한다.
- downsampling은 최대·평균 값을 구분하며 peak를 평균으로 숨기지 않는다.
- 현재 조직에 할당된 기간의 지표만 고객에게 제공한다.
- 할당 변경 시 과거 조직과 새 조직 사이에 metric이 섞이지 않도록 assignment 시각으로
  범위를 자른다.
- PVE 내부 node/cluster label은 고객 metric series에서 제거한다.
- 365일을 초과한 고객 metric은 집계 삭제하며 감사·operation 이력과 혼합하지 않는다.

## 9. 알림 정책

### 9.1 초기 채널

- 플랫폼 운영자: Prometheus/Alertmanager에서 Slack과 운영 이메일로 전달
- 보안 책임자: 보안 이메일과 지정된 incident 채널
- 고객: 검증된 계정 이메일
- 일반 고객 Webhook과 Slack: Release C 이후 opt-in 기능

PagerDuty 같은 on-call 연동은 배포 환경의 Alertmanager 설정으로 추가할 수 있다.

### 9.2 이벤트와 기본 수신자

| 이벤트 | 심각도 | 기본 수신자 | 고객 노출 |
|---|---|---|---|
| cluster 연결 실패·critical stale | 높음 | 운영자 | 영향 VM이 있으면 안전한 장애 공지 |
| worker down·scheduler 실패 | 높음 | 운영자 | 없음 |
| queue backlog·stuck operation | 중간/높음 | 운영자 | 자기 작업 지연만 |
| provisioning 반복 실패 | 중간 | 운영자 | 자기 요청이 있을 때만 |
| IP pool 부족 | 중간 | 운영자 | 없음 |
| backup 누락·실패 | 중간/높음 | 운영자 | 고객 백업 기능 활성화 후 |
| restore 실패 | 높음 | SUPER_ADMIN·운영자 | 요청 고객에게 안전한 실패 상태 |
| credential 권한·만료 문제 | 높음 | SUPER_ADMIN·보안 책임자 | 없음 |
| 관리자 MFA·session 위험 이벤트 | 높음 | 대상 사용자·보안 책임자 | 자기 계정만 |
| 고객 전원 작업 완료·실패 | 정보/중간 | 요청 고객 | 허용 |
| 예정 maintenance | 정보 | 운영자·영향 고객 | 허용 |
| VM 장기 비가용 | 중간 | 운영자·영향 고객 | 내부 원인을 제거해 허용 |

기본 규칙:

- 동일 fingerprint의 반복 이벤트는 하나의 alert로 집계한다.
- 고객에게는 cluster/node/queue/UPID와 내부 오류 원문을 보내지 않는다.
- maintenance window 중에도 보안 사고와 데이터 손실 위험 경보는 억제하지 않는다.
- 알림 실패는 원래 operation을 실패로 바꾸지 않지만 별도 delivery 경보를 만든다.

## 10. 백업·복구와 보존 정책

### 10.1 책임 경계

- PVE Master는 PVE에 등록된 PBS storage를 참조한다.
- PBS credential과 encryption key는 PVE/PBS가 관리한다.
- snapshot retention과 prune의 기준 시스템은 PBS prune job이다.
- PVE Master는 PBS snapshot을 직접 삭제하거나 prune 권한을 보유하지 않는다.
- PVE Master는 backup policy, 최근 성공, 다음 예정, 관측된 snapshot metadata를 관리한다.

### 10.2 권한

- `SUPER_ADMIN`: target 정책, 수동 backup, 전체 내역, 새 VMID restore
- `OPERATOR`: 수동 backup과 내역 조회
- `CUSTOMER`: Release A에서는 backup API와 화면 모두 금지
- 기존 VMID 덮어쓰기와 원본 VM 자동 교체는 모든 역할에 금지

### 10.3 복구

- 성공하고 서버가 확인한 snapshot만 복구 원본으로 사용한다.
- 복구는 같은 cluster의 비어 있는 새 VMID에만 수행한다.
- restore 완료 후 자동으로 고객에게 할당하거나 전원을 켜지 않는다.
- inventory sync 후 NIC, IP, hostname, 조직 할당을 검토한다.
- 결과 불명확 오류는 자동 재제출하지 않고 PVE task와 snapshot 존재 여부를 먼저
  재조정한다.

### 10.4 보존

- 실제 workload snapshot 보존 기간은 PBS 정책이 결정한다.
- PVE Master의 `backup_runs`와 `restore_runs`는 operation 보존 기간과 함께 365일
  온라인 보존을 초기 기본값으로 한다.
- snapshot이 PBS에서 prune되어도 실행·감사 이력은 보존하고 snapshot 가용 상태만
  갱신한다.
- 분기마다 표본 복구 훈련을 수행하고 결과를 기록한다.

고객 백업 조회는 과거 소유자의 데이터 노출 방지 설계와 assignment 시각 기반 snapshot
경계가 구현된 이후에만 활성화한다.

## 11. SLI와 SLO

### 11.1 서비스 SLO

| SLI | 초기 목표 |
|---|---:|
| API 월간 가용성 | 99.9% |
| DB 기반 목록·상세 API latency | 월간 p95 500ms 이하 |
| 상태 변경 요청 접수 latency | 월간 p95 2초 이하 |
| 인증 성공 요청 latency | 월간 p95 1초 이하 |
| inventory 자동 sync 정상 시작 | 예정 시각에서 60초 이내 99% |
| 정상 cluster inventory freshness | 마지막 성공 관측 180초 이내 99% |
| operation queue 시작 지연 | p95 30초 이하 |
| 경보 최초 전달 | 조건 충족 후 5분 이내 99% |

PVE 자체 응답 시간과 장기 backup/restore 완료 시간은 API 접수 latency에서 분리한다.
다만 PVE 지연과 실패율은 별도 SLI로 관측한다.

### 11.2 Stale 기준

- sync 기본 주기: 60초
- warning stale: 마지막 성공 관측 후 180초 초과
- critical stale: 마지막 성공 관측 후 600초 초과
- 부분 sync 실패: 누락 workload를 absent로 판정하지 않음
- 모든 상태 변경: cached 상태와 관계없이 PVE preflight 수행
- critical stale 또는 PVE preflight 실패: 상태 변경을 `503` 또는 안전한 오류로 거부

### 11.3 Operation timeout 기본값

| 작업 | 기본 제한 |
|---|---:|
| PVE connect | 5초 |
| 일반 PVE read | 15초 |
| 전원 작업 | 5분 |
| 프로비저닝 | 30분 |
| backup | 60분 |
| restore | 60분 |
| console 연결 | 15초 |
| console 최대 session | 60분 |

작업 종류별 timeout은 PVE task polling 총 제한이며 HTTP 요청 하나를 장시간 열어두지
않는다. timeout은 실패와 결과 불명확 상태를 구분하며 생성·backup·restore 제출을
무조건 재시도하지 않는다.

## 12. RPO와 RTO

### 12.1 PVE Master 제어 평면

- PostgreSQL 목표 RPO: 1시간
- PostgreSQL 목표 RTO: 4시간
- 감사 WORM 전송 목표 지연: 24시간 이하
- Redis RPO: 적용하지 않음. Redis 유실이 업무 데이터 유실로 이어지면 안 됨
- 운영 복구 훈련: 최소 분기 1회

DB backup은 암호화하고 애플리케이션 암호화 키와 분리 보관한다. 복구 시 미완료
operation과 PVE 실제 상태를 재조정한 뒤 queue를 개방한다.

### 12.2 고객 workload

- Release A의 수동 backup에는 보장된 workload RPO를 선언하지 않는다.
- 자동 backup 정책이 활성화된 Release B 기본 목표 RPO: 24시간
- 성공 snapshot이 있는 workload 복구 목표 RTO: 8시간
- 고객별 계약 SLA는 이 기본 목표와 분리하며 Release E 전에는 자동 credit을 제공하지
  않는다.

실제 RPO/RTO는 분기 복구 훈련에서 측정하고 목표 미달 원인과 개선 작업을 기록한다.

## 13. 데이터 보존과 개인정보

| 데이터 | 온라인 보존 | 후속 처리 |
|---|---:|---|
| 감사 로그 | 365일 | 외부 WORM/SIEM으로 archive |
| operation·PVE task | 365일 | 큰 raw result는 30일 이내 축약/삭제 |
| backup·restore 실행 이력 | 365일 | snapshot 가용성과 분리 |
| 고객 활동 이력 | 365일 | 고객에게 자기 현재 소유 범위만 제공 |
| 로그인·보안 활동 | 90일 | 보안 사건 연결분은 감사 정책 적용 |
| 고객 성능 1분 지표 | 24시간 | 5분으로 downsample |
| 고객 성능 5분 지표 | 30일 | 1시간으로 downsample |
| 고객 성능 1시간 지표 | 365일 | 만료 후 삭제 |
| sync run 상세 | 30일 | 월별 집계는 365일 |
| 완료 outbox·scheduler run | 7일 | 실패·incident 연결분은 더 오래 보존 |
| refresh session | 만료·폐기 후 30일 이내 | token 원문은 보존하지 않음 |

개인정보 원칙:

- 필요한 최소 IP와 user agent만 정규화·축약해 저장한다.
- token, cookie, password, private key, 전체 authorization header를 저장하지 않는다.
- 고객 활동과 metric은 현재 조직 소유권과 assignment 기간으로 제한한다.
- 사용자 삭제는 감사·소유권 참조를 보존하도록 비활성화와 가명화를 우선한다.
- 보존 작업과 export도 감사 대상이다.

## 14. Release 범위 확정

### P1 — 정식 서비스 초기 필수

- 경보, incident, Slack·이메일 notification
- PBS 자동 backup 정책과 복구 검증
- 고객 operation 이력, VM 상세, 성능 지표
- 고객 MFA, session과 이메일 알림

### P2 — 핵심 운영 안정화 후

- 승인 기반 고객 self-service
- 조직 membership 역할, 초대, quota와 승인
- snapshot, migration, HA, node maintenance, 일괄 작업
- 상세 VM/CT 구성과 제한된 firewall/SDN
- 고객 backup 조회·요청은 별도 데이터 격리 검증 후 선택
- 고객 LXC는 별도 capability·보안 검증 후 선택

### P3 — 사업 모델 승인 후

- 주문, 과금, invoice, 결제와 suspension
- 지원 ticket
- 고객 public API와 scoped API token
- CRM, billing, DNS, 외부 IPAM/CMDB 연동
- reseller와 다단계 tenant

## 15. 변경 관리

다음 정책 변경은 `PLAN.md`, 보안 모델, API 설계와 테스트 매트릭스를 함께 갱신해야
한다.

- 고객에게 새 workload 종류 또는 PVE 내부 정보를 노출
- 고객 action allowlist 확대
- 고객 backup/snapshot 접근 허용
- 조직 역할이나 객체 소유권 단위 변경
- 직접 실행 가능한 고객 self-service 확대
- backup/prune 책임 시스템 변경
- RPO/RTO 또는 데이터 보존 기간 완화
- 상용 결제 상태를 workload 수명주기와 연결

고객 격리, TLS 검증, MFA, 감사, 비밀정보 비노출 원칙은 제품 정책 변경으로 완화할 수
없다.
