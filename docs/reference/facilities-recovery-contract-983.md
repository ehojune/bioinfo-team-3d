# 시설팀 복구 상태 계약 (#983)

기준 `da3eff9f`. 이 문서는 후속 구현 제안이며 실제 복구를 증명하지 않는다. 명시적 문의와 자동 진단은 별개다.

| 경계 | 현재 구현·저장 | 공백 및 표시 계약 |
|---|---|---|
| 요청 대상·담당 | `cso.answer_ask`는 ask의 `to`와 저장된 `routed_to`를 구분한다. 부재 시 CSO 답변에 `requested_to`, `fallback_reason`과 대체 안내를 붙인다. `ask` store에 답변을 남긴다. | 응답자·route·요청 대상을 각각 표시한다. CSO 답변은 시설팀 진단·복구가 아니다. |
| 자동 진단 | `Hub.request_facilities_diagnosis`는 request/turn별 ask ID를 저장한다. 시설팀 offline이면 CSO에 넘기지 않고 거부 기록을 남긴다. 가용하면 read-only consult 한 번을 기다린다. | 답변은 advice이며 원 단계 실패 결과는 그대로다. 답변만으로 수정이나 재개를 주장하지 않는다. |
| 거부 | `answer_ask`의 hard stop은 PI로 라우팅하며 read-only 정책 거부는 `rejected`. 자동 진단은 취소·offline·예산 또는 login/quota hold에서 중단된다. | 거부·timeout·login·quota·정책을 성공과 구분하고 사유를 보존한다. 자동 진단 거부 사유는 일부 합쳐져 있어 세부 분류는 후속 과제다. |
| 승인·수정 | `request_facilities_fix`는 allowlist proposal, approval ID와 decision을 request의 `facilities_fixes[step]`에 저장한다. `record_facilities_fix`는 execution과 `request.facilities_fix` event를 기록한다. 거절/timeout은 재실행하지 않는다. | 승인과 실행은 별도 상태다. package `applied`는 task-local 지시이며 실행 성공이 아니다. `succeeded`는 fix 기록이지 원 단계 성공을 보장하지 않는다. |
| 원 단계 재개 | `run_step`은 승인된 fix의 applied/성공 뒤 `facilities_rerun`으로 같은 단계 한 번을 dispatch하고 결과를 반환한다. `TaskResult.facilities_fix`와 step 결과는 별개다. | 재개 시도, 재개 결과, 원 단계 성공을 분리한다. 다른 오류로 재시도가 실패하면 package fix는 `applied`에 남는다. |
| readiness | `doctor`의 facilities support는 설정·실행 파일·preflight·login 증거에 한정된다 (#991). | ready는 실응답·수정·원 단계 성공이 아니다. 실제 설치판 복구는 UNVERIFIED. |

근거: `labhq/orchestrator/cso.py:3438,3500,3538,3590,3988,4341`, `labhq/gateway/server.py:4374,4464,4542`, `labhq/models.py:423`, `labhq/web/state.js:1065`, `labhq/runner/daemon.py:1975`. 승인된 package와 cache fix는 실행 방식이 다르다. 저장된 ask·approval·request·task 결과는 독립 기록이며 통합 복구 상태 필드는 없다.

## 최소 후속안

`labhq/gateway/server.py`에서 request/step 단위의 읽기 전용 projection을 만들고 `labhq/web/state.js`와 `labhq/web/ui/tasks.js`가 소비한다. 기존 ask·approval·fix·step 결과를 각각 참조하고 `requested_to`, `routed_to`, `answer_status/reason`, `approval_status/id`, `fix_status`, `rerun_status`, `step_status`를 별도로 노출한다. 없는 증거는 `unknown`으로 표시한다. 이 필드명은 제안이며 저장 schema 확정이 아니다. 기존 결과만으로 재시도 시작 여부가 확실하지 않으면 `unknown`; 별도 durable rerun task ID와 완료 기록이 필요할 때는 `cso.py`와 gateway 저장 경계를 추가하는 별도 구현 배정을 받는다. crash/restart 중 정확히 한 번 실행을 추정하지 않는다.

Gate: fallback 및 자동 offline 차이, 답변/거부, 승인/timeout, fix applied/failed/succeeded, rerun 실패/성공, login/quota/policy, 재시작과 기존 record 호환을 mock으로 검증한다. 불명 상태를 성공으로 승격하지 않는다. 실제 설치판 진단·승인된 수정·원 단계 복구는 별도 승인과 실증이 필요하다.
