## 결론

Refs #606. runner의 디스크 쓰기(작업 폴더 파일 289번과 SQLite 대부분)를 쓰기 전용 thread로 넘겼습니다. 느린 디스크에서도 API가 멈추지 않습니다. fsync를 0.5초씩 늦춰도 `/api/health`는 한 번도 0.2초를 넘기지 않았습니다(main은 1.03초). "쓰고 나서 다음 단계"와 쓰기 순서는 그대로이고 fsync를 모으거나 미루지 않습니다.

한계: gateway의 SQLite 쓰기(약 900번)는 아직 loop에서 동기로 돕니다. 이유는 아래 "gateway를 옮기지 않은 이유"에 있습니다.

## 쓰기 지점 지도

mock e2e 한 번(요청 4건, task 40개)에서 센 값입니다. `os.fsync`와 SQLite 쓰기 transaction을 호출 지점별로 셌습니다.

### 파일 쓰기(fsync 있음)

| 지점 | 횟수 | 이전 | 지금 |
|---|---|---|---|
| runner `_run_task`: `ws.update_run`(manifest.json 읽기·수정·쓰기) 4곳, 읽기 전용 판정·환경 수정의 3곳 | 160(뒤 3곳은 e2e에서 안 돎) | loop | thread |
| runner `_run_task`: `outputs/RESULT_<id>.md`·`RESULT.md` | 80 | loop | thread |
| runner `_run_task`: `TASK*.md`, 참고 파일 | 45 | loop | thread |
| 파견직 계약 `registry.save_contract`(`atomic_write_text` 2개) | 4 | loop | thread |
| 직원 CLI 설정 `prepare()`(mcp.json, system_prompt.md, AGENTS.md, GEMINI.md, .gemini/settings.json, prompt.md, role.md, output_schema.json), `command.txt`, `stderr_tail.txt` | e2e 0(mock은 안 씀), 실제 엔진은 task당 약 5~9 | loop | thread |
| `ctx.record_run`(session id, model id, plugin 기록) | e2e 0, 실제 엔진은 task당 2~3 | loop | loop(동기 호출이라 그대로) |
| 합계 | 289 | loop 289 | loop 0 |

loop 밖이라 손대지 않은 곳: `labhq_hpc`·`labhq_annot`의 쓰기(직원 CLI가 띄우는 MCP 서버 프로세스), `record_tool_use`(Claude hook 프로세스), CLI(`cli._pid_file`, bench), `paper_skills.finish`(`asyncio.to_thread`), 의미 모델 그림자·액션 기록(daemon thread, 기본 off).
fsync 없이 rename만 하는 곳도 그대로입니다: round 기록(`RoundRecorder.write`), 입력 hash cache, 시스템 CA, Claude `--settings` 파일.

### SQLite 쓰기(WAL, `synchronous=FULL`이라 커밋마다 fsync)

| 종류 | main(loop) | 지금 loop | 지금 thread |
|---|---|---|---|
| runner outbox: `enqueue`(emit·task.accepted 등) | 261 | 0 | 261 |
| runner outbox: `ack` | 약 520 | 0 | 약 520 |
| runner `accepted_task` put | 약 80 | 0 | 약 80 |
| runner 그 밖(`job`·`notified` put) | 3 | 3 | 0 |
| gateway 이벤트 순번: `append_event`, `commit_terminal`, `commit_request_event`, `set_message_display` | 448 | 448 | 0 |
| gateway task 원장(dispatch, runner 메시지 처리) | 192 | 192 | 0 |
| gateway 요청 행 `save_request` | 117 | 117 | 0 |
| gateway step 제어, pi_language, 승인·ask, 종료 전달 등 | 약 137 | 약 137 | 0 |
| 합계 | 약 2,330 | 897 | 약 1,430 |

## 바뀐 것

- `labhq/durable.py`(새 파일): 쓰기 전용 thread와 FIFO 대기열. 대상마다 하나입니다(작업 폴더 파일 하나, SQLite 파일마다 하나). `await run(lane, fn)`은 끝날 때까지 기다리고, `call(lane, fn)`은 동기 호출입니다. thread가 안 떠 있으면 그 자리에서 쓰고, 일이 쌓여 있으면 뒤에 서서 기다립니다. 2초 놀면 thread가 끝나고 다음 쓰기에 다시 뜹니다.
- 파일 쓰기: `write_owned`, `atomic_write_text`는 같은 일을 하되 위 `call`로 갑니다. `awrite_owned`, `aatomic_write_text`, `TaskWorkspace.aupdate_run`·`awrite_task_md`·`awrite_reference_files`, `RunContext.awrite_meta`, `Registry.asave_contract`가 코루틴용입니다. manifest.json의 읽기·수정·쓰기는 한 작업이라, 사이에 다른 쓰기가 끼어 갱신을 잃지 않습니다.
- `StateStore`: 읽기 연결과 쓰기 연결을 나눴습니다. 쓰기는 전부 쓰기 연결 하나로 한 번에 한 transaction씩 갑니다. 읽기는 쓰기 도중에도 기다리지 않습니다(WAL). transaction 안의 읽기는 쓰기 연결로 합니다(`request.followup_done`이 방금 저장한 요청 행을 읽기 때문). `close()` 뒤의 쓰기는 이전처럼 `ProgrammingError`입니다.
- runner: `emit`과 `_on_message`의 outbox 쓰기·ack가 코루틴용 경로를 탑니다. 메시지는 계속 `Runner.send`를 거칩니다(테스트가 `send`를 바꿔 가로챕니다). 결과 메시지와 "finished" 표시, `task.accepted`와 "running" 표시는 한 작업이라 취소돼도 갈라지지 않습니다.
- 종료: gateway 종료 hook과 `Runner.run_forever`의 `finally`가 대기 중인 쓰기를 모두 끝낸 뒤 나갑니다. 프로세스 종료 때도 `atexit`이 같은 일을 합니다(30초 상한).
- 새 test 27건: `tests/test_durable_writer.py`(25), `tests/test_loop_liveness_606.py`(2, 새 API 없이 쓰여서 main에서도 돌아갑니다). 기존 test는 하나도 바꾸지 않았습니다.

## 독립 리뷰 반영(PR 리뷰 3건)

리뷰어 둘이 따로 찾았고 적대적 확인을 거친 결함 셋입니다. 각각 고치기 전 head에서 test가 실패하는 것을 확인했습니다.

| 결함 | 원인 | 고침 | test |
|---|---|---|---|
| P1: `task.cancel`이 두 번 오면 `task.result`가 안 나가 gateway가 영영 기다림 | 취소·실패 처리(`_run_guarded`)가 `emit`을 두 번 이어 불렀는데, `emit`이 이제 멈추므로 둘째 취소가 둘 사이에 들어옴 | 닫는 사건(상태 + `task.result` + finished 표시)을 `Runner.emit_all`로 한 작업에 묶었습니다. `controls.finish()`도 보내기 전에 불립니다. `_run_task`의 닫는 쌍 9곳, 거부 경로, 승인·ask 쌍, job 제출·상태·완료와 그 표시도 같은 규칙입니다. 각 사건은 계속 `Runner.emit`을 거칩니다. | `test_a_second_cancel_while_the_closing_events_commit_still_delivers_the_result_once` |
| P2: 마지막 결과를 보내다 취소되면 취소 결과가 하나 더 나가 gateway가 덮어씀 | 결과 commit은 끝까지 가는데 취소 처리가 둘째 `task.result`를 보냄 | runner가 task마다 "결과가 이미 나갔다"를 적고(`_result_queued`), 취소·실패 처리는 그러면 아무것도 더 보내지 않습니다. 새 실행이 시작되거나 guarded 실행이 끝나면 지웁니다. | `test_a_cancel_during_the_final_result_does_not_queue_a_second_result` |
| P2: Windows에서 쓰기 thread의 `os.replace`가 loop thread의 `read_owned`와 겹치면 `PermissionError` | `read_owned`의 열기는 `FILE_SHARE_DELETE`가 없어 교체를 막고, 교체 중에는 열기도 거부됨 | `HeldDir.replace`가 Windows에서 `util.replace_file`처럼 짧게(최대 약 1초) 기다려 다시 시도합니다. `read_owned`의 열기도 `PermissionError`를 짧게(최대 약 0.1초) 다시 시도하고, 그래도 안 되면 이전처럼 없음으로 읽습니다. | `test_replace_waits_out_a_reader_that_still_has_the_file_open`, `test_read_owned_retries_a_sharing_violation_instead_of_reporting_the_file_missing`(Windows 전용) |

옮기지 않은 "멈추지 않음" 가정 한 곳: `_on_control`의 `agent.control` + 안내 로그 두 사건은 정보 표시라 갈라져도 상태가 어긋나지 않아 묶지 않았습니다. 승인 응답의 `agent.status` + ack는 둘째가 빠져도 gateway가 결정을 다시 보내 ack가 나가므로 그대로 뒀습니다. gateway가 같은 task의 둘째 `task.result`를 무시하게 하는 것은 하지 않았습니다(runner 쪽에서 막았고, gateway는 이번에 안 건드립니다).

## 설계 판단

| 판단 | 이유 |
|---|---|
| 쓰기 thread를 프로세스에 하나가 아니라 대상마다 하나로 | 처음에는 하나였습니다. e2e처럼 gateway와 runner가 한 프로세스에 있으면 gateway의 동기 커밋이 runner의 막힌 파일 쓰기 뒤에 서서 `/api/health`가 0.30초 걸렸습니다. 순서가 필요한 것은 같은 파일·같은 DB뿐이라 대상별로 나눴고, 같은 대상 안에서는 그대로 FIFO입니다. |
| SQLite: 쓰기 전용 연결 하나 + 읽기 연결 하나(소유 thread 하나로 합치지 않음) | 읽기까지 쓰기 thread로 보내면 loop이 읽기마다 막힌 커밋 뒤에 섭니다. 연결 하나를 lock으로 나눠 쓰면 읽기가 커밋이 끝나기를 기다립니다(커밋 중에는 sqlite가 그 연결을 잠급니다). WAL에서 읽기 연결은 쓰기를 기다리지 않습니다. |
| 동기 호출도 같은 대기열 | 코루틴이 넣어 둔 쓰기를 동기 쓰기가 앞지르면 같은 키에 옛 값이 나중에 덮입니다. 일이 쌓여 있으면 뒤에 서고, 비어 있으면 그 자리에서 씁니다(CLI·스크립트·thread 없는 테스트는 이전과 같음). |
| 쓰기는 취소되지 않음(`asyncio.shield`) | 동기였을 때는 쓰기 도중에 취소될 수 없었습니다. 호출한 코루틴이 취소돼도 쓰기는 끝납니다. |
| 직렬화는 호출 시점에 | `aput`, `aenqueue`는 본문을 부르는 순간 JSON으로 굳힙니다. 이후 호출자가 바꿔도 행은 그대로입니다. |

### gateway를 옮기지 않은 이유

처음에는 gateway의 `publish`(요청 행 저장)와 runner 메시지 처리의 task 활동 저장도 옮겼습니다. 기존 test 9건이 깨졌습니다(`test_state` 3, `test_ask_review3` 2, `test_login_wait`, `test_request_lifecycle`, `test_staff_control`, `test_staff_control_review`). `publish`가 처음으로 실제로 멈추게 되면서 "`await asyncio.sleep(0)` 한 번 뒤에 dispatch가 runner 소켓에 나가 있다" 같은 가정이 깨졌습니다. 같은 가정은 제품 코드에도 있을 수 있습니다(클라이언트가 없으면 `publish`는 한 번도 멈추지 않아 왔습니다). 그래서 gateway는 이번에 안 건드렸고, 되돌린 뒤 9건이 다시 통과했습니다.

옮기려면 따로 풀어야 하는 것:
- 이벤트 순번: `append_event`와 `self.events`(메모리), snapshot의 `seq`(DB)가 짝입니다. 커밋과 목록 추가 사이에 loop이 끼어들면 새 클라이언트가 이벤트 하나를 영영 못 받는 틈이 생깁니다.
- task 원장은 DB가 원본이라 읽고-고치고-쓰기(`get` 뒤 `put`)가 많습니다. 쓰기를 늦추면 옛 값을 읽고 덮을 수 있습니다.
- 요청 행은 메모리가 원본이라 안전하지만 `save_request`가 동기 함수로 100곳 넘게 불립니다.

## 확인

| | main | 지금 |
|---|---|---|
| fsync 0.5초일 때 `/api/health`(task 하나 실행 중, 별도 thread에서 20ms마다 조회) | 창 안에서 5번 답함, 최대 1.03초, 중앙값 0.86초 | 117번 답함, 최대 0.011초, 중앙값 0.005초 |
| SQLite 커밋 0.5초일 때 runner `emit` 3번 + ack 동안 loop 정지 | 2.01초 | 0.2초 미만(test가 확인) |
| e2e mock 지연 없이(통과 횟수, call 시간) | 5/5, 9.9~10.9초 | 10/10, 9.2~11.4초 |
| e2e mock에 fsync마다 0.2초 지연 | 69.4초(289번 모두 loop thread) | 68.6초(loop thread 0번) |

벽시계 시간은 같습니다. e2e는 쓰기를 하나씩 기다리며 이어지는 흐름이라 쓰기 시간이 그대로 더해집니다. 달라진 것은 그 사이 loop이 살아 있다는 점입니다(위 두 번째 줄 이상).

- 새 test 27건은 모두 통과했습니다. `tests/test_loop_liveness_606.py`의 2건은 main에서 실패합니다(`/api/health` 1.01초, loop 정지 2.01초). 나머지는 새 API를 쓰니 main에서는 import부터 실패합니다.
- 충돌 모의: 부분 파일은 없고(대상 파일은 옛것이나 새것), 적용된 쓰기는 대기열 앞쪽 K개였습니다. 파일 3지점, SQLite 2지점, runner 재시작 1건(outbox 앞쪽 4개와 중단된 task 결과 하나, 순번 연속)을 확인했습니다.
- 전체 pytest(리뷰 반영 전 head): 6,651 passed, 59 failed, 66 skipped. 실패 59건은 이 PC에서 main도 똑같이 실패하는 것입니다(`test_web_3d.py::test_office_in_node` 58건은 node 경로, `test_staff_control_steps::test_a_cli_start_the_runner_cannot_record_ends_the_cli`은 `tasklist` 출력 해석).
- `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.
- 리뷰 반영 뒤에는 PI 시운전 중이라 프로세스를 띄우는 test를 로컬에서 돌리지 않았습니다. 프로세스를 띄우지 않는 Runner·Hub test 1,000여 건과 새 test를 돌렸고(통과), 나머지는 CI에 맡겼습니다.

## 남은 것

- gateway SQLite 쓰기 약 894번(위 표)은 loop에서 동기입니다. 느린 디스크에서 gateway API는 아직 커밋 시간만큼 멈춥니다. 위 세 가지를 풀 설계가 먼저 필요합니다.
- 동기 호출 쪽은 같은 대상의 대기열 뒤에서 기다립니다. 그래서 runner에서 아직 동기로 남은 `record_run`은 쌓인 쓰기가 끝나기를 기다린 만큼 loop을 붙잡습니다(이전에는 자기 쓰기 시간만큼). 실제 엔진은 task당 2~3번입니다.
- Windows에서 `write_owned`의 교체(`HeldDir.replace`)는 백신이 막 쓴 파일을 잡고 있으면 `PermissionError`로 실패합니다. 다른 곳은 `util.replace_file`이 기다려 주지만 여기는 재시도가 없습니다. 새 test(같은 파일을 1,000번 바꿈)에서 보였고 이번 PR 밖입니다.
