# 단계 8 — 고객 작업 이력, VM 상세와 성능 지표 (P1)

## 목표

고객 포털을 단순 VM 목록에서 자기 VM의 상태와 작업을 이해할 수 있는 운영 화면으로
확장한다.

## 데이터와 API

- 현재 소유권과 요청자 조건을 강제하는 고객 operation 목록·상세
- VM별 최근 작업과 상태 변화 이력
- 안전하게 집계된 CPU, RAM, disk, network 시계열
- uptime, observed_at, stale, 최근 backup 상태
- metric 보존·downsampling 정책
- 서버 페이지네이션, 기간, 상태, VM 필터

## 고객 UI

- VM 상세 route
- 상태, 사양, IP, uptime, 마지막 확인
- CPU/RAM/disk/network 그래프
- 최근 전원 작업과 결과·실패 안내
- backup 상태와 예정 maintenance
- 작업 완료 전역 알림과 새로고침 후 복구
- 빈 상태, stale 상태, 부분 metric 누락 상태

## 보안과 개인정보

- 현재 조직 멤버십과 현재 할당을 모든 조회에서 재검사한다.
- 과거 소유 기간의 metric과 operation을 새 소유자에게 노출하지 않는다.
- cluster, node, UPID, PVE endpoint와 내부 오류 원문은 숨긴다.
- metric query에 조직/워크로드 범위와 기간 제한을 적용한다.

## 테스트

- 과거 소유 metric/operation 접근
- 조직에서 제거된 직후 접근
- metric partial/missing/stale
- 긴 기간 query 제한
- 새로고침 후 진행 작업 복구
- 반응형 차트와 키보드 접근성

## 완료 조건

- 고객이 별도 문의 없이 VM 상태와 최근 작업 결과를 이해할 수 있다.
- 모든 고객 이력과 metric이 현재 소유권 경계를 지킨다.


---

[전체 작업 인덱스로 돌아가기](README.md)
