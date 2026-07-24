# 단계 13 — 상용 호스팅 기능 (P3, 선택)

내부 운영 도구로 유지하면 이 단계는 진행하지 않는다.

## 기능 후보

- 상품 주문, 변경, 해지
- 사용량 측정과 청구
- invoice, payment 상태와 세금 정보
- trial, credit, suspension 정책
- 지원 ticket과 incident communication
- 고객 API token과 scoped API
- 외부 CRM, billing, IPAM, DNS, CMDB 연동

## 설계 원칙

- 결제와 인프라 operation을 하나의 transaction으로 가정하지 않는다.
- 주문/청구/프로비저닝은 각자 상태 머신과 보상 흐름을 가진다.
- suspension이 즉시 VM 삭제로 이어지지 않게 한다.
- 고객 API token은 scope, 만료, rotation, revoke, 사용 이력을 제공한다.
- 외부 webhook은 서명 검증, replay 방지, 멱등 event ID를 사용한다.

## 완료 조건

- 상용 기능 장애가 기존 VM 운영과 고객 소유권을 훼손하지 않는다.
- 결제 데이터와 PVE credential의 저장·접근 경계가 분리된다.


---

[전체 작업 인덱스로 돌아가기](README.md)
