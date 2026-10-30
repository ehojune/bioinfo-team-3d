## 단계
#1076 ① Windows pool 9분 초과: 끊긴 순간의 시험을 남기는 진단(상한은 바꾸지 않음). 진단안·승인: [#1076 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1076#issuecomment-6520450345).

## 한 일
1. `.github/workflows/test.yml`: pool 단계 명령에 `--ci-stall`을 더하고 `pytest-windows` job env에 `CI_STALL_INTERVAL_S: '150'`을 둔다. 150·300·450초에 worker마다 `parallel/stacks-gwN.log`에 현재 시험 nodeid와 모든 스레드 stack, 끝나지 않은 asyncio task stack이 남고, 마지막 snapshot이 상한 540초의 90초 전이다. 산출물은 기존 `if: always()` upload로 끊겨도 7일 보관된다.
2. `scripts/ci_suite.py`: `stall_interval()`이 `CI_STALL_INTERVAL_S`(1 이상 숫자)를 읽고 아니면 300으로 돌아간다. `WorkerStall`이 이 간격을 쓴다. Linux 3.10 단계는 환경변수가 없어 기존 300초 그대로다.
3. `tests/test_ci_windows10m.py`: workflow 계약(pool만 `--ci-stall`, 간격 × 3 < 540초, always upload), 간격 해석, snapshot이 현재 시험을 적고 `.log`가 phase 검사 glob(`collection-*.json`·`metrics-*.json`·`*error*.json`)에 걸리지 않는다는 것을 시험으로 더했다.

## 확인한 것 (PR 본문 미확인 3가지 중)
- `windows.combine`·`check`는 `collection-*.json`·`metrics-*.json`·`*error*.json`만 읽는다(`scripts/ci_windows_phases.py` 99~105·232행). `stacks-gwN.log`는 걸리지 않는다. 첫 CI에서 한 번 더 확인한다.

## 아직 확인하지 못한 것
- 450초 snapshot이 실제로 상한 전에 artifact에 남는지(상한에 닿는 run이 나와야 확인).
- Windows에서 `gc.get_objects()` 순회와 스레드의 비용(첫 CI의 pool 단계 소요를 기존 중앙 6.8분과 비교).

## 테스트 결과
Windows `test_ci_windows10m`·`test_ci_windows_phases`·`test_ci_boundary_preview`·`test_ci_skip_policy`·`test_ci_cohort823` 569 passed.
