# 단계 10 — 제한된 고객 Self-service (P2)

## 목표

고객에게 데이터 손실이나 인프라 격리 위험이 낮고 정책으로 제한 가능한 변경 기능을
제공한다.

## 1차 기능

- SSH 공개키 추가·교체·삭제
- hostname과 허용된 설명 변경
- rDNS 요청 또는 외부 DNS/IPAM 연동
- 승인된 firewall/security group 적용
- backup 실행 또는 restore 요청
- vCPU/RAM/disk 증설 요청
- 재설치 요청과 영향 확인

## 정책

- 직접 실행과 승인 요청을 기능별로 구분한다.
- organization/product quota와 허용 범위를 서버에서 검사한다.
- disk 축소와 기존 VM 덮어쓰기 복구를 금지한다.
- 공개키 private material은 서버로 전송하지 않는다.
- firewall 입력은 구조화된 schema와 allowlist를 사용한다.
- 재설치는 typed confirmation, step-up MFA와 별도 operation을 요구한다.

## 데이터 모델

- `service_requests`: type, requester, organization, workload, input snapshot, status
- `approval_steps`: approver role, decision, reason, decided_at
- `ssh_public_keys`: public key, fingerprint, label, owner, revoked_at
- `security_groups`와 workload assignment
- quota 모델은 단계 11과 공유

## API와 UI

- 고객 요청 생성·취소·상태 조회
- 관리자 승인·거부·수정 승인
- 변경 전후 영향 preview
- SSH key와 security group 관리
- 재설치·복구·증설의 진행 상태

## 테스트

- quota 초과와 승인 우회
- 다른 조직 key/security group 참조
- 위험 입력과 injection
- 승인 후 소유권 변경
- 중복 요청과 멱등성
- 재설치 취소 경계와 실패 복구

## 완료 조건

- 허용 범위 밖 변경이 UI 우회로도 실행되지 않는다.
- 승인, 실행, 결과가 operation과 audit에 연결된다.
- 실패한 고객 요청이 안전한 복구 또는 관리자 처리 상태로 남는다.


---

[전체 작업 인덱스로 돌아가기](README.md)
