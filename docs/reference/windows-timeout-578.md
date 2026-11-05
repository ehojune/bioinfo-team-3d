# #578 Windows timeout 진단

2026-11-05 · rev6 · **hold 유지**. 기존 로그·artifact 목록·CI 소스만 읽었다. 재실행·코드/시험/workflow 수정·HPC 실행은 하지 않았다.

**권고:** 먼저 실패 시 진단 보존 경로를 고친다. pool 상한 확대나 시험 제외를 뒷받침할 근거는 없다. timeout은 step 상한 도달로 확인됐지만 지연 node와 EBUSY의 잠긴 파일은 **UNVERIFIED**다.

## 확인한 증거

- head: `75758303ffa2c784e3ee2395e591bd800cca59e6`.
- [run 42013297410 attempt 1](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42013297410), [Windows job 127197833918](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42013297410/job/127197833918).
- [job API](https://api.github.com/repos/ehojune/bioinfo-team-3d/actions/jobs/127197833918)로 head·step 시각·결과를 대조했다. 아래 시각은 UTC다.

| 순서 | 기존 Windows 로그에서 확인 |
|---|---|
| critical | 10:46:53~10:47:39, **31 passed**. critical-ready도 성공 |
| pool 시작 | 10:47:40, `-q -n 4 --dist loadfile --ci-phase parallel --ci-stall tests` |
| 진행 | 10:56:47에 96%, 11:00:15에 98%, 11:00:29에 99%. 중간 출력 간격 207.96초 |
| 마지막 출력 | 11:00:47.981에 점 27개. 종료 5.03초 전까지 출력이 있었음 |
| 첫 실패 | 11:00:53.008, `timed out after 13 minutes`. API step 경과 793초에는 runner 종료 처리도 포함되며 pytest 순수 실행시간으로 쓰지 않음 |
| 보고 | pool duration step 성공, coverage report step은 **skipped** |
| 두 번째 실패 | 11:00:54.527에 업로드 대상 34개, 11:00:55.011에 ZIP 생성 중 `EBUSY: resource busy or locked, read`. 파일명 없음 |
| 정리 | 11:00:57.758에 orphan process cleanup. 업로드보다 뒤임 |

[artifact API](https://api.github.com/repos/ehojune/bioinfo-team-3d/actions/runs/42013297410/artifacts)에는 Linux artifact 2개만 있다(`13408283351`, `13407414590`). Windows artifact는 없어 worker checkpoint·stack·metrics 내용을 회수하지 못했다. 이전 Windows 다운로드 폴더도 `selection-error.json`만 있어 이번 pool과 비교할 수 없다.

## 원인 판단과 한계

| 판단 | 근거·한계 |
|---|---|
| **확인: step 상한 도달** | [workflow 196~199행](https://github.com/ehojune/bioinfo-team-3d/blob/75758303ffa2c784e3ee2395e591bd800cca59e6/.github/workflows/test.yml#L196)의 PR pool 제한은 13분이다. job 전체 18분 제한보다 먼저 끝났다. 99%는 node·stage·잔여 실행시간을 알려주지 않는다. 진행 중 시험 실패도 summary가 없어 배제하지 못한다 |
| **후보: 누적 실행·lock 대기·특정 node 지연** | [ci_suite 414~438행](https://github.com/ehojune/bioinfo-team-3d/blob/75758303ffa2c784e3ee2395e591bd800cca59e6/scripts/ci_suite.py#L414)은 setup~teardown 전체를 host-resource lock으로 감싸고 최대 1500초 기다린다. 이 대기는 per-test clock과 `limit_notice` 밖이다. `pyproject.toml`의 test 제한 300초로 pool 전체를 보장하지 못한다. 실제 lock 소유자·대기시간·남은 callable은 **UNVERIFIED** |
| **확인: timeout 뒤 별도 업로드 실패** | EBUSY는 timeout 약 2초 뒤 ZIP 읽기에서 발생했다. timeout의 선행 원인이라는 근거는 없다. 실패 뒤 artifact가 사라져 진단이 막힌 것은 확인된다 |
| **후보: 살아 있는 writer/lock과 업로드 충돌** | [ci_suite 405~412행](https://github.com/ehojune/bioinfo-team-3d/blob/75758303ffa2c784e3ee2395e591bd800cca59e6/scripts/ci_suite.py#L405)은 worker별 stack 파일을 열고, [596~622행](https://github.com/ehojune/bioinfo-team-3d/blob/75758303ffa2c784e3ee2395e591bd800cca59e6/scripts/ci_suite.py#L596)은 150초마다 기록한다. lock 파일도 artifact 트리 안이다. 종료 경로에서만 stream을 닫으며, workflow는 실패 뒤 같은 트리 전체를 바로 업로드한다. 로그에는 실패 파일·PID·handle이 없어 어떤 파일이 잠겼는지, worker가 생존했는지는 **UNVERIFIED** |
| **확인: console만으로 node 복원이 불가** | `--durations=15`는 종료까지 도달하지 않았다. stack snapshot은 console이 아닌 파일에 기록한다. 로그에 per-test limit 안내·worker crash·node별 결과가 없다는 사실은 hang 부재의 증명이 아니다 |

coverage report의 `if: success()` 때문에 실패 시 완전한 receipt를 만들지 않는 것은 현 계약이다([workflow 201~219행](https://github.com/ehojune/bioinfo-team-3d/blob/75758303ffa2c784e3ee2395e591bd800cca59e6/.github/workflows/test.yml#L201)). 진단 보존과 PASS/재사용 receipt를 계속 분리해야 한다. 이 PR은 workflow·`ci_suite.py`·pytest timeout 설정을 바꾸지 않았지만 변경 시험이 실행량에 미친 영향은 미확인이다. 변경과 무관한 실패로 판정하지 않는다.

## 다음 claim의 최소 제안

| 파일·범위 | 제안·완료 증거 |
|---|---|
| `.github/workflows/test.yml`의 Windows 실패 후 진단·업로드 | pool 실패 시 run/attempt에 묶인 checkpoint·stack tail·진단 JSON을 먼저 console에 남긴다. 임시 lock은 업로드에서 제외하고 읽을 수 있는 파일만 별도 staging한다. 읽기 실패는 상대경로와 오류를 남기며 원래 pool 실패를 유지한다. 잠긴 파일 하나 때문에 나머지 증거가 유실되지 않아야 함 |
| `scripts/ci_suite.py`의 lock 관측 경계 | worker/node·lock 대기 시작/획득/해제·소유 PID를 진단 전용으로 기록한다. 기존 stack/checkpoint를 재사용하고 test 선택·assertion·lock 보호·상한은 유지한다. 실패 후 현재 node와 대기/실행/정리 구분이 남아야 함 |
| `tests/test_ci_suite.py`와 기존 Windows workflow 검사 | 잠긴 입력·불완전 session에서도 진단 보존, 원래 실패 유지, receipt 미발급을 검증한다. 실제 파일 잠금 확인은 다음 승인 범위에서 결정 |

위 항목은 **제안이며 미수정·미실행**이다. 증거를 보존한 다음 실행 승인이 나온 뒤에만 같은 head 또는 수정 head를 확인한다. 원인 node를 확보하면 해당 시험/fixture로 수정 범위를 좁힌다. hold 해제에는 최종 head 필수 CI 통과와 같은 head 실제 리뷰 등 기존 gate가 필요하다.

로컬 근거: worker scratch의 `578-rev5-windows.log` 594~608·644~772·793~853행. 이번 checkout 변경은 이 문서 한 개이며 commit·push하지 않았다.
