# issue 정리 A (2026-11-07)

lookup-only. 기준은 [#1076 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1076)의 "열린 issue 정리 기준"이다. 완료 조건이 main에서 충족됐으면 근거를 달아 닫고, gate에 걸렸으면 남은 일과 재개 조건만 적는다. 근거는 main `32b5baa7`(2026-11-07) 기준이다.

| issue | 판정 | 근거 한 줄 |
|---|---|---|
| [#912](https://github.com/ehojune/bioinfo-team-3d/issues/912) 10-24 인계 보고 | 닫음 | 보고가 묶은 D #904·#905·#906·#907과 #890은 모두 닫혔다. 남은 B #910, C #739는 각자 issue에서 추적한다 |
| [#560](https://github.com/ehojune/bioinfo-team-3d/issues/560) 시운전 1차 추적표 | 닫음 | 표 20행 중 19행이 병합·배포·닫힘이다. 남은 16행(디자인 리뉴얼)은 [#546](https://github.com/ehojune/bioinfo-team-3d/issues/546)이 추적한다 |
| [#618](https://github.com/ehojune/bioinfo-team-3d/issues/618) PR #614 follow-up | 닫음 | P1(동시 시작 경쟁)은 PR #967, P2(broker port 사전 충돌)는 PR #1024(`a6bdb88a`)가 main에 넣었다 |
| [#1070](https://github.com/ehojune/bioinfo-team-3d/issues/1070) 병합된 branch 정리 | 닫음 | 완료 조건(전후 개수, 보존 사유, bundle 검증, 불변 조건)을 두 번(rev1·rev2) 충족했다. 삭제는 0건이고 이유가 적혀 있다 |
| [#963](https://github.com/ehojune/bioinfo-team-3d/issues/963) PI 댓글 감시·예약 전달 복구 | 열어 둠 | 아래 |
| [#674](https://github.com/ehojune/bioinfo-team-3d/issues/674) 환경 단계가 runner별 환경을 만들지 않음 | 열어 둠 | 아래 |

## 닫은 issue의 근거

### #912
- 본문의 체크리스트: D(#904, 하위 #905·#906·#907), B(#910, 선결 #890), C(#739).
- 현재: #904·#905·#906·#907·#890 모두 CLOSED. #910(새 설치판 KRAS 연구 1건)과 #739(Astra·Sol·Opus 비교 시운전)는 OPEN이고 실행 승인에 걸려 있다. #914(실행 준비)도 OPEN이다.
- 이 issue는 10-24 05:00의 한 번짜리 인계 기록이라 추적할 일이 남아 있지 않다. 남은 일은 #910·#739·#914가 각자 갖는다.

### #560
- 표 20행 중 마지막 갱신 때 열려 있던 것은 16행(디자인 리뉴얼, #546), 17행(파견직 C·B), 20행(#576 "PR 열림")이었다.
- 현재: 표가 가리키는 #545·#544·#548·#547·#554·#561·#565·#562·#567·#571·#569·#566·#563·#572·#576·#553·#551 PR은 모두 MERGED, #557·#558·#559·#549는 CLOSED다. 17행의 #549는 닫혔고 #553이 병합됐다.
- 남은 것은 16행 디자인 리뉴얼뿐이다. #546이 OPEN이고 10-24 대조 댓글이 3D 개편·탈락 시안 보관 위치·디자인 스킬 장착을 남은 범위로 적었다. 17행의 #549는 닫혔고 #553·#576은 병합됐다.

### #618
- 본문 두 지적: P1 `instance_guard.py:449` 동시 시작 경쟁, P2 `:78` broker port 충돌.
- P1: PR #967(`4a25e06c`). state별 OS lock 안에서 조회·기록을 묶고, 살아 있는 owner 보호·stale owner 복구·실패 획득자가 다른 owner를 지우지 않음을 시험한다(이슈 댓글 10-26).
- P2: PR #1024(`a6bdb88a`, 2026-10-29). `labhq/instance_guard.py:38` `BROKER_FILE = "labhq-broker-port-{port}.json"`로 `runner.broker_port`마다 소유 기록을 두고(`:221`), `:384-390`에서 이미 다른 설정이 쓰는 port면 시작 전에 막는다(`key == "broker_port"`).
- 시험: `tests/test_instance_guard.py`의 `test_a_second_runner_on_the_same_broker_port_is_refused_before_it_starts`·`test_runners_on_different_broker_ports_do_not_conflict` 등 broker 관련 4건을 이번에 돌려 통과했다. manual: `docs/manual/15-config.md` 12행.
- 총괄의 이전 판단(10-26 "P2는 보류, 새 장애 근거가 있으면 재판단")은 그 뒤 #1024가 처리해 해소됐다.

### #1070
- 완료 조건(이슈 본문): 전후 개수, 삭제한 이름과 SHA, 보존한 후보와 이유, recovery bundle 검증, 불변 조건 확인, "안전 후보가 없으면 이유를 남기고 finish".
- rev1(10-30): 로컬 157개·origin 추적 71개 전후 동일, bundle 359 refs 검증, 작업 트리 128개 확인, 14개 stale 추적 ref는 main 선조가 아니라 prune 보류.
- rev2(11-01): 로컬 287개·추적 200개 전후 동일, bundle 490 refs 검증, 작업 트리 134개(16개 dirty) 확인, 50개 추적 ref는 prune 보류, 병합됐어도 쓰임을 배제하지 못한 branch는 보존.
- 남은 것: 보존된 branch 중 어느 것이 더 쓰이지 않는지는 소유자(각 worker·PI)가 알려야 한다. 그때 새 issue로 다시 한다. 이 issue의 조건은 두 번 충족됐다.

## 열어 둔 issue

### #963
- 닫지 않는 이유: 마지막 판단(10-26)이 "기한 뒤(10-28 21:00 KST 임시 발신 권한 제거 뒤) 전달 경로 미확정"으로 OPEN을 유지했다. 이 상태는 저장소가 아니라 로컬 운영 도구에 있어 이번 정리에서 확인할 수 없다.
- 정황: 개발방에 PI 댓글을 옮긴 "자동 관측"·"정기보고" 메시지가 11-06~07에도 계속 도착한다. 임시 기한 뒤에도 전달 경로가 동작하는 것으로 보인다. 다만 어느 신원·예약이 그것을 하는지, 로그인·재부팅 뒤에도 유지되는지는 확인하지 못했다.
- 남은 일(총괄 확인): 현재 전달 경로의 발신 신원, 예약 등록 상태, 재부팅 뒤 복구를 로컬에서 한 번 확인하고, 문제가 없으면 이 issue를 닫는다. 재개 조건: 총괄이 로컬 확인 결과를 한 줄로 남기거나, 전달이 끊기는 사례가 생길 때.

### #674
- 닫지 않는 이유: 총괄의 rev11·rev13 판정이 "실제 WSL 실행과 #666 의존 항목이 남아 계속 연다"이다. 코드 결함은 못 찾았다.
- 완료된 것: B안(읽기 grant, Claude 한정)은 PR #1176으로, Codex·Gemini 소비 단계의 경로·환경 변수·브리핑은 #1178(닫힘)로 main에 있다(`labhq/runner/daemon.py:313-314` `ENVIRONMENT_ENGINES`·`ENVIRONMENT_GRANT_ENGINES`).
- 남은 일과 재개 조건
  1. 실제 WSL runner에서 Claude가 grant된 venv의 interpreter를 실행하는지(승인 카드 포함). 재개 조건: 시운전(#739·#910) 승인 또는 WSL runner 실측 승인.
  2. #666(OS 샌드박스) 의존 항목. 재개 조건: #666 진행.
- rev13이 총괄 판단을 요청해 둔 작은 개선 두 건은 **이미 main에 있다**(정리 중 확인, 처음에는 열려 있다고 적었다가 바로잡음). PR #1254(`b2d7d807`, 2026-11-01 "binding 든 저장 계획 검사와 publish 뒤 runtime 재확인")이 둘 다 넣었다.
  - `labhq/orchestrator/cso.py`의 `prepare_recruits`가 `validate_steps(..., stored_environment=any("environment_binding" in s or "environment_bindings" in s ...))`를 넘긴다(`:7451`). 시험: `tests/test_runner_env_plan_674.py::test_a_resumed_bound_plan_is_checked_with_its_scheduler_made_environment_steps`(`:386`).
  - gateway의 `publish` 뒤 runtime 재확인도 같은 PR에 있다. `labhq/gateway/server.py`가 `task.dispatched` 처리에서 task를 기록·전송하기 직전에 `_session_pin_refusal`을 다시 부른다(`:4431-4433`). 시험: `tests/test_runner_env_plan_674.py::test_a_runner_that_changes_runtime_while_task_dispatched_is_published_is_not_sent_the_stale_binding`(`:415`).
  - `tests/test_runner_env_plan_674.py` 25건이 이번에 통과했다. 그래서 #674에 코드로 고칠 작은 일은 남아 있지 않다.

## 한계

- 닫은 issue 네 건의 근거는 이슈 댓글과 main의 파일·PR 상태이고, 실제 운영 인스턴스·로컬 도구 상태는 보지 않았다.
- #618의 시험 4건은 이번에 이 PC(Windows)에서 돌렸고 다른 OS에서는 돌리지 않았다.
