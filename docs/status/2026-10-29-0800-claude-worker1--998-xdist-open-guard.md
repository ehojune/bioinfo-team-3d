## 단계
CI 간헐 실패 추적 — Closes #998.

## 원인 (실제 로그)
- 실패 run 40776469480, `pytest (3.12)`, job 123127070462. traceback 끝: `scripts/ci_suite.py` `pytest_runtest_logreport` → `StageDiagnostics.record` → `flush` → `Path.write_text` → `Path.open` → `tests/test_request_input_proof_959.py:105`의 `lambda: pytest.fail('guarded input opened')` → `Failed`.
- `test_restricted_and_private_inputs_not_opened`가 프로세스 전체의 `Path.open`을 `pytest.fail`로 바꿔 둔 call 단계가 끝날 때, 진단 flush(5초 간격)가 우연히 겹치면 `Failed`(OSError가 아니라 `except (OSError, ValueError)`에 안 잡힘)가 hook 밖으로 나가 xdist가 INTERNALERROR로 worker를 죽입니다. 테스트 소요가 5초 간격 경계에 걸릴 때만 나타나 재실행하면 통과했습니다.
- 제품 코드 결함이 아닙니다. 테스트 guard와 CI 진단 도구의 상호작용입니다.

## 한 일
- `scripts/ci_suite.py`: 진단 flush가 import 시점에 묶어 둔 `io.open`으로 씁니다(`Path.write_text`/`Path.open`을 쓰지 않음). `Path.open`·`builtins.open`을 패치하는 다른 test(web_shutdown_process, windows_encoding_contract 등)가 진단 기록을 조용히 끊는 경로도 함께 닫힙니다.
- `tests/test_request_input_proof_959.py`: guard를 보호 폴더 경로에만 `pytest.fail`로 걸고 나머지는 원래 `Path.open`으로 넘깁니다. 검사 의도는 그대로입니다.
- 회귀 test `test_the_diagnostic_write_survives_a_test_that_patches_path_open_to_fail`(interval=0, 시계 고정): 수정 전 코드에서 `Failed: Path.write_text used`로 실패, 수정 후 통과를 로컬에서 확인했습니다.

## 테스트 결과
- `tests/test_ci_*.py` 5개 파일과 `test_request_input_proof_959.py` 통과, `ci_selection.validate_profile()` 통과.
- 미검증: 실제 CI의 xdist 환경에서 간격 경계를 다시 만들지는 않았습니다. 같은 부류(전역 `Path.open` 패치)의 다른 test가 `pytest.fail`을 쓰는지 grep했고 `Path.open`을 `pytest.fail`로 막는 곳은 이 한 곳뿐입니다. `Path.read_text`를 `pytest.fail`로 막는 test(knowledge_recall, step_memory_limit)는 flush가 `read_text`를 쓰지 않아 영향이 없습니다.

## 바꾼 파일
scripts/ci_suite.py, tests/test_request_input_proof_959.py, tests/test_ci_windows_phases.py와 변경 기록.

## 막힌 점
없음. #898·#844 범위는 건드리지 않았습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
