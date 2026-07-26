# 단계 12 설계 결정

- 상태: 구현 완료
- 기준일: 2026-07-26

## 독립 기능과 공통 안전 경계

Snapshot, migration, HA, node maintenance, bulk, guest config와 Firewall/SDN을
각각 독립 flag로 분리했고 기본값은 비활성이다. 모든 실행 기능은 동일한 preview
계약을 사용하되 feature별 option allowlist와 상태 전이를 분리한다.

preview는 대상 존재·종류·node·전원 상태, 진행 중 operation, local disk,
passthrough, HA/replication 호환 확인, downtime·backup·고객 알림 확인을 검사한다.
접수 시 대상 snapshot과 모든 workload의 활성 잠금을 transaction으로 만든다.

## 실행과 결과 불명확 처리

worker는 관리자 활성 권한과 각 대상의 node/version을 다시 확인한다. PVE UPID는
기존 task polling과 operation heartbeat에 연결한다. 제출 timeout은 같은 요청을
자동 재전송하지 않고 `NEEDS_ATTENTION`으로 종료한다. 고급 작업의 retry는 과거
snapshot을 재사용하지 않고 새 preview부터 시작해야 한다.

HA는 requested/observed state를 분리하고 fencing/quorum 경고를 표시한다.
Firewall/SDN은 PVE 구성을 allowlist 필드로 읽기만 하며 변경 API는 제공하지 않는다.
Guest config는 CPU, RAM, bridge, VLAN, boot order와 제한된 Cloud-Init 필드만 허용하고
passthrough와 임의 argument는 제외한다.
