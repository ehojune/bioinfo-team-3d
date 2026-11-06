## 단계
#1272 rev4: resizable 시험의 시작 시간 측정. 원인 수정이 아니라 다음 실패와 비교할 평소 값을 남기는 단계다.

## 한 일
- `tests/test_web_3d.py`: `resizable_startup_summary`를 더했다. 스크립트의 `resizable timing:` 행(stderr)과 Python이 기록한 launch 시각으로 `node_start_ms`, `module_load_ms`, `before_open_ms`, 남은 시간을 한 줄로 만들고, 시험이 통과하면 `warnings.warn`(`UserWarning`)으로 남겨 pytest의 warnings 요약에 나오게 한다(직렬·xdist 모두 CI 로그에 보임). 행이 없거나 이상해도 그 필드만 빠지고 시험을 실패시키지 않는다.
- 시험 2건: 행에서 값을 읽는 계산, 이상한 입력에서 예외가 없음.
- `docs/reference/ci-1076-flake-tally.md`: 분석 절(9e62f820)과 측정 설명을 더했다.
- `tests/web_resizable_panels.cjs`는 이미 `module-ready`·`open` 행을 stderr에 찍고 있어 바꾸지 않았다.

## 확인
- `tests/test_web_3d.py` 101 passed, 1 skipped(Windows 로컬). 이 PC의 평소 값: `node_start_ms` 18~23, `before_open_ms` 49~122.

## 하지 않은 것
- 시간 한계·단언은 바꾸지 않았다.
- 줄은 warnings 요약에만 나오고 시험 출력에는 나오지 않는다. `test_ci_windows10m`의 축소된 namespace에서는 보고를 건너뛴다.
