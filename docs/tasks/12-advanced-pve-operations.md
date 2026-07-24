# 단계 12 — 관리자 고급 PVE 운영 (P2)

이 단계는 각각 독립 feature flag와 별도 상태 머신으로 나누어 출시한다.

## 12.1 VM Snapshot

- snapshot 생성·목록·삭제·rollback
- memory 포함 여부와 storage 지원 여부 검증
- rollback은 step-up MFA와 typed confirmation 적용
- backup snapshot과 VM snapshot을 UI에서 명확히 구분
- 진행 중 backup, migration, config operation과 충돌 방지

## 12.2 Live/Offline Migration

- 대상 node/storage/network 호환성 사전 검사
- online/offline 선택과 downtime 경고
- local disk, passthrough, HA, replication 제약 표시
- UPID 추적과 결과 불명확 시 source/target 재조정
- 고객에게 예정 downtime 알림

## 12.3 HA 정책

- HA group/resource 조회와 제한된 변경
- requested/observed state 구분
- fencing과 quorum 위험 경고
- 일반 OPERATOR와 HA 관리자 권한 분리 검토

## 12.4 Node Maintenance와 Drain

- maintenance window 생성
- node의 workload 영향 분석
- migration 가능/불가 분류
- backup과 고객 알림 확인 후 drain 실행
- 자동 배치 제외와 복귀 절차

## 12.5 일괄 작업

- 필터로 대상을 선택하되 실행 전 immutable target snapshot 생성
- 전원, backup, assignment, tag 등 안전한 작업부터 허용
- 동시성, rate limit, 부분 성공, 재시도 제공
- 고객 영향과 대상 수를 typed confirmation에 표시

## 12.6 상세 VM/CT 구성

- disk/NIC/bridge/VLAN/boot order/Cloud-Init 구조화 편집
- QEMU와 LXC capability 차이 표시
- hotplug 가능 여부와 재부팅 필요 여부 preview
- passthrough와 임의 PVE argument는 초기 범위에서 제외

## 12.7 Firewall과 SDN

- 기존 PVE firewall/SDN 구성을 먼저 read-only로 투영
- cluster/node/workload scope를 명확히 구분
- rule validation, ordering, lockout 방지 preview
- 변경 전 export와 rollback 계획
- PVE 권한을 별도 token으로 분리

## 공통 테스트

- PVE capability 차이
- 지원하지 않는 storage/network/guest 종류
- operation 충돌
- timeout과 결과 불명확
- 권한·TLS·PVE API 오류
- 실행 중 소유권/대상 상태 변경

## 완료 조건

- 각 기능은 독립적으로 비활성화할 수 있다.
- 위험 작업은 preview, step-up MFA, 감사, operation 추적을 갖춘다.
- 실패가 다른 클러스터나 operation queue를 고갈시키지 않는다.


---

[전체 작업 인덱스로 돌아가기](README.md)
