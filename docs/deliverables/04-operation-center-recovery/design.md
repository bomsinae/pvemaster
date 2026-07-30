# Operation 센터와 복구 흐름 설계 결정

## 문서 상태

- 상태: 구현 완료
- 기준일: 2026-07-24
- 범위: `docs/tasks/04-operation-center-recovery.md`

## 통합 작업 투영

관리자 Operation 센터는 기존 `operations`, `pve_tasks`, `backup_runs`,
`restore_runs`와 `provisioning_requests`, `provisioning_steps`를 새 작업 테이블로
복제하지 않고 하나의 읽기 모델로 합친다. 전원·관리·백업·복구 작업은 operation
UUID, 프로비저닝은 provisioning request UUID를 작업 ID로 사용한다.

`operation_events`와 `operation_assignments`는 두 리소스 중 정확히 하나만 참조하는
XOR 제약을 갖는다. 이벤트에는 상태, 단계, 안전한 메시지, 오류 코드 같은 제한된
metadata만 저장한다. PVE UPID는 관리자 응답에서도 원문 대신 SHA-256 앞 12자리로
만든 `upid:<reference>`만 표시한다.

## 상태와 복구 정책

- 취소는 worker가 시작하지 않은 `QUEUED` 작업에만 허용한다.
- 재시도는 원본 상태를 되돌리지 않고 새 operation/request를 생성하며
  `retry_of_id`로 한 번만 연결한다.
- 전원 작업은 `retryable=true`인 `FAILED|TIMEOUT`만 재시도한다.
- 프로비저닝은 clone 미제출, IP 미예약 상태의 `FAILED`만 재시도한다.
- 제출 timeout, worker 재개 시 UPID가 없는 reboot/reset 등 결과가 불명확한 작업은
  `NEEDS_ATTENTION`으로 보내 자동 재제출하지 않는다.
- backup/restore의 외부 호환 응답 상태는 기존 `TIMEOUT|FAILED`를 유지하지만 연결된
  operation은 불명확한 제출에 `NEEDS_ATTENTION`을 사용한다.
- 담당자 지정, 확인, 수동 해결은 operation `version`을 비교하고 갱신해 동시 변경을
  `409 OPERATION_VERSION_CONFLICT`로 거부한다.

## 권한 경계

관리자 목록·상세·복구 명령은 서비스 계층에서 `SUPER_ADMIN|OPERATOR`를 요구한다.
삭제·복구·사양 변경과 프로비저닝 재시도는 `SUPER_ADMIN`만 가능하다. worker는 실행
직전에 요청자 활성 상태와 역할, 고객 조직 멤버십과 현재 workload 소유권을 다시
검사한다.

고객 작업 목록과 상세는 다음 조건을 SQL에 함께 적용한다.

- 현재 사용자가 직접 요청함
- 고객 허용 전원 작업임
- operation의 조직과 workload의 현재 조직이 같음
- 현재 사용자가 그 활성 조직의 멤버임

조건 실패는 존재 여부를 숨기기 위해 모두 404로 처리한다.

## MFA 의존 기능

대량 export는 단계 5의 step-up MFA와 만료 다운로드 URL이 준비되기 전에는 endpoint를
노출하지 않는다. 동기 export나 MFA 없는 우회 경로를 만들지 않는 것을 단계 4의
fail-closed 경계로 삼는다.
