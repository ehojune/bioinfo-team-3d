## 단계
#674 후속: runner 쪽 producer(환경 단계) 단수 `environment_binding` 사전 검사.

## 한 일
1. 확인: 소비 단계(복수 `environment_bindings`)는 실행 전에 검사했지만, 환경 단계 자신의 단수 `environment_binding`은 실행 전 검사가 없었다(완료 시점 manifest 대조는 #1025). 재현됨.
2. `Runner._producer_binding_refusal`: 계약 형식, binding runner ID == 이 runner, session_runner 일치, runtime == 이 runner의 실제 runtime, `step_id` 일치를 엔진 시작 전에 확인하고 어긋나면 `$0` 거부한다. `_environment_refusal_reason`이 소비 단계 검사 앞에서 호출한다.

## 테스트 결과
- Windows: `test_runner_env_inputs_674.py`·`test_observed_outputs.py` 91 passed. WSL Ubuntu-22.04: 95 passed.
- 기존 producer test는 binding의 runner ID·step ID를 실제 값으로 맞췄다(이전에는 불일치 상태로 통과하고 있었음).
- 미검증: 게이트웨이를 거친 전체 dispatch, 실제 엔진 쓰기 보호. manual은 이번 범위에 없다.

## 바꾼 파일
labhq/runner/daemon.py, tests/test_runner_env_inputs_674.py, 패치노트·이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
