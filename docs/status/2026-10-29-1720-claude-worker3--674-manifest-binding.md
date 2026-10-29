## 단계
#674 후속 2: manifest 내용과 binding 대조(runner 쪽, 환경 단계 완료 시점).

## 한 일
1. `labhq/runner/step_inputs.py`: `producer_environment_problem` 추가. 수집된 `runner-manifest.json`의 hash·내용을 binding과 정확히 대조하고 interpreter 존재까지 확인한다(`validate_runner_env` 재사용).
2. `labhq/runner/daemon.py`: `environment_binding`이 붙은 단계가 성공으로 끝나기 전에 위 검사를 호출한다. 어긋나면 `ok=False`, `incomplete: runner environment does not match the pinned binding: ...`.
3. 이전에는 소비 단계 시작 시점(#1021)에만 대조했다. 이제 환경 단계 자체가 실패한다.

## 테스트 결과
- Windows: 새 test 8개(`test_runner_env_inputs_674.py` 끝), 관련 test 포함 80 passed. WSL Ubuntu-22.04: 같은 test와 관련 test 84 passed.
- 범위 밖이라 하지 않은 것: manual 한·영 반영(claim 범위에 manual 없음), 게이트웨이를 거친 전체 dispatch end-to-end.

## 바꾼 파일
labhq/runner/daemon.py, labhq/runner/step_inputs.py, tests/test_runner_env_inputs_674.py(새 test 8개 추가; Windows inventory 등록 검사를 피하려 새 파일 대신 기존 파일에 둠), 패치노트·이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
