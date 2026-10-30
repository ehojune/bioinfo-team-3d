## 단계
#1117: `tests/test_state.py`의 고정 횟수 대기를 시간 기준 대기로. 근거: [#1076 계측 실측](https://github.com/ehojune/bioinfo-team-3d/issues/1076#issuecomment-6530142714).

## 한 일
- `wait_until(predicate, timeout=10)`(async)와 `wait_until_sync`(TestClient 시험용)를 더하고, 이 파일의 `for _ in range(N): ... sleep(0.01)` 대기 12곳을 모두 바꿨다(async 9, sync 2, resume 한 곳은 두 조건). 조건이 서면 바로 나오고 한도를 넘으면 False를 돌려주므로 뒤의 단언이 그대로 실패를 알린다.
- 대상 시험 `test_completed_direct_result_finishes_without_original_agent`는 `[False]`·`[True]` 모두 같은 대기를 쓴다. 단언은 하나도 바꾸지 않았다.
- helper 시험 한 건(조건이 늦게 서도 기다림, 한도 초과는 False, 이미 참이면 즉시).

## 확인
- `tests/test_state.py` 61 passed.
- Windows inventory: `config/ci/windows-boundary-v1.json`에 `tests/test_state.py`는 `known_tests` 목록에만 있고 sha256 고정(`mixed_nodes`)이 없어 수정하지 않았다.

## 하지 않은 것
- 실제 느린 Windows 러너에서의 재현은 하지 못했다(CI에서 확인). 이 PC에서는 시험이 통과하는 것과 helper의 동작만 확인했다.
- 같은 부류의 다른 시험 파일(`test_state_*.py` 등)의 고정 횟수 대기는 이번 범위가 아니다.
