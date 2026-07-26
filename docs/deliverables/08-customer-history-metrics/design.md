# 단계 8 설계 결정

- 상태: 구현 완료
- 기준일: 2026-07-26

## 소유권과 이력 경계

고객 VM, 작업과 metric 조회는 활성 사용자뿐 아니라 현재 조직 멤버십, 활성 조직,
workload의 현재 조직, 미회수 assignment를 함께 검사한다. 작업은 현재 사용자가
요청했고 현재 assignment 시작 이후 생성된 전원 operation만 보인다. metric은
수집 당시 조직 snapshot과 assignment 시작 시각을 모두 적용한다. 재할당 전 이력은
새 소유자에게 승계하지 않는다.

고객 응답은 이름, 전원 상태, 사양, IP, uptime, 관측 시각, 일반화된 상태 변화와
작업 결과만 제공한다. cluster, node, VMID, UPID, endpoint, 조직 UUID와 PVE 오류
원문은 서버 response schema에서 제외한다.

## Metric 수집과 보존

inventory의 현재 QEMU와 미회수 assignment를 수집 대상으로 삼고 PVE RRD endpoint를
기존 TLS 검증·timeout·목적지 allowlist가 적용된 client로 호출한다. 1분 bucket은
복합 유일 키로 중복을 막고 값이 없는 지표는 NULL로 보존한다.

- 1분: 24시간
- 5분 가중 rollup: 30일
- 1시간 가중 rollup: 365일

5분과 1시간 rollup은 최신 원본에도 겹쳐 생성해 장기 그래프의 최신 구간이 비지 않게
하고, 각 보존 기한은 오래된 저해상도 행을 삭제하는 기준으로만 사용한다.

고객 API의 day/month/year 범위는 각각 위 해상도를 선택하며 최대 10000개 점을
반환한다. 예상 점의 80% 미만이면 `partial`로 표시한다.

## 고객 화면

VM 이름은 새로고침 가능한 `/customer/vms/{id}` 상세 route로 연결된다. 상세에는
사양, IP, uptime, 관측 최신성, CPU/RAM/disk/network SVG 시계열, 최근 작업·상태
변화, 최근 백업 상태와 예정 유지보수를 표시한다. 희소 지표는 선을 연결하지 않고
누락 상태를 설명한다. 진행 작업이 끝나면 전역 알림을 표시하고 목록과 열린 상세를
다시 조회한다.
