# 단계 13 — 상용 호스팅 기능 착수 결정

## 문서 상태

- 상태: 정책상 보류
- 기준일: 2026-07-26
- 근거: `docs/deliverables/00-product-policy/product-policy.md`

## 결정

현재 PVE Master는 **과금 없는 멀티테넌트 인프라 운영 포털**로 확정되어 있다.
사업 모델을 상용 호스팅으로 전환한다는 별도 승인이 없으므로 주문, 과금, 결제,
지원 ticket, 고객 public API와 외부 상용 시스템 연동은 구현하지 않는다.

이 결정은 단계 13을 누락한 것이 아니라, `docs/tasks/13-commercial-hosting.md`의
“내부 운영 도구로 유지하면 진행하지 않는다”는 선택 조건과 단계 0 제품 정책을
적용한 결과다. 결제 상태를 기존 workload 수명주기나 조직 소유권에 연결하는 변경도
금지한다.

## 보류 범위

- 상품 주문, 변경, 해지 상태 머신
- 사용량 측정, 청구, invoice, payment와 세금 정보
- trial, credit와 결제 기반 suspension
- 고객 지원 ticket과 incident communication
- 고객 public API와 scoped API token
- CRM, billing, DNS, 외부 IPAM/CMDB 연동

보류 기능을 위한 테이블, API, worker, 메뉴와 feature flag도 만들지 않는다. 비활성
코드라도 결제 데이터와 PVE 운영 데이터 사이에 아직 승인되지 않은 결합 계약을
고정할 수 있기 때문이다.

## 향후 착수 게이트

Release E를 시작하려면 구현 전에 다음 결정이 승인되어야 한다.

1. 사업 주체, 통화, 가격·세금·환불·연체·credit 정책
2. 주문, 구독, 사용량, invoice, payment, provisioning의 독립 상태 머신과 보상 흐름
3. suspension이 VM 정지·삭제와 자동 결합되지 않는 유예 및 수동 승인 정책
4. 결제 데이터 저장소와 PVE credential 저장소의 접근·암호화·운영자 권한 경계
5. 고객 API token의 scope, 만료, rotation, revoke와 사용 이력 보존 정책
6. 외부 webhook의 서명 알고리즘, timestamp 허용 범위, replay 방지와 멱등 event ID
7. PCI DSS 적용 범위 최소화와 결제수단 비저장 원칙
8. 개인정보·세금 문서 보존, 삭제, export와 감사 정책
9. 외부 CRM/billing/DNS/IPAM/CMDB별 timeout, retry, circuit breaker와 reconciliation
10. 상용 기능 장애가 기존 VM 운영·조직 소유권에 전파되지 않는 격리 시험

승인 시 `PLAN.md`, 제품 정책, 보안 모델, API 설계, 데이터 모델과 위협 모델을 먼저
개정하고 구현을 별도 단계로 재개한다.

## 검증

- 제품 정책의 현재 제외 범위와 Release E 조건을 재확인했다.
- 애플리케이션, 데이터베이스와 외부 연동에 상용 기능 변경을 만들지 않았다.
- 기존 VM 운영과 고객 소유권 경계에 결제 상태가 추가되지 않았음을 확인했다.

