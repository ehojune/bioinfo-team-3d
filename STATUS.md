# STATUS — labhq

<!-- 자동 생성 — 직접 고치지 말 것, entries·docs/status에 쓴다 -->

최신 항목이 맨 위. 단계를 끝낼 때마다 PR 본문과 같은 내용을 여기에 추가합니다 (형식: `.github/pull_request_template.md`).

# 해제 episode의 실행별 ID (#1008)

| 항목 | 내용 |
|---|---|
| 문제 | runner 메모리의 episode 카운터는 재시작·task 재전송 때 0부터 다시 센다. 이전 실행의 해제 명령(episode N)이 새 실행의 N번째 제한에 맞으면 잘못 풀린다 |
| 방식 | 저장하지 않고 ID를 바꾼다. `Escalation.run`(실행마다 새 값) + 카운터 = `episode_id`(문자열). 이전 실행의 명령은 어떤 새 제한과도 맞지 않아 `stale` |
| 바뀐 곳 | `runner/breaker.py`, `runner/daemon.py`, `gateway/server.py`(episode를 문자열로), manual 한·영 |
| 시험 | `tests/test_breaker_enforcement.py` 갱신·추가 |
| 미확인 | 실제 runner 재시작·network E2E |

# 해제 ACK·durable 재전송 (#1008)

| 항목 | 내용 |
|---|---|
| 명령 | `Hub.release_breaker`가 `command_id`·`episode`를 만들어 `store`(`breaker_release`)에 저장한 뒤 보낸다. `sent`는 전송 사실일 뿐 |
| 재전송 | `_push_task_state`(연결·배정·gateway 재시작)가 ACK 전까지 같은 명령을 다시 보낸다. task 종료 때 만료 |
| ACK | runner가 `agent.breaker`에 `command_id`와 `released`/`stale`을 담아 답한다. gateway `_note_breaker_event`가 같은 ID일 때만 명령을 지운다 |
| runner | `Escalation.episode`(constrain 진입마다 +1), `last_command`. 재전송은 재적용하지 않고 다시 ACK, 다른 episode는 `stale` |
| 시험 | `tests/test_breaker_enforcement.py`(+2, 1 수정) |
| 미확인 | 실제 network E2E, runner 재시작 뒤 복구(runner 메모리의 episode·last_command는 재시작 시 초기화), CSO 승인, compaction |

## 단계
#809 2차: research_protocol·pi-qa·vocabulary 영문판.

## 한 일
1. `docs/research_protocol.en.md`, `docs/pi-qa.en.md`, `docs/vocabulary.en.md`를 추가했습니다. 장·절 순서와 표 구조는 한국어판과 같고 코드·키·설정 이름은 원문 그대로입니다.
2. `vocabulary.md`는 `scripts/vocab_tables.py`가 만드는 생성 파일입니다. 영문판은 생성기 출력이 아니라 한국어 점검표 250칸을 손으로 옮긴 사본이며 파일 머리에 그렇게 적었습니다. `labhq/vocab/`이 바뀌면 손으로 갱신해야 합니다.
3. `README.en.md`의 Further reading 표와 29번째 줄, `docs/manual.en.md`의 해당 링크(research_protocol·pi-qa·vocabulary)를 영문판과 영문 anchor로 바꿨습니다.
4. `AGENTS.md`의 영문판 동시 갱신 안내에 세 문서를 더했습니다.

## 테스트 결과
- 새 문서의 내부 anchor 대조: 깨진 anchor 0. `test_vocab_tables.py`·`test_integrations.py`·`test_variant_effect_topic.py`·`test_ci_skip_policy.py` 226 passed. `check_public.sh` 통과.
- 미검증: 원어민 검수, 한국어판과의 문장 단위 전수 대조(vocabulary는 250칸 순서 대응과 표본 확인만 했음).

## 바꾼 파일
docs/research_protocol.en.md, docs/pi-qa.en.md, docs/vocabulary.en.md(신규), README.en.md, docs/manual.en.md, AGENTS.md, patch_notes/entries/claude-worker3--docs-en-2.yaml, 이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
#809 1차: 영문 매뉴얼.

## 한 일
1. `docs/manual.en.md`를 추가했습니다. `docs/manual.md`와 장·절 순서가 같고(1586줄/1589줄), 코드·명령·설정 키·경로는 원문 그대로입니다. 다른 문서(pi-qa, research_protocol, reference/ 등)로 가는 링크는 그대로 두고 한국어 문서임을 표시했습니다.
2. `README.en.md`의 manual 링크(Further reading 표, 본문 6곳)를 영문판과 영문 anchor로 바꾸고 29번째 줄 문구를 고쳤습니다. 맨 위 배지는 `scripts/integrations.py`가 만들어 `docs/manual.md#연결된-도구` 등을 가리키므로 건드리지 않았습니다.
3. `AGENTS.md`에 "한국어 manual을 고치면 영문판도 같은 PR에서 고친다"를 적었습니다.
4. 영문판의 "연결된 도구" 표는 생성 표(`<!-- integrations -->` 표식)가 아니라 손으로 옮긴 사본입니다. 생성기는 한국어 manual만 갱신하므로 도구가 바뀌면 영문 표도 따로 고쳐야 합니다. 영문판 본문에 그 사실을 적었습니다.

## 테스트 결과
- 영문판 내부 anchor 링크를 제목 slug와 대조: 깨진 anchor 0.
- `python scripts/patch_notes.py check`, `scripts/check_public.sh`는 PR 전에 실행합니다(결과는 PR 본문).
- 미검증: 번역 품질의 원어민 검수, 한국어 manual과 문장 단위 대조(구조·핵심 수치는 대조했으나 전수 대조는 하지 않음).

## 바꾼 파일
docs/manual.en.md(신규), README.en.md, AGENTS.md, patch_notes/entries/claude-worker3--manual-en.yaml, 이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
공동 작성자 훅의 본문·trailer 구분과 모델명 — Closes #808.

## fe745fac 누락 사례를 먼저 가른 결과 (이 훅의 범위 아님)
- `fe745fac`("Merge main with reviewed Windows ACK diagnostics from PR873")은 부모 2개의 **merge commit**이고 author·committer가 모두 사용자(`ehojune <ehojune@gmail.com>`)이며 trailer가 없습니다. 에이전트 환경(`LABHQ_COMMIT_AGENT`·`CLAUDECODE`·`CODEX_*`·`GEMINI_CLI`)이 없는 셸에서 만든 커밋이라, 훅이 "알 수 없는 환경은 막지 않고 그대로 둔다"(`[ -z "$AGENT" ] && exit 0`)는 설계대로 동작한 것입니다. 이 훅의 결함이 아니라 에이전트 표식이 없는 환경에서 만든 통합 commit입니다. 이번 PR은 그 경로를 바꾸지 않습니다(알 수 없는 환경에서 커밋을 막지 않는 동작은 유지).

## 한 일 (`.githooks/commit-msg`)
- trailer 판정을 메시지 전체 grep에서 `git interpret-trailers --parse`(git이 마지막 문단에서 인식하는 trailer)로 바꿨습니다. 본문에 적은 `Co-authored-by:` 예시가 실제 작성자 표시로 오인돼 작성자가 빠지던 경로를 닫습니다.
- Codex 옛 주소(`noreply@openai.com`) 치환도 마지막 문단의 줄에만 적용합니다. 이전 `sed`는 본문 예시의 주소까지 바꿨습니다.
- 모델명: `LABHQ_COMMIT_MODEL`이 있을 때만 이름에 붙이는 규칙은 그대로고, 이미 이름만 있는 에이전트 trailer(`Claude <주소>`)는 환경에 모델이 있을 때만 그 이름에 채웁니다. 이미 괄호나 다른 이름(예: `Claude Sonnet 5.5`)이 있으면 그대로 두고, 환경에 모델이 없으면 아무것도 추정해 넣지 않습니다. 같은 메시지로 다시 돌려도 결과가 같습니다(amend 멱등).
- `AGENTS.md`·`CLAUDE.md`의 해당 줄에 위 규칙을 한 문장 추가했습니다.

## wrapper의 모델명 전달 확인 (저장소 밖 파일, 읽기만 함)
- `~/bin/agyq`(Gemini)는 `LABHQ_COMMIT_AGENT=gemini LABHQ_COMMIT_MODEL="$M"`을 export합니다. `~/bin/gh-agent-env`·`gh-agent-exec.py`(PR·댓글)도 같은 변수를 읽습니다.
- Claude Code 세션은 `CLAUDECODE=1`만 있고 `LABHQ_COMMIT_MODEL`은 export되지 않으며, Codex 쪽 wrapper도 이 저장소에서는 확인되지 않습니다. 그래서 Claude·Codex 커밋은 모델이 trailer에 자동으로 들어가지 않고, 모델이 직접 적은 trailer(이름에 모델 포함)에 의존합니다. 이 변수를 Claude·Codex 실행 경로에서 export하는 일은 저장소 밖(사용자 wrapper) 변경이라 여기서 하지 않았습니다.

## 테스트 결과
- `tests/test_git_hooks.py`: 기존 46건 그대로 + 새 8건 = 54건 통과. 새 시험은 임시 메시지 파일에 훅을 직접 돌립니다: 본문 예시(에이전트 자신의 주소 포함)가 있어도 실제 trailer가 추가되고 본문은 그대로, 옛 Codex 주소는 trailer 문단에서만 치환, 정상 trailer·누락 trailer 추가의 amend 멱등, 이름만 있는 trailer는 모델이 있을 때만 채움(다른 모델명은 유지), 알 수 없는 환경·미지의 에이전트는 막지 않고 그대로, 끝의 `#` 주석 줄이 있어도 trailer 인식.
- 수정 전 훅으로 같은 시험을 돌리면 본문 예시·옛 주소·모델 채움 3건이 실패합니다(나머지는 회귀 방지용으로 양쪽 통과).
- 미검증: 전역 훅(`~/.git-template/hooks`)과 합쳐진 실제 커밋 흐름의 새 시나리오, Windows가 아닌 환경의 `awk` 구현 차이(CI ubuntu·macOS가 확인).

## 바꾼 파일
.githooks/commit-msg, tests/test_git_hooks.py, AGENTS.md, CLAUDE.md와 변경 기록.

## 막힌 점
없음.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

# 폭주 차단기 후속 (#1008)

| 항목 | 내용 |
|---|---|
| P2 1 | runner의 breaker halt를 `agent.control`로 알리고, gateway가 `agent.breaker(enforcement=halt)`를 받으면 `halt_requested`·step `halt_op`을 기록(`Hub._note_runner_halt`) |
| P2 3 | constrain·stop 단계에서는 `Breaker.repeating`만 신호로 쓴다. 거부된 호출의 오류 연속은 승격하지 않음(manual 명시) |
| P2 2 | 끊긴 runner에 보낸 해제는 `_breaker_release_pending`에 두고 `_push_task_state`가 재연결 때 한 번 재전송. gateway 재시작 시 사라짐 |
| P2 4 | `_run_guarded` 주석 분리 |
| 작업판 | `control.constrained`면 "제한 해제" 버튼(`POST /api/tasks/{id}/breaker/release`) |
| 시험 | `test_breaker_enforcement.py`(+2), `web_staff_control.cjs`. 관련 staff_control py 72 passed(timing flake 1건은 단독 재실행 통과) |
| 범위 밖 | constrain 때 CSO 승인, compaction 예외(범위·크기는 PR 본문), 실제 network E2E |

## 단계
#1001·#1004 리뷰의 비차단 지적 2건 정리.

## 한 일
1. `tests/web_office_browser.cjs` `cdp()`: `ws.send`가 동기 예외를 내면(소켓이 이미 닫힘) 그 호출의 30초 timer와 `pending` 항목을 지우고 같은 오류로 reject합니다. 이전에는 Promise 실행기에서 예외가 reject로만 이어지고 timer가 남았습니다.
2. `labhq/gateway/server.py` `Hub._agent_mcp`: `agent.get("engine", "claude_code")`로 읽어 `engine` 키가 없는 직원 dict에도 `labhq_job`을 표시합니다(AgentSpec 기본값 `claude_code`와 같음). 엔진이 `codex` 등으로 명시된 직원은 그대로 제외됩니다.

## 테스트 결과
- 새 시험 `test_an_agent_record_without_an_engine_key_is_a_claude_agent_for_the_job_tool`(engine 키 없음→표시, codex→제외, `job` 없음→없음, AgentSpec 기본값 확인). `test_job_tool_engines_1003.py`·`test_local_jobs_675.py` 46 passed, 1 skipped.
- 실제 Chrome으로 `web_office_browser.cjs` 직접 실행: 두 화면 측정과 정리까지 끝남(약 19.5초).
- 미검증: 1번이 실제로 프로세스 종료를 앞당기는지. `ws.send`가 항상 던지게 만든 임시 사본으로 수정 전후를 재 보았으나 둘 다 0.5초에 끝나 지연 차이를 재현하지 못했습니다(node:test가 끝나면 남은 timer와 무관하게 종료). 따라서 1번은 정리 성격의 변경이며 효과를 입증하지 못했습니다.

## 바꾼 파일
tests/web_office_browser.cjs, labhq/gateway/server.py, tests/test_job_tool_engines_1003.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
#678 현재 main 대조와 남은 한 칸 — Refs #678 (닫지 않음).

## 대조 (main `80db3335`, 코드·시험)
| #678 요구 | 상태 | 근거 |
|---|---|---|
| doctor: 작업 폴더·gateway 상태 드라이브 여유가 기준(10 GiB 또는 5%) 아래면 warn | 있음 | `doctor._disk_rows`(#763·#847), `test_disk_preflight.py` |
| doctor: 더 낮으면(예 2 GiB) **fail** | **없었음 → 이 PR** | `_disk_rows`는 warn만 냈음 |
| runner: task 시작 때 확인, 기준 아래면 멈추고 PI 카드 | 있음 | `Runner._disk_gate`, `runner.disk_fail_free_gb`(기본 2)·`disk_space.py`(#920), `test_disk_start_678.py` 10건 |
| runner: 알려진 입력 크기의 복사 전 확인 | 있음 | #852 `step_inputs.py`, `test_step_inputs_665.py` |
| 큰 원자료 위치 `runner.bulk_data_root` + 직원 브리핑 | 있음 | `daemon._bulk_data_hint`(통제 구역이면 제외), 예시 설정 2곳, `test_disk_start_678.py` |
| 큰 다운로드·압축 해제 **직전** 확인(셸 curl·wget, 크기 미상) | 없음 | 명령 해석이 필요해 이전 배정에서 범위 밖으로 둠 |
| 실제 디스크·운영 PC 검증 | 안 함 | 범위 밖 |

## 한 일 (이 PR)
- `labhq/doctor.py`: `workspace disk`와 `gateway state disk` 행이 여유 `< runner.disk_fail_free_gb`(기본 2 GiB, runner의 시작 기준과 같음)이면 `fail`입니다. 0이면 fail 없음. 임시·직원 홈 드라이브는 warn까지, 조회 실패도 warn 그대로입니다. 안내 문구에 `disk_fail_free_gb`를 추가했습니다.
- `docs/manual.md` doctor 디스크 절에 반영, 시험 2건 추가, 시험 1건의 기대(1 GiB 남은 gateway 상태 드라이브)를 warn→fail로 고치고 이름에서 "still warns"를 "still gets its own row"로 바꿨습니다. 의도(UNC 등 st_dev 0 볼륨도 자기 행을 가짐)는 그대로입니다.
- 효과: `labhq doctor`가 이 두 드라이브가 2 GiB 미만이면 종료 코드 1을 냅니다(기존 fail 행과 같은 처리).

## 테스트 결과
- `test_disk_preflight.py`·`test_doctor.py`·`test_disk_start_678.py` 통과(1건 제외: `test_doctor_warns_about_parent_session_markers_and_pi_skills`, 변경 없이도 이 세션에서 실패하는 환경 문제). doctor·disk·cli·init·up·status·health 키워드 2352건 통과.
- 미검증: 실제 디스크 여유가 부족한 PC에서의 동작.

## 남은 것 (#678 열어 둠)
- 셸 다운로드·압축 해제 직전 확인(크기 미상): 명령 해석과 별도 설계가 필요합니다.
- 실제 디스크·실운영 검증.

## 바꾼 파일
labhq/doctor.py, docs/manual.md, tests/test_disk_preflight.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
#673 후속: repair 비용 이중 집계 회귀 시험 — Refs #673.

## 확인한 것
- repair가 있는 단계에서 `_sum_repair_turns`가 돌려주는 결과는 앞 턴들의 비용·usage를 더한 값을 가지지만, 요청 비용 합계(`Orchestrator.cost`)는 dispatch마다 task id로 한 번만 세는 `_count_cost`로 쌓입니다. 합산 결과의 task id는 마지막 repair의 id이고 이미 센 id라 다시 세지 않습니다(`labhq/orchestrator/cso.py` `_count_cost`).
- 현재 코드에서 이중 집계는 없습니다(결함 아님). 시험만 추가했습니다.

## 한 일 (`tests/test_673_retry_budget.py`)
- 형식 오류 2번 뒤 성공하는 3 dispatch 단계에서 `result.cost_usd`(step 결과)와 `orch.cost`(요청 합계)가 모두 dispatch별 비용의 합(0.3)이고, 합산 결과를 `_count_cost`로 다시 세거나 `_count_recorded_costs`를 다시 돌려도 합계가 변하지 않음을 확인합니다.

## 테스트 결과
- `test_673_retry_budget.py` 9건 통과(로컬). 변경은 시험 파일 하나뿐입니다.
- 변이 확인: repair 뒤 합산 결과를 새 task id로 한 번 더 세도록 `cso.py`를 임시로 바꾸면 이 시험이 `0.8 == 0.3`으로 실패했습니다(원복했고 커밋하지 않음). 이중 집계를 실제로 잡습니다.

## 바꾼 파일
tests/test_673_retry_budget.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
Codex 직원이 쓸 수 없는 `labhq_job`을 안내받던 문제 — Closes #1003 (Refs #681 #675).

## 확인한 것 (main `ac8d9e08`, 코드)
- `labhq_job`은 직원 YAML의 `builtin_mcp: [job]`로 연결됩니다. 기본 직원 중 `job`을 가진 것은 analyst·bioinfo-agent(둘 다 Claude)뿐이고 Codex 직원(engineer·editor·facilities·lit_scout·sci_reviewer)에는 없습니다.
- 그런데 `WORKSPACE_WRITE_RULES`(쓰기 직원 공통 프롬프트)는 엔진과 무관하게 "10분 넘는 계산은 `labhq_job`…, nohup·&는 거절된다"고 말했습니다. Codex 직원은 도구가 없어 따를 수 없습니다.
- "거절된다"는 Claude만 사실입니다. 떼어 내는 명령을 카드 없이 거절하는 승인 gate(`approval_prompt`)는 Claude의 `--permission-prompt-tool`이고, Codex는 `approval_policy="never"`로 OS sandbox 안에서 실행돼 이 gate를 거치지 않습니다(`adapters/claude_code.py:392`, `adapters/codex.py` 264·302). 그래서 이 이슈의 전제 "Codex가 nohup을 쓰면 거절 문구로 `labhq_job`을 안내받는다"는 틀렸고, 실제 결함은 프롬프트의 안내였습니다.
- `job`을 가진 Codex 직원을 따로 만들면 도구가 연결되지만(`_mcp_servers`는 엔진을 보지 않음) 호출은 런타임에서 "local jobs are for Claude staff…"로 늘 실패했습니다.

## 선택과 이유
- **Codex에 `labhq_job`을 주지 않습니다.** runner가 시작하는 job은 Codex가 명령을 가두는 OS sandbox 밖에서 돌아 sandbox를 우회합니다. 이미 `_submit_local_job`이 같은 이유로 거절하고 시험(`test_codex_staff_cannot_submit…`)이 그것을 고정합니다. 안전하게 줄 수 있는 방법이 없어 issue의 두 번째 선택지(엔진별 안내)로 갑니다.

## 한 일
- `adapters/base.py`: 긴 계산 규칙을 엔진별로 나눕니다. Claude는 기존 문구 그대로(`WORKSPACE_WRITE_RULES`가 바이트 단위로 같음), 그 외 엔진은 `WORKSPACE_WRITE_RULES_NO_JOB`: `labhq_job`은 쓸 수 없고, `labhq_hpc`가 있으면 그것, 아니면 끝까지 끝나는 조각으로 나눠 산출을 파일로 남기거나 PI에게 묻고, 떼어 둔 프로세스는 턴이 끝나면 멈춘다고 안내(거절된다는 주장은 뺌). `role_footer`가 `ctx.agent.engine`으로 고릅니다.
- `runner/daemon.py`: `labhq_job` MCP를 Claude 직원에게만 연결하고, 거절 메시지에 대안을 덧붙입니다.
- `gateway/server.py`: 직원 MCP 목록 표시에서도 같은 조건.
- `docs/manual.md`: 로컬 job 절에 반영.

## 테스트 결과
- 새 `tests/test_job_tool_engines_1003.py` 9건: Claude 프롬프트 불변, 다른 엔진 4종의 프롬프트(`job_submit`·"refused" 없음, 대안 있음), 두 문구의 차이가 해당 bullet뿐, 엔진별 MCP 연결(runner·gateway), Codex 기본 직원 YAML에 `job` 없음, Codex 제출 거절 메시지의 대안.
- `test_role_footer`·`test_private_paths`는 Codex도 Claude 상수와 같다고 고정하고 있어 엔진별 기대로 고쳤습니다(약화 아님: Claude 쪽은 같은 문자열 단언을 유지).
- `-k "mcp or job or prompt or footer or write_rules or workspace or agent_spec or adapter"` 878 passed, 40 skipped (진짜 node를 PATH 앞에 둔 상태). 이 머신의 기본 `node`는 Codex 런타임 shim이라 `test_web_3d`의 node 시험은 그 상태에서만 통과합니다(환경 문제).
- 미검증: 실제 Codex 실행에서 새 문구가 직원 행동을 바꾸는지(실모델 실행은 범위 밖), Windows CI.

## 바꾼 파일
labhq/adapters/base.py, labhq/runner/daemon.py, labhq/gateway/server.py, docs/manual.md, tests/test_job_tool_engines_1003.py, tests/test_role_footer.py, tests/test_private_paths.py, config/ci/windows-boundary-v1.json(새 시험 등록)과 변경 기록.

## 막힌 점
없음. Gemini·Antigravity·cli 엔진도 같은 이유로 `labhq_job`을 받지 않는 쪽으로 맞췄습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
Windows CI 실제 R parse 시험의 시간 초과 — Refs #898 (닫지 않음).

## 근거 (실제 로그)
- run 40022968041(main `481221f8`) job 120652090937: `test_real_r_parse_without_running_the_script[stop('not evaluated')\n-pass]` 실패, `assert 'timeout' == 'pass'`.
- 단계 기록: `[{"phase":"version","timeout_s":10,"returncode":0,"elapsed_s":7.704},{"phase":"syntax","timeout_s":10,"timeout":true,"elapsed_s":10.0}]`, 전체 call 17.72초. version이 7.704초 걸려 성공한 뒤 syntax가 10초 한도를 넘겼습니다. 10-26 #987 조사(run 40305182030)도 version 성공 뒤 syntax 초과로 같은 형태입니다.
- 이 두 시험이 확인하는 것은 실제 R이 script를 실행하지 않고 parse하는지입니다. Rscript 시작이 늦은 것은 판정 대상이 아닙니다. R 시작이 느린 이유는 확인하지 못했습니다.

## 한 일 (`tests/test_reproduction_receipt.py`만)
- 실제 Rscript를 띄우는 `test_real_r_parse_without_running_the_script` 2건에만 `build_receipt(timeout=60)`(호출당)을 넘깁니다. 제품 기본값 10초와, 이를 `assert kwargs["timeout"] == 10`으로 확인하는 mock timeout 귀속 시험은 그대로입니다.
- `r_parser_trace` fixture가 version·syntax 소요 시간을 통과할 때도 `UserWarning`으로 남깁니다. CI 로그는 통과한 시험의 출력과 병렬 worker의 출력을 보여 주지 않지만 warnings summary는 출력하므로, 이 줄로 R 시작 시간 분포를 모을 수 있습니다. 기존 `print`·`record_property`는 유지합니다.

## 테스트 결과
- 로컬에는 `Rscript`가 없어 실제 R 시험 2건은 skip됩니다. 같은 파일 107 passed, 3 skipped. mock 시험으로 경고 출력은 확인했습니다.
- 미검증: Windows CI에서 실제 R 시험 2건이 skip이 아니라 실행돼 통과하는지, 60초가 충분한지. 이 PR의 Windows job 로그로만 확인할 수 있습니다.

## 남은 것
- 제품 기본값 10초가 실제 Windows 사용자 PC에서 모자랄 수 있는지는 이 PR이 답하지 않습니다. 모은 단계 시간으로 따로 판단합니다.
- SIGTERM ACK는 #900 이후 관측이 없지만 해결로 선언하지 않습니다. #898은 열어 둡니다.

## 바꾼 파일
tests/test_reproduction_receipt.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
요청 묶음 출력 폴더의 symlink 바꿔치기 — Refs #405 (PR #389 후속).

## 현재 main 대조
- 남아 있었습니다. `build_request_bundle`은 `requests/`를 경로로 한 번 검사한 뒤 `_clear_owned_dir(temp/target)`·`mkdir`·쓰기·`_replace_dir`를 모두 경로로 했습니다. 입력 쪽(`_held_workdir`)만 held handle이었습니다(`labhq/request_bundle.py` main `380d4fca`).

## 한 일 (`labhq/request_bundle.py`만)
- `requests/`를 `HeldDir.hold`로 한 번 열어(링크는 따르지 않음) 빌드 내내 잡고 있습니다. `HeldDir`는 Windows에서 handle이 delete 공유를 허용하지 않아 열려 있는 동안 그 폴더와 상위 폴더가 이름 바꾸기·삭제·교체되지 않습니다(`adapters/held_dir.py` 설명). 따라서 Windows에서는 바꿔치기 자체가 막힙니다.
- 그 밖의 플랫폼에서는 `_clear_owned_dir`(삭제 직전)와 마지막 `_replace_dir` 직전에 `held.same_as(requests/)`를 확인하고, 폴더가 바뀌었으면 삭제·교체 없이 `OSError`로 멈춥니다.
- 큰 함수의 지역 변수 `held`(작업 폴더 handle)와 이름이 겹치지 않도록 새 handle은 `requests_held`로 받습니다. 본문은 `_build_request_bundle`로 옮겼고 동작은 같습니다.

## 테스트 결과
- 새 시험 2건(`test_request_bundle.py`): (1) 빌드 중 `requests/` 이름 바꾸기가 Windows에서 거절되고 빌드가 끝난 뒤에는 handle이 남지 않음. (2) 이전 묶음이 있는 상태에서 빌드 도중 `requests/`를 symlink로 바꿔 두면 Windows는 바꿈 자체가 막혀 빌드가 끝나고, 그 밖의 플랫폼은 `OSError`로 멈추며 바뀐 대상 아래 같은 이름의 폴더(`precious.txt`)가 그대로 남음.
- `test_request_bundle.py` 전체 통과(로컬 Windows). 수정 전 같은 파일 99 passed.
- 미검증: POSIX에서의 실행(두 번째 시험의 `OSError` 분기는 로컬 Windows에서 실행되지 않음, CI ubuntu·macOS로만 확인), 실제 Windows 파일 공유 잠금 아래의 복구 시나리오.

## 한계(남는 것)
- POSIX에서는 확인과 삭제 사이의 아주 짧은 틈이 남습니다(`same_as` 뒤 `shutil.rmtree`는 경로로 동작). 완전히 닫으려면 dir_fd 기반 삭제·교체가 필요한데 `shutil.rmtree(dir_fd=)`는 Python 3.11부터라 3.10 지원과 맞지 않습니다. #405가 말한 "runner 묶음 이전 때 함께 설계"의 몫입니다.
- 묶음 안 파일을 쓰는 경로(`temp / "x"`)는 여전히 경로 기반입니다. 이번 지적의 삭제·교체는 막았지만 새 파일 생성은 같은 방식으로 보호하지 않습니다.

## 바꾼 파일
labhq/request_bundle.py, tests/test_request_bundle.py와 변경 기록.

## 막힌 점
없음.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
StructuredOutput 재시도의 요청 예산 초과 — Closes #673 (이어받음: Codex W3 → claude-worker1, PR #996).

## 원인
- `runner/structured_output.py`의 `run_with_json_retries`가 한 dispatch 안에서 형식 오류를 최대 2번 스스로 다시 호출했습니다. 병렬 단계들은 같은 `request_budget_remaining_usd` snapshot을 받으므로, 잔여 $0.20에서 두 단계가 각각 $0.10을 쓰고 각자 재시도하면 $0.40이 나갔습니다(W3 회귀: 4번 호출, 합계 0.4 > 0.2).

## 한 일
- runner: 오케스트레이터가 보낸 dispatch(`meta.format_retry="gateway"`)에서는 한 번만 실행하고 `error_kind=structured_output_format`과 `TaskResult.format_retry`("plain"|"compact", 세션이 있을 때만)를 돌려줍니다. 마커가 없는 task(직접 runner 사용)는 기존 runner 내부 재시도를 그대로 둡니다. W3의 `admit_retry` callback(daemon·gateway에 연결되지 않은 초안)은 이 구조로 대체해 제거했습니다.
- 오케스트레이터(`dispatch_turn`): 그 결과를 받으면 같은 세션을 새 dispatch(새 task id, `parent_task`, 같은 workdir)로 다시 보냅니다. 따라서 매 repair가 `_check_budget`과 `_reserve_dispatch`를 포함한 기존 예산 확인을 지납니다.
- 승인 규칙: 요청의 남은 예산이 없거나, 같은 요청에서 아직 실행 중인 다른 dispatch의 최대 지출 몫(`inflight_caps`, 예약이 이미 잡은 것은 제외)을 빼면 남지 않으면 repair를 보내지 않고 형식 오류로 멈춥니다. repair 때문에 PI에게 예산을 더 묻지 않습니다. 한 턴의 상한(task·agent·per_task cap)에서 이미 쓴 몫을 뺀 만큼만 repair의 `budget_usd`로 줍니다. 비용·usage는 이전과 같이 한 결과에 합산해 돌려줍니다(각 dispatch는 따로 집계됨).
- `TaskResult.format_retry` 필드 추가, 매뉴얼 해당 절, Windows 경계 목록에 `test_673_retry_budget.py` 등록.

## 테스트 결과
- 병렬 예산 회귀 `test_parallel_format_retries_cannot_exceed_request_budget`: 수정 전 합계 0.4 > 0.2로 실패, 수정 후 통과.
- 새 시험(`test_673_retry_budget.py`): 마커 dispatch의 단일 시도·힌트(plain/compact/세션 없음), 마커 없는 task의 기존 동작, repair가 같은 세션의 새 dispatch이며 task cap에서 쓴 몫을 뺀 예산을 받음, repair가 예산 카드를 만들지 않음.
- 기존 `test_structured_output_retry.py` 21건 중 20건은 그대로 통과합니다. 1건(`test_retry_limit_yields_only_format_error_card`)은 "dispatch가 1번"이라는 단언(`len(hub.calls) == 1`)이 곧 이 이슈가 없애려는 runner 내부 반복을 못 박고 있어 `len(hub.calls) == 3`(repair마다 dispatch)으로 고쳤습니다. 나머지 단언(adapter 3회 호출, 형식 오류 카드 1장, 오류 원문)은 그대로입니다.
- 로컬: structured·cso·budget·retry·step·request·runner·hub·gateway·models·cost 키워드 2621건 통과. 전체 suite는 CI에 맡깁니다.
- 미검증: 실제 gateway·실모델에서의 병렬 실행. 예약(`reserve_before_dispatch`)을 켠 경우의 동작은 기존 예약 시험이 덮는 범위 밖입니다(repair는 예약 경로를 그대로 지나며, 맞지 않으면 기존 예산 카드가 뜰 수 있음).

## 한계·범위
- 이 흐름은 단계 turn(`run_step`)만 대상입니다. 계획·상담·CSO 등 다른 dispatch는 마커가 없어 이전처럼 runner 안에서 재시도합니다.
- 병렬 단계가 모두 실행 중인 동안 repair는 보수적으로 거절될 수 있습니다(몫을 빼면 남지 않을 때). 그 경우 단계는 형식 오류 카드로 멈춥니다.

## 바꾼 파일
labhq/runner/structured_output.py, labhq/models.py, labhq/orchestrator/cso.py, docs/manual.md, tests/test_673_retry_budget.py, tests/test_structured_output_retry.py, config/ci/windows-boundary-v1.json와 변경 기록.

## 막힌 점
없음.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## 단계
Windows CI `test_office_in_node[web_office_browser.cjs]` 150초 멈춤 추적 — Closes #1000.

## 원인 (실제 로그)
- run 40780405903(main `82125d74`), job 123140494538. 출력에 두 화면(1440×900, 390×844) 측정 JSON(`console.log`)이 모두 있고, 그 뒤 subtest가 `testTimeoutFailure`(150006ms)로 끝났습니다.
- 그 JSON을 출력하는 줄 다음에 남는 단계는 `await cdp('Browser.close')`와 finally 정리(최대 2초)뿐입니다. 정리는 150초를 쓸 수 없으므로 멈춘 곳은 `Browser.close`의 답을 기다리는 줄입니다. Chrome이 답을 보내기 전에 소켓을 닫거나 종료하면 그 Promise는 영영 끝나지 않았습니다(`ws.onclose`가 대기 중인 호출을 거부하지 않음, 호출에 상한 없음).
- 이 결론은 로그 구조에서 추론한 것입니다. 실제 CI에서 Chrome이 답을 안 한 사실을 직접 본 것은 아닙니다.

## 한 일 (`tests/web_office_browser.cjs`만)
- `Browser.close`는 5초만 기다리고, 답이 없거나 소켓이 닫히면 단계 이름과 함께 기록한 뒤 finally가 Chrome을 끝냅니다. 모든 단언은 그 전에 끝나 있습니다.
- 모든 DevTools 호출에 30초 상한과 이름 있는 오류, `ws.onclose`에서 대기 호출 거부를 넣었습니다.
- 단계 이름·시각(`office phase +Nms: …`)을 stderr에 남깁니다. `test_office_in_node`가 timeout 때 stderr를 그대로 출력하므로 다음 멈춤은 마지막 단계로 바로 보입니다.

## 테스트 결과
- 로컬 Chrome에서 통과(약 19.6초), 단계 기록 정상. pytest `test_web_3d.py -k web_office_browser`는 실제 `C:\Program Files
odejs`를 PATH 앞에 둔 상태에서 통과(21초)합니다. 이 머신의 기본 `node`는 Codex 런타임을 부르는 `~/bin/node.CMD` shim이라 test의 임시 HOME에서 `node.exe not found under ...cua_node`로 실패하며, 이 변경과 무관한 환경 문제입니다(수정 전 파일도 같은 shim을 씁니다).
- 재현 실험(임시 사본, 커밋 안 함): `Browser.close` 요청을 보내지 않아 답이 없게 하면 수정 전 파일은 150초 timeout까지 멈추고(약 3분), 수정 후는 5초 뒤 `Browser.close: CDP Browser.close had no reply within 5000ms`를 남기고 통과합니다.
- 미검증: 실제 Windows CI에서 원인이 이것이라는 직접 증거와, 이 수정 뒤 CI 재발 여부. 한 번 green은 해결 근거가 아니므로 #1000은 열어 둡니다.

## 바꾼 파일
tests/web_office_browser.cjs와 변경 기록. 제품 웹 코드는 바꾸지 않았고 결함 근거도 없습니다.

## 막힌 점
없음. #844·#898 범위와 섞지 않았습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

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

## 단계
Paper2Agent 설치본 Windows 경로 패치 — Closes #662.

## 한 일
- `labhq setup-paper2agent`(와 `doctor --fix`)가 설치본 `paper2skill/scripts/reading_package.py`의 `str(...relative_to(...))` 네 곳을 `.as_posix()`로 고칩니다. 두 엔진 사본을 교체하기 전에 staging 복사본에서 적용합니다.
- 결과를 `.labhq-source.json`의 `patches`에 남깁니다(patch id, 파일, 고정 upstream commit `8c2d0591…`, `applied`/`already_patched`/`file_absent`).
- 이미 고쳐진 사본은 그대로 둡니다. 문구가 맞지 않거나 일부만 고쳐진 사본은 `RuntimeError`로 설치를 멈추고 기존 설치를 보존합니다.
- `labhq doctor`는 패치되지 않은 옛 설치본을 준비 안 됨으로 보고하고 `--fix`가 다시 설치합니다.
- 매뉴얼 해당 절을 고쳤습니다.

## 테스트 결과
- `tests/test_paper2agent_winpath_662.py` 6건: 네 곳 적용·provenance, Windows 경로(PureWindowsPath)에서 패치 전 키 불일치→패치 뒤 일치, 재적용 무변화, 불일치 upstream 실패와 기존 설치 보존, 반만 고쳐진 사본 실패, 스크립트 없음 기록·옛 설치본 감지.
- 관련 test 126건 통과(`test_contract_provenance`의 marker 동등 비교 2곳은 `patches` 키를 제외하도록 고침).
- 미검증: 실제 upstream 설치본 위 적용, 실제 논문 스킬 빌드(범위 밖). `tests/test_doctor.py::test_doctor_warns_about_parent_session_markers_and_pi_skills`는 변경 없이도 이 세션에서 실패합니다(세션 환경 변수).

## 바꾼 파일
labhq/recruit/paper2agent.py, labhq/doctor.py, docs/manual.md, tests/test_paper2agent_winpath_662.py, tests/test_contract_provenance.py와 변경 기록.

## 막힌 점
없음. upstream PR은 선택 범위라 내지 않았습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

# #59 halt→resume E2E 통합 (#994)

| 항목 | 내용 |
|---|---|
| 담당 | claude-worker2 (Codex W2에게서 인수) |
| 통합 | #995 breaker(`ac8d9e08`)가 들어간 main을 병합. 충돌 없음 |
| 로컬 시험 | `test_59_halt_resume_e2e`·`test_breaker`·`test_breaker_enforcement`·`test_staff_control` 78 passed, `validate_profile()` ok |
| CI | `f0cd6836`: pytest-windows만 `tests/test_state.py::test_new_runner_generation_ends_live_accepted_future` TimeoutError(이 변경과 무관, 로컬 통과). 나머지 통과 |
| 미확인 | 실제 network E2E(CLI·network는 simulated) |

# 폭주 차단기 단계 집행 (#59 ②)

| 항목 | 내용 |
|---|---|
| 담당 | claude-worker2 (Codex W1 초안 `9bbbde3`를 이어받음) |
| 정책 | #59 확정 댓글 6479569164: 미지원 엔진 `unsupported`, hardStop 기본 끔, beat 10초·새 관측 때만 한 단계, compaction 보류 |
| 바뀐 곳 | `runner/breaker.py`(Escalation·stage_record), `runner/daemon.py`(단계 집행·release), `gateway/server.py`(`POST /api/tasks/{id}/breaker/release`), `settings.py`(`breaker_hard_stop`·`breaker_beat_s`), manual |
| 시험 | `tests/test_breaker.py`, `tests/test_breaker_enforcement.py` |
| 미확인 | 실제 네트워크 E2E, 작업판 해제 버튼, constrain 때 CSO 승인, compaction 예외 |
| #673 겹침 | `daemon.py` `emit()` 안의 breaker 호출부는 #673 retry loop와 별개. `gateway/server.py`는 새 함수 `release_breaker`만 추가 |

## 단계
문서 정비 — Refs #992.

## 한 일
- Claude 전체 총괄·Codex 중대형 작업·소속 worker의 완료 책임을 운영 기준에 모으고 AGENTS/CLAUDE 진입점에서 연결했습니다.
- 역할 복귀와 실제 기동·개발 재개를 구분하고 과거 보고 주기·한도의 자동 복원을 막았습니다. 리뷰·자율 복구·짧은 보고 원칙을 정리했습니다.
- 인계의 #990 CI 복구/남은 결함과 #991 지원 요구 구현/운영 미검증을 구분했습니다. README 한영판에는 준비 판정이 실제 복구 성공을 보증하지 않는다는 한계를 연결했습니다.

## 테스트 결과
- 문서 변경만 있습니다. 공개 정보·기록 형식·상대 링크·한영 의미·diff 검사를 수행하며 실행 검증은 이 PR의 근거가 아닙니다.
- 제품 코드·worker·예약·모델·연구·설치·배포는 변경하지 않습니다.

## 바꾼 파일
AGENTS.md, CLAUDE.md, HANDOFF.md, docs/reference/lead-operations.md, README.md, README.en.md와 변경 기록.

## 막힌 점
없음. 실제 운영 복구와 일반 개발 재개는 별도 승인 범위입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #983. `runner.facilities_support`로 시설팀 지원 요구를 선언합니다. 기본은 `unspecified`이며 기존 설치와 init `--force`의 명시 값을 보존합니다.

- required의 부재·실패·미확인·dry-run·mock은 FAIL, optional+ready만 OK, unspecified는 항상 WARN입니다. 다른 직원 FAIL과 기존 login warning·runtime admission은 유지합니다.
- 기존 version/login·시설팀 개별 preflight 증거만 재사용합니다. timeout/미지원·판별 불가는 unverified, 명시적 비인증은 blocked입니다. 추가 probe·직원 추가는 없습니다.
- 기존 doctor 비교: 지원 행 부재로 7 failed. 수정 후 관련 mock/static 273 passed, 추가 force_engine 2건을 포함한 최종 시설팀 회귀 60 passed(앞 검사와 중복). 21조합·직원별 증거·custom contract/만료·무쓰기·init 보존·CLI exit를 확인했습니다. #985/#989 회귀도 통과했습니다.
- Python 3.10 syntax·public·diff·Windows inventory 검사 통과. manual은 LF 기준 194,939→195,168자입니다. 상세 판정표와 한계는 [reference](../reference/facilities-support-983.md)에 있습니다.
- #990의 구현 종료·최종 head 리뷰 요청 뒤 manual 한 문단을 반영했습니다. 최종 head CI·같은 head 실제 review·알려진 P0/P1 수정검증·head guard 뒤 담당 병합하며, 대기는 hold로 표시합니다.
- 실제 설치판 복구는 **UNVERIFIED**, #983 OPEN입니다. 운영 설정/roster·직원/daemon·설치·배포·연구·HPC는 실행하지 않았습니다. token/provider 사용량은 미측정입니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #606 — step_control 저장과 continuation/close 경계를 수정합니다.

- `4618a13`: attach 뒤 revision이 전진해 dispatch가 거부되면 그 task의 steer·drop 알림 예약만 반환합니다. 격리 재현 1 FAIL·2 PASS → 관련 61 PASS. 추가 제어 검증은 150 PASS·7 FAIL 후 checkout PYTHONPATH 지정으로 실패 7건 PASS입니다. 최종 head CI·리뷰와 병합은 대기합니다.
- `4bafedca` Windows CI는 새 test의 inventory 누락으로 시작 전 실패했습니다. `9a8af6b`로 해당 회귀를 Windows boundary cohort에 추가했으며 profile validation PASS입니다.
- PI·attach·settle·release·NACK·close는 최신 row를 lock 안에서 갱신하고 SQLite writer commit 뒤 wake/send합니다. 취소 정산·revision 경계·runner dedup을 유지합니다.
- `23b2273`은 질문-only turn을 완료로 알리지 않아 같은 revision의 답변 후속 turn과 대기 중 steer를 보존합니다. 최종 성공·거절·skip은 기존 close를 거칩니다.
- 취소된 attach는 steer 예약과 자신이 선점한 drop 알림 몫만 반환합니다. 동시 갱신과 rollback을 보존합니다.
- `a97dee5`: 전송 직전 종료 정산 중인 halt는 거절합니다. 취소된 attach는 revision 전진 뒤에도 자기 예약을 반환하며 close 후 새 row의 알림은 보존합니다. 리뷰 P1·P2 회귀 2 FAIL → PASS, 관련 154 PASS·subprocess PYTHONPATH 지정 후 7 PASS입니다.
- `0ee0aaa`: 종료 경쟁 때 새 steer ID만 반환하고 기존 예약은 result의 delivered 정산에 맡깁니다. 후속 P1은 pause·halt·steer 3 FAIL → 관련 164 PASS입니다.
- Linux 3.10/3.12 CI의 mock e2e 실패와 같은 `step ended before dispatch`를 로컬에서 재현했습니다(1 FAIL). 격리 회귀 4 FAIL → 4 PASS, 동시 drop 알림 보존 1 PASS. mock e2e·followup·ask·CSO 430 PASS, 제어 92 PASS. fake CLI import 실패 6건은 test PYTHONPATH 지정 뒤 7 PASS(추가 1건 포함)로 확인했습니다. 설치·assertion 변경은 없습니다.
- manual 후속 수정 195,066→195,145자(LF). public·diff 검사 PASS. 근거는 [PR #990](https://github.com/ehojune/bioinfo-team-3d/pull/990)의 test·CI·리뷰 기록입니다.
- hold는 최종 head CI·같은 head 실제 리뷰·알려진 P0/P1 해결·main/head guard 뒤 해제합니다. #987과 최신 main의 W3 HANDOFF·기록을 충돌 없이 통합했습니다.
- #606 OPEN. 복구는 격리 Store 재열기로 검증했으며 실제 API·실디스크 성능·실프로세스 재시작·운영 복구는 UNVERIFIED입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #989 · Refs #983. doctor가 runner의 custom `contract_dir`에 있는 유효한 직원도 검사합니다.

- YAML 로드 순서·계약 유효성 규칙을 공유하고 기본/custom 경로·만료/archived·중복 ID 결과를 runner와 맞췄습니다. doctor는 파일을 이동하거나 수정하지 않습니다.
- custom 경로 누락 회귀: 수정 전 1 failed, 수정 후 통과. 기본/custom·오류 처리·force_engine·파일/디렉터리 불변을 포함한 관련 mock/static 235 passed.
- Python 3.10 syntax·public·diff 검사 통과. manual 한 문단 갱신: 194,818→194,939자(LF 기준).
- 시설팀 필수/선택 정책은 후속입니다. 실제 설치판 복구는 UNVERIFIED, #983 OPEN·#987 hold 유지. 운영 roster·설치·배포·HPC는 실행하지 않았습니다.

최종 head CI·같은 head 실제 bot review·P1=0·head guard를 확인한 뒤 담당자가 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #606 — control-state 저장 경계만 수정합니다.

- PI·runner·result·halt 만료가 같은 row의 lock을 공유하고 async commit 뒤 memory/event를 반영합니다. PI 제어는 step별로 전송부터 순서를 지킵니다. 삭제 표식과 PI 시작 시점의 세대로 늦은 메시지·진행 중 제어의 부활을 막습니다.
- 반복 asyncio/AnyIO 취소 뒤 commit·rollback을 판정합니다. halt 정리는 제어 row 삭제와 대기 표시·만료 알림까지 마칩니다. step·task/result/dispatch 전체 전환은 범위 밖입니다.
- 수정 전 memory/rollback 회귀 8 failed, PI 전송 경쟁·result의 삭제 row 재개·종료 turn feedback의 새 PI intent 덮어쓰기 각 1 failed. 신규 격리 mock 26건 포함 관련 회귀 결과와 public·diff·Windows state cohort·patch-note·최종 head CI·실제 리뷰는 PR에 기록합니다.
- 실제 API·실디스크 성능·실프로세스 재시작·운영은 미검증입니다. 복구 검증은 격리 Store/Hub 재열기이며 #606은 OPEN 유지합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #606, #983. HANDOFF에 최신 main의 저장·doctor 완료 범위와 잔여를 반영했습니다.

- #982 activity·halt/resume task intent, #984 채용 preflight 읽기, #988 control-state commit async 전환은 병합 완료. task·step·result 수신·dispatch sync 저장은 잔여이며 #990은 미병합입니다.
- #989 doctor의 custom contract_dir roster 정합은 병합 완료. #983 시설팀 필수/선택 정책과 실제 설치판 복구는 잔여입니다. #606/#983 OPEN·운영 검증 UNVERIFIED를 유지합니다.
- HANDOFF 전체 main 대비 4,289→4,662자(+373), 이번 수정 전 4,445→4,662자(+217). LF 기준입니다.
- 검증: public·diff·patch-notes·notes-index 검사. 문서만 수정했으며 제품 코드·test 변경이나 실제 process·실디스크·재시작·운영·설치·배포·연구·HPC 실행은 없습니다.
- 최종 head CI·같은 head 실제 리뷰·미해결 P0/P1·최신 main 및 head guard 확인 후 담당 병합합니다. 결과는 PR에 기록합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #675. 취소 회귀검사의 PID readiness race를 수정했습니다.

- 파일 존재 대신 양수 PID를 읽을 때까지 기존 40초 안에서 기다리고 읽은 값을 보관합니다.
- 프로세스 종료·다른 job 보존·완료 깨움 없음 assertion을 유지합니다. 제품 코드는 바꾸지 않았습니다.
- 로컬: `pytest tests/test_local_jobs_675.py -q -k pid_readiness` → 6 passed, 31 deselected. missing→empty→valid 및 missing/empty/invalid/0/-1 timeout을 fake clock으로 확인했습니다.
- Windows CI inventory 검증 통과. 실제 통합 경로는 CI에서 검증합니다. 로컬 실프로세스·Chrome·WSL은 실행하지 않았습니다.
- 최종 head CI·실제 리뷰·P0/P1·head guard 확인 전 hold를 유지합니다. #675의 다른 잔여 범위와 Windows 두 실패의 원인 미확정 상태는 그대로입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #983 — 시설팀 부재 안내·재문의 순환 방지의 부분 수정입니다.

- facilities→CSO fallback의 prompt와 답변에 시설팀 부재·실제 대체 담당을 명시합니다. 과거 cached/adopted 답변에도 안내를 붙이고 같은 단계의 재문의를 추가 상담 없이 거부합니다.
- 시설팀 가용 경로·재접속 대기·hard stop·read-only 경계는 유지합니다. CSO 응답을 시설팀 진단·수정·복구 성공으로 표시하지 않습니다.
- 시설팀 복귀 뒤 동일 질문이 과거 CSO fallback cache를 우회하도록 고쳤습니다(P2 수정 전 1 failed → 관련 mock 149 passed).
- 비식별 신규 회귀는 수정 전 8 failed·1 passed. 동시 재문의 P2는 추가 회귀 1 failed로 재현한 뒤 lock 내부 재확인·답변 저장으로 고쳤습니다. main #982·#984 반영 후 관련 mock 149 passed입니다.
- Windows 새 test inventory 거부를 고쳐 신규 회귀 12건을 필수 cohort에 등록했습니다. profile·patch-note·문서 목차·public·diff 검사 PASS입니다.
- #982의 manual 반영 뒤 시설팀 설명 한 곳에 새 계약을 기록해 manual P2를 수정했습니다(main 대비 194,563→194,768자).
- hold: 선행 #982·#984 병합·main 반영 완료. 최종 head CI와 같은 head bot review·알려진 P0/P1 수정검증·head guard 뒤 해제합니다.
- roster/doctor 연결·전체 상태 모델·실제 설치판 복구 검증은 남아 #983을 닫지 않습니다. 설치·운영 roster 변경·기동·연구·HPC·배포는 실행하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #606. 계획 채용의 talent YAML 탐색·probation·provenance 검사를 기존 FILES lane에서 기다립니다.

- 분리한 Registry는 경로만 받습니다. 후보 snapshot·거절 결과를 반환하며 live Registry·roster는 loop에서 다룹니다. `arehire(validate=...)`의 저장 직전 재검증·저장·publication 계약을 유지합니다.
- 수정 전 worker thread·heartbeat 회귀 8 failed. 수정 후 전용 22 passed; 200ms 읽기 mock에서 heartbeat 약 31ms, 채용·hash 거절·읽기 오류 판정 유지. 관련 기존 회귀 결과와 최종 head CI·리뷰는 PR에 기록합니다.
- 취소·timeout 뒤 rehire 미시작, 앞선 FILES writer 순서, preflight 뒤 hash 변경 거절, 빈 풀·후보 불일치·이미 active인 후보를 검증했습니다. 보존 diagnostic은 변경하지 않았습니다.
- 실API·실디스크 지연·운영·설치·배포·연구·HPC는 미실행입니다. #606은 OPEN 유지하며 공통 manual은 담당자 통합 범위입니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# Runner activity·control intent 저장 (#982)

Refs #606. activity와 halt/resume task intent를 SQLite writer에서 await하고 transaction 안의 최신 row에 필요한 필드만 병합합니다. 저장 뒤 publish·ACK하며 삭제·완료 row를 복원하지 않습니다. 뒤따른 전체-row 쓰기가 halt intent를 지우면 성공/queued를 반환하지 않습니다.

- 기존 코드 P2 두 건: activity 덮어쓰기·job.submitted 경쟁을 각각 1 failed → PASS로 검증했습니다.
- manual P2 4374841544: async 전환 범위와 남은 step/control_state·result/dispatch 등 sync 경계를 저장 경계·로드맵에 반영했습니다. manual 194,351→194,563자(+212), 저장 경계 문단 338→481자.
- 이번 검증: `test_state_activity_durable`, `test_state_control_durable`, `test_state_control_job_durable`, `test_durable_writer`, `test_gateway_publish_durable` **100 passed**. 기본 Python에 pytest가 없어 기존 repo venv interpreter로 실행했습니다. 새 설치는 없습니다.
- public·diff·notes-index 검사 PASS. patch-notes 검사와 최종 head CI·실제 bot review·head guard를 확인한 뒤 hold를 해제합니다.
- #606 OPEN 유지. 다른 전체-row writer·sync 경계는 남습니다. 실디스크 성능·실프로세스 재시작·실운영·설치·배포·연구·HPC는 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# 로컬 계산 browser QA — Refs #675

정적 Chrome fixture에서 발견한 직원/캐릭터의 local job HPC 오표시와 HPC rack 건수를 고칩니다. backend 변경은 없습니다.

| 상태 | 2.5D | 3D |
|---|---|---|
| 계산 중 | 이름·실행 중·진행률 모름 | 요청 줄과 캐릭터 대기 구분 |
| terminal 직후 | 해당 계산 문구 제거 | 해당 요청 줄에서 제거·다른 요청 보존 |
| snapshot/요청 전환 | ID·상태 확인 중·요청 분리 | 두 요청의 ID·상태를 각각 복구 |

- 수정 전 local 대기 회귀 실패 → 수정 후 관련 Node 66 passed, legacy reducer 43 event PASS.
- bot P2 badge 회귀도 수정 전 실패·수정 후 성공. browser에서 job 정보 없는 badge·strip·단계 흐름의 대기 문구 일치를 확인했습니다.
- bot P1의 기존 `web_issue106` 기대값을 unknown/HPC 두 경우로 분리하고 취소 검사를 유지했습니다. 3D rack·범례·설명 P2도 수정했으며 관련 검사 PASS입니다.
- 해당 Python Node wrapper도 새 성공 문구로 맞춰 1 passed를 확인했습니다. 최종 3D live/demo 화면에서 대기·범례·설명 일치도 확인했습니다.
- Windows source pin만 갱신하고 필수 node는 유지했습니다(profile 검증·CI 검사 62 passed). task 없는 local 대기도 terminal event 뒤 제거하는 호환 회귀를 확인했습니다.
- screenshot·정적 fixture는 worker3 scratch `675-qa/`에 보존합니다. 정적 fixture QA이며 실제 transport·OS 재시작·연구·WSL·운영은 미검증입니다. #675는 닫지 않습니다.
- #979/#980 병합 main을 반영했습니다. manual P1 4374507406의 직원 대기 표·HPC-only rack 안내를 고치고 async 저장 범위와 남은 sync 경계를 manual/HANDOFF에 통합했습니다. README 한영판은 대조 후 유지했습니다.
- 문서 길이: manual 194,280→194,351자, HANDOFF 4,582→4,289자(합계 222자 감소). diff·public·patch-notes·notes-index·integrations 검사 통과. backend·CI gate 변경은 없습니다.
- 최종 head CI·실제 리뷰·알려진 P1·head guard 확인까지 hold입니다. 이전 head의 clean-install은 PASS이며 validation-manifest identity 실패는 별도 조사 중입니다.

plan-rehire가 느린 계약 저장으로 event loop를 막는 경계를 async로 전환합니다. Refs #606.

- `Registry.arehire()`는 별도 Registry에서 읽기·provenance 검사·두 파일 저장을 기존 FILES 단일 job으로 실행합니다. 성공 뒤 loop에서 Registry·roster를 반영하며 sync API는 유지합니다.
- mock 150ms 저장 중 idle/active heartbeat: 수정 전 2 failed → 수정 후 통과. 동일 ID update/hire/rehire, FIFO publication, snapshot, 반복 취소, 저장 오류·재시도, 두 복사본·재로드를 검증했습니다.
- P1 4374314239·4374314243의 지연된 이전 publication·교체된 부적격 계약을 5 failed로 재현했습니다. sync 쓰기 표식으로 FIFO 판정을 보완하고 writer가 읽은 계약의 payload·수습·hash 자격을 저장 전에 다시 검사합니다.
- CI Python 3.10의 반복 취소 3 failed와 저장 오류 masking 2 failed를 수정했습니다. wait_for를 shield하고 내부 task에 취소를 한 번 전달한 뒤 실제 결과를 확인합니다. 3.10의 취소 cleanup·오류 masking을 모사한 회귀도 추가했습니다.
- reload 경쟁 P1 4374470002를 1 failed로 재현했습니다. sync load의 읽기·만료 archive를 FILES 단일 job으로 묶고 live Registry는 호출자 쪽에서 반영합니다.
- 관련 pytest 185 passed, 39 subtests passed. public·diff·Windows cohort 검사 통과. 실제 process·디스크 지연·운영·연구·HPC·설치·배포는 실행하지 않았습니다.
- `hold` 해제 조건: 최종 head CI 성공·실제 리뷰 완료·알려진 P1 수정검증·충돌 없음·head guard 확인.
- 앞선 talent_pool·probation·provenance preflight 읽기와 sync reload의 대기는 남습니다. gateway/task/job 잔여와 별개이며 #606은 OPEN 유지합니다. 공통 문서는 담당 순서대로 별도 통합합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #606. task 취소 intent 저장 하나를 기존 SQLite async writer로 옮겼습니다.

- `Hub.cancel_task()`는 저장 성공 뒤 runner에 취소를 보냅니다. 반복 취소에도 commit 결과·오류를 확인하며 실패하면 전송하지 않습니다. 저장된 intent는 재접속 때 다시 보냅니다.
- 지연 mock의 idle/active lane heartbeat 2 failed를 재현한 뒤 통과했습니다. 관련 mock/static 118 passed·1 skipped입니다. commit→send 순서·반복/AnyIO 취소·rollback·DB 재열기·offline replay를 확인했습니다.
- 리뷰 P2 4374299436의 완료/취소 경쟁은 수정 전 2 failed로 재현했습니다. writer가 완료된 task의 intent 생성을 거부하고 완료 표시 뒤 intent를 정리합니다. await 뒤 완료 여부를 다시 확인해 전송도 생략합니다. 신규 회귀를 Windows 필수 cohort에 등록했습니다.
- 다른 task/result/control 동기 저장은 남아 #606을 열어 둡니다. 공통 manual은 별도 통합합니다. 실디스크 성능·실프로세스 재시작·운영·연구·HPC·배포는 미검증입니다.
- PR #979는 최종 head CI·실제 리뷰·알려진 P1=0 확인까지 `hold`를 유지합니다. 조건 충족 뒤 담당이 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# 로컬 계산 작업판 표시 — Refs #675

- 변경: 기존 job event·pending_jobs를 요청/단계에 연결하고 공통 단계 흐름·task card·3D 요청 줄에 로컬 계산 이름·관측 상태를 표시합니다. 로컬 제출은 프로세스 시작 후 event라는 runner 계약에 맞춰 실행 중으로 표시합니다. HPC와 구분하며 진행률은 모름으로 둡니다.
- 정리: 종료 job·완료/실패/취소 요청·다음 턴·재접속 snapshot에서 이전 대기 표시를 제거합니다. event 밖의 job은 ID·상태 확인 중만 표시합니다.
- 검증: 신규 mock 13건 포함 관련 Node 44건, Python Node wrapper·CI 수집 검사 13건 PASS. 기존 reducer golden 43 event와 snapshot 복구 검사 PASS. 수정 전 신규 fixture는 실패했습니다. 2.5D 요청 cache의 job-only 갱신과 같은 scheduler ID의 task별 격리도 검사합니다.
- 문서: manual 로컬 job 절 1,427 → 1,171자. 기존 설명을 교체했습니다.
- 한계: 실제 browser·운영 요청·연구 QA·WSL·실제 계산 진행률은 미검증입니다. backend·설치·배포·HPC 실행은 없습니다. #675는 닫지 않습니다.
- 병합 gate: 필수 CI·알려진 head P1·충돌·최종 head 확인은 PR에서 기록합니다. 배정에 따른 PI 임시 별도 리뷰 대기 생략을 적용합니다.

Refs #606 — runner `contract.update` 저장 경계.

- 지연 mock에서 idle/active writer 모두 heartbeat 정지 2건을 재현했습니다.
- 기존 FILES writer에서 계약 snapshot을 저장하고 성공 뒤 Registry·roster를 반영합니다. 같은 계약의 갱신은 저장·반영까지 직렬화하며 반복 취소 뒤에도 성공·오류를 확인합니다.
- 관련 mock/static: **132 passed, 7 subtests passed**. 두 복사본·lifecycle·오류·취소·경쟁·재로드·rehire warning 검증입니다. 같은 ID의 recruitment가 먼저/나중에 저장되는 경쟁과 sync plan-rehire 3건도 실패→통과를 확인했습니다.
- 각 파일의 atomic write·fsync 보장은 유지합니다. 두 파일 사이 저장 오류의 rollback은 추가하지 않았고 실패 때 Registry·roster를 갱신하지 않습니다. 재시도로 두 파일이 수렴하는지 확인했습니다.
- gateway/store/job/task 원장은 변경하지 않았습니다. 실디스크 latency·실프로세스 재시작·설치·배포·연구·HPC는 미실행입니다. #606은 열어 둡니다. 공통 manual 통합은 W2 뒤 별도 범위입니다.
- public/diff 검사와 최종 head CI·알려진 P1·충돌 확인 뒤 담당 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #606. 일반 승인 저장을 기존 durable writer로 옮겼습니다. task 원장·runner daemon 잔여가 있어 issue는 닫지 않습니다.

- 카드 삭제·결정·runner 전달 행을 한 transaction으로 저장합니다. 같은 카드의 응답·timeout·close·재접속 전달·ACK를 직렬화하고 commit 뒤 memory·future·전송을 반영합니다.
- 반복 취소에도 시작한 commit의 성공·오류를 확인합니다. 실패하면 카드와 미완료 future를 보존합니다. runner replay는 답한 카드를 다시 열지 않습니다.
- storage 선택 중 취소·재시도, facilities 대기 상태, resume CP1 저장 순서를 유지합니다. 기존 mock은 새 transaction 경계를 주입하도록 갱신했고 assertion은 유지했습니다.
- 관련 mock/static 실행은 각각 142 passed, 87 passed, 14 passed·12 subtests passed입니다(중복 포함). 기존 실패 8건과 새 barrier 회귀로 heartbeat·rollback·경쟁·반복/level 취소·DB 재열기·재접속/ACK를 검증했습니다.
- manual·Windows CI inventory를 반영했습니다. 실제 디스크 지연·실제 process 재시작·운영 검증은 미실행입니다. 설치·배포·연구·HPC 실행은 없습니다.
- 최종 head 필수 CI·알려진 미해결 P1·충돌/head guard 뒤 담당 병합합니다. PI 임시 리뷰 대기 생략을 적용하며 봇 PASS로 기록하지 않습니다.
- 봇 P1 4373997996: runner backpressure 수정 전 2 failed, 수정 뒤 관련 140 passed·12 skipped입니다. 승인 전달은 최대 10초이며 timeout 뒤 연결 등록을 해제하고 durable 전달 행을 보존합니다. 반복 취소 후 lock 해제·후속 ACK도 확인했습니다.
- 봇 P1 4374026562: resume expiry·async/sync retirement 경쟁을 수정 전 3 failed로 재현했습니다. 같은 카드 lock을 event/request lock보다 먼저 잡고 retirement도 단일 transaction으로 저장합니다. sync 호출이 진행 중 승인과 겹치면 async retirement로 넘깁니다. 관련 복구·CP4·취소 mock/static 131 passed·12 subtests passed입니다.
- CI `08021c4d`의 publish 카드 종료 검사 2건은 `sleep(0)` 뒤 비동기 commit 완료를 가정했습니다. 같은 카드의 완료 future를 기다리도록 readiness만 바꾸고 카드 제거·DB expired assertion은 유지했습니다.
- 봇 P1 4374092270: 늦은 번역 snapshot이 결정된 카드를 다시 저장하는 경쟁을 수정 전 2 failed로 재현했습니다. writer transaction에서 결정 존재를 확인해 재삽입을 거부하며 관련 52 passed입니다. DB 재열기에서도 카드가 다시 열리지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #653. gateway snapshot에 요청 메모리의 `runner_waits`를 포함하고 공통 reducer가 대상 직원을 복원합니다. 비대상 직원은 차례 대기를 유지합니다. 영속 schema 변경은 없습니다.

- 수정 전: 후속 이벤트 200·201개에서 통합 회귀 2 failed.
- 수정 후: 199·200·201개 snapshot→Node reducer, ledger, 관련 web 검사 11 passed. 구형·잘못된 payload와 오래된 replay 덮어쓰기 검사도 통과했습니다.
- public·diff 검사 통과. 2.5D·3D의 공통 판정 호출부는 기존 그대로 사용합니다.
- 실제 browser QA·운영 재현은 미검증입니다. PI 임시 리뷰 생략을 적용하며 최종 head CI·알려진 P1·head가드는 병합 전에 확인합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #971. Refs #606, #970. #972보다 먼저 병합하는 최소 수정입니다.

- `wait_halted()`의 초기 두 publish를 기존 cleanup try/finally 안으로 옮겼습니다. 진입 중 취소도 종료 이벤트·대기/제어 상태를 정리하고 CancelledError를 전달합니다.
- 두 publish를 각각 barrier로 멈춘 일반/요청 취소 4개 조합: 수정 전 **4 failed**, 수정 후 관련 mock/static **10 passed**. `halted_waits`·`pi_waits`·task control·DB 정리와 resume·timeout을 확인했습니다. 기존 종료 이벤트·취소 전파 assertion은 유지했습니다.
- resume 검사는 등록 직후가 아닌 `waiting_pi` 발행 완료를 기다리도록 readiness만 고쳤습니다. 취소 재현 지점은 늦추지 않았습니다. temp/state는 저장소·OS TEMP 밖에 격리했습니다.
- 첫 green 명령에 `test_staff_control.py` 전체를 잘못 포함해 hook subprocess도 실행됐습니다. 중단 후 명시한 mock/static node 10개만 검증했습니다. 연구·HPC 실행은 없습니다. 실제 process gate는 기존 CI 결과로 판단합니다.
- manual·patch notes를 포함합니다. 최종 head 필수 CI·동일 head 봇 완료·모든 head 미해결 P1=0 뒤 병합하고 #972에 반영합니다.

Refs #974. failed-job 재실행의 수집 오류도 수정했습니다.

- 같은 run/head의 유효한 Windows 실행과 원래 job·upload 시간을 대조해 artifact ID를 선택합니다. 누락·다른 출처·시간 불일치는 거부하며 coverage 검증을 유지합니다.
- 모든 재실행 run은 receipt 검증 뒤 reusable manifest를 발행하지 않습니다. Windows만 새로 실행한 경우도 포함합니다. 이전 실패 artifact는 후보에서 제외합니다. 관련 mock/static 611 passed, 실제 실패 run API에서 artifact 12615771377·attempt 1 선택을 확인했습니다.
- 최종 head CI·동일 head 봇 완료·미해결 P1=0 확인 뒤 #973→#972 순서로 병합합니다. #972 봇 대기는 #974 PI 예외이며 PASS로 기록하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #965. Refs #606; 잔여 직접 저장 경계가 있어 #606은 닫지 않습니다.

- request helper·SavedResults의 추가/`pop`/삭제·review·terminal 호출부를 async API로 전환했습니다. CLI·초기화용 sync API는 유지합니다.
- request별 lock 안에서 snapshot을 확정합니다. commit 뒤 변경 필드만 반영해 await 중 다른 변경을 보존하며 저장 오류는 호출자에게 전달합니다.
- 취소는 시작한 저장 결과와 후속 상태 적용을 확인한 뒤 전달합니다. CP4 receipt·terminal event transaction, round record, 재시작의 결과 추가/삭제를 보존합니다. group commit은 없습니다.
- storage 카드 선택은 receipt를 저장해 commit 뒤 취소돼도 같은 선택을 재처리합니다. 재시작은 저장된 선택으로 카드를 해결하며 다른 선택의 재전송은 거부합니다. 봇 P2와 semantics 제거 표식 누락을 수정했고 관련 mock/static 97개가 통과했습니다.
- 로컬 관련 mock/static **915 passed, 1 skipped, subtest 12 passed**. 지연 commit heartbeat·FIFO·실패 rollback·반복 취소·결과 삭제·CP4 결정 4종과 DB 재열기를 포함합니다. 실제 process 검증은 기존 CI 범위입니다.
- manual·Windows test inventory·patch notes를 함께 갱신했습니다. 최종 head 필수 CI·미해결 P1=0 확인 뒤 병합합니다. 봇 대기는 #974 PI 지시로 생략하며 봇 PASS로 기록하지 않습니다. runner/jobs·감시 도구·연구·HPC는 범위 밖입니다.

- #973 병합 main을 충돌 없이 반영했습니다. halt 진입 취소·resume·timeout과 async 저장 통합 mock/static 33 passed입니다. 별도 기능 수정은 없습니다.
- 후속 `a1a092b3`: 비용 commit 뒤 task 완료를 기록하고 quota 소비자 취소 뒤 저장을 await합니다. 기존 head 비용·재시작 실패와 barrier 비용·quota 실패를 재현했습니다. 재시작 검사 5건은 고정 polling 대신 완료 task를 기다리며 assertion을 유지합니다. 관련 5파일 최종 결과 합계 **172 passed, 1 skipped**(state 최종 60 passed 포함). DB 재열기·중복 합산 방지·unknown·다른 요청 카드 보존을 확인했습니다.
- 후속 `2bd3a7ab`: 새 봇 P1 4373460589의 저장 경계 2곳에서 수정 전 2 failed. 호출자 취소를 runner 중단·카드 정리·terminal 기록 뒤 전달합니다. 관련 lifecycle·active-cancel·persistence·task-cancel 95 passed, 기존 CI 실패 3건 3 passed. process-loss 복구 assertion은 유지하고 카드 준비는 완료 신호를 기다립니다.
- #972 hold 유지: 최종 head 필수 CI 성공·알려진 미해결 P1=0·충돌 없음 뒤 담당 병합합니다. #974의 PI 임시 리뷰 생략에 따라 별도 리뷰·총괄 재확인은 기다리지 않으며 봇 PASS로 기록하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #882. HANDOFF·manual의 지원/복구 안내와 AGENTS·CLAUDE·운영 기준·pi-qa의 역할/창구 안내를 최신 PI 인계에 맞췄습니다.

- [최종 통합 배정](https://github.com/ehojune/bioinfo-team-3d/pull/970#issuecomment-6407311846)에 따라 최신 main을 충돌 없이 반영했습니다. #973·#972·#975 병합과 #965·#653 CLOSED를 안내에 반영했습니다. #606 전체 완료·실제 browser·transport/OS 재시작·새 설치·반복 복구는 미검증이며 #882 OPEN·별도 실행 GO를 유지합니다.
- 이번 통합(LF 기준): HANDOFF 4,603→4,582자, manual 194,255→194,194자. 합계 82자 감소. README 한영판·직원 YAML과 대조했으며 다른 담당자의 async 저장·snapshot 안내를 보존했습니다.
- 현행 안내·변경 링크·UTF-8·integrations·public·diff·patch-notes·notes 검사 PASS. 문서 변경으로 로컬 runtime test는 실행하지 않았습니다.
- 최종 head 필수 CI·알려진 모든 head P1 수정검증·충돌/head guard 확인 전까지 hold입니다. PI 임시 리뷰 생략을 적용하며 새 봇 PASS로 기록하지 않습니다. 코드·설정·설치·배포·연구·HPC 실행은 없습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #653

현재 요청의 담당 단계가 지난 요청의 PI 검토 대기에 가려지던 직원 표시를 수정했습니다. CSO의 무조건 참여 예외를 없애고 검토 대기에 짧은 요청 제목을 붙였습니다. 2.5D·3D는 공통 state 판정을 쓰며 실행 task·승인·경고의 우선순위를 유지합니다.

- 변경: `state.js`, `office-art.js`, 회귀검사·visual baseline, manual.
- 재현: 새 Node 검사 수정 전 4 failed·1 passed. 봇 P1의 task 없는 답 대기와 P2의 direct·runner 대상·실패 snapshot·CSO mode 참여를 재현해 수정했고 관련 7개 파일 67 passed. 요청 전환·직원 제어 추가 22 passed.
- 검사: `git diff --check`, public 검사, patch-note·status 검사.
- Browser: Chrome desktop·phone 32 view PASS. baseline은 세 직원의 차례 대기 상태·문구와 chip 폭·위치 변경 60개만 반영했습니다.
- 한계: 3D는 공통 판정·표시 호출부 정적 대조이며 실제 browser QA와 운영 재현은 UNVERIFIED. 연구·HPC 실행 없음.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #675. runner 부재 중 취소를 복구한 runner에 재전송하는 통합 회귀검사를 보존했습니다. production 변경은 없습니다.

- 무해한 Python job 2개로 취소한 요청의 wrapper·자식 잔류 0, 완료 깨움 없음, 다른 요청 보존, 연결 실패만으로 취소하지 않음을 확인합니다.
- Windows CI mandatory node로 등록했습니다. 실제 프로세스를 쓰며 transport만 대체합니다.
- 관련 pytest: 4 passed, 27 deselected. Windows selection 계약·public·diff 검사 통과.
- CI inventory 예상 목록도 갱신했습니다. 해당 계약 pytest 3 passed.
- 실제 WebSocket 재연결·gateway OS 재시작·연구·WSL은 미검증입니다. #675의 WSL·진행률 범위는 남습니다. 필수 CI·동일 head 봇 리뷰 뒤 병합합니다.

Refs #606. publish 우선 단계이며 issue를 닫지 않습니다. 동기 helper·SavedResults 전환은 #965입니다.

- event·request 저장을 기존 FIFO SQLite writer에서 await합니다. loop에서 payload를 확정하고 writer에는 복사·직렬화한 값만 넘깁니다.
- 취소돼도 개별 commit의 결과·오류를 확인합니다. network는 취소 가능하며 pending delivery는 lock 반환 뒤 독립적으로 재개합니다. client 전송은 최대 10초, 취소·시간 초과 때 별도 1초 제한 close로 reconnect·DB replay를 유도합니다.
- 동기 checkpoint가 높은 seq를 먼저 반영해도 buffer는 최신 event를 seq 순으로 보존합니다. REST history·replay-gap snapshot도 WebSocket과 같은 ordering lock을 사용합니다.
- 번역·보고 소비자는 저장 완료 뒤 예약합니다. 두 요청의 login notice도 await 뒤 원자적으로 확인·예약해 중복을 막습니다.
- 검증: 관련 mock/static **292 passed**, 후속 delivery readiness 검사 **27 passed**. heartbeat, request 유무·idle/active lane, FIFO·snapshot, error·반복 cancel·AnyIO 취소 뒤 후속 동작 차단, backpressure·자동 delivery 재개·끊긴 client의 후속 전송 제외, runner cursor·reporter intent·DB 재개 읽기를 포함합니다. 기존 assertion·timeout은 유지했습니다. Windows inventory·public·diff·patch notes 검사 PASS.
- 초기 durable suite 검사 39 passed에는 실수로 로컬 crash subprocess 검사가 포함됐습니다. mock/static 한도를 벗어난 실행이며 이후 로컬 검사는 subprocess 없이 진행했습니다. 연구·HPC는 실행하지 않았습니다.
- manual·patch notes·동일 status 기록을 포함합니다. 실제 process 검증은 기존 CI 범위입니다. 최종 head 필수 CI·동일 head 봇 리뷰·미해결 P1=0 뒤 병합하며 동기 저장 경계 전체 해결·실디스크 성능은 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# runner state 소유 획득 경쟁

Refs #618. 서로 다른 설정의 runner owner 조회·기록을 state별 OS lock으로 묶었습니다. 실패한 획득자는 owner를 바꾸지 않으며 기록 실패도 시작을 막습니다. stale owner 복구는 유지합니다.

- 수정 전 경쟁 회귀 1 failed, 수정 후 `pytest tests/test_instance_guard.py -q` 37 passed. 별도 process의 lock 배제도 검사했습니다.
- 범위: 코드·임시 state 검사. 운영 인스턴스·HPC·재부팅 검사는 하지 않았습니다.
- 남은 조건: #618 P2 `broker_port` 사전 충돌 검사. CI·동일 head 봇 리뷰 뒤 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# #960 edition 검사 경계 수정

- 14차: access ledger의 11열 중 마지막 비고가 없는 9행을 재현했다. 정리본에서만 빈 비고를 추가하고 보정 기록을 연결한다. 다른 열 누락·초과는 차단한다.
- 17차: 근거 표의 MCP 도구명과 URL query key가 `__` 잔존으로 검출됐다. 생성한 표에서 기술 문자열을 inline code로 표시한다. 강조 오류 검사는 유지한다.
- 검증: 관련 pytest 119 passed. 저장된 14차 표 shape PASS, 17차 ledger 53행·전체 원고 render marker 0건. 입력 두 파일 SHA256 불변 확인.
- 봇 P1 4370698644: 같은 기술 문자열이 claim 각주에도 남는 경로를 재현하고 공통 formatter로 고쳤다. anchor와 URL query key를 함께 넣은 회귀 검사를 추가했다.
- ledger가 있으면 기존 생성 근거 표도 교체한다. 오래된 표가 남는 실패를 막고 원고 본문은 보존한다.
- 원자료·기존 결과·설치판·연구·HPC는 변경하거나 실행하지 않았다. 실제 edition 게시와 browser QA는 미검증이다.
- CI·최종 head 봇 리뷰는 PR에서 확인한다. Closes #960.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

요청 reference가 있는 모든 단계를 다시 실행하던 결함을 고쳤습니다. 파일·폴더 manifest를 실행 전후 gateway와 runner에서 대조하고 단계별로 보존합니다. 다음 라운드는 같은 입력·사양·도구·산출일 때만 재사용합니다. Closes #959. PR #962.

- 누락·변경·읽기 실패·legacy·runner receipt 불일치는 fail-closed입니다. CP1 카드에 재사용 거부 이유를 표시합니다.
- reference roots·restricted/private path guard를 유지합니다. 링크·hard link·특수 파일·scan 상한·256 MiB 초과와 URL reference는 재사용을 거부합니다.
- 회귀 재현: 수정 전 3 failed, 2 passed. 리뷰 수정 뒤 관련 mock/static·text encoding 검사 190 passed, 12 subtests passed. public·patch notes·notes index·Windows inventory 검사도 통과했습니다.
- bot P1의 파일 reference sibling 누락과 P2의 Windows/WSL 경로 불일치를 수정했습니다. 파일 reference는 runner가 노출하는 부모 폴더 전체를 hash합니다.
- Linux CI에서 발견한 CP2 카드 문구 순서 회귀를 수정했습니다. 기존 assertion을 유지하고 거부 이유 표시 검증을 추가했으며 관련 32개 test가 통과했습니다.
- 실제 연구·HPC·설치판 교체는 하지 않았습니다. gateway에서 원본을 읽을 수 없는 원격 입력과 디렉터리 산출의 재사용은 지원하지 않습니다. 경계 검사이며 실행 중 파일 잠금은 아닙니다.
- CI·최종 head bot review는 PR에서 확인합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #956

17차(`req_2fe54ec7dd`)가 연구와 보고서까지 마치고도 `the report anchors no claim while the ledgers hold citable claims` 한 번에 `report_incomplete`로 끝났습니다. 계획 검사(#948)와 달리 보고서에는 고칠 기회가 없었습니다.

- **교정:** claim 검사에 문제가 있으면 종합 단계(CSO)에 문제 원문, 인용 가능한 claim 목록(`step_id/claim_id`와 한 줄 요약), 고칠 보고서를 주고 "분석을 다시 하지 말고 결론마다 `[[claim:<step_id>/<claim_id>]]`만 달라"고 최대 2번(`REPORT_CORRECTIONS`) 되돌립니다(`report_correction`). 고친 보고서로 다시 검사합니다.
- **결과:** 통과하면 기존 성공 경로(`research_reported` → CP4)로 갑니다. 끝까지 실패하면 지금처럼 `report_incomplete`이고 첫 보고서가 아니라 마지막 교정본과 그 문제를 기록합니다. 교정 호출이 실패하거나 예산이 거부되면 각각 앞 보고서 유지·예산 거부 경로로 갑니다. 교정 횟수는 `research_contract.report_corrections`에 남깁니다(`report_check` 형식은 그대로).
- **종합 prompt:** 이미 인용 가능한 claim 목록을 처음부터 담고 있어 바꾸지 않았습니다(prompt 고정 hash 유지).
- **테스트(`tests/test_research_report.py`):** 앵커 없는 첫 보고서가 교정 1회 뒤 통과 / 교정 후에도 실패하면 `report_incomplete`(호출은 첫 보고서 + 교정 2회) / 두 번째 교정에서 통과, 새 문제 원문이 두 번째 prompt에 실림 / 첫 검사를 통과하면 교정 호출 없음. 기존 report 검사 테스트 포함 41건, 관련 묶음 1141건 통과(로컬, Windows, Python 3.12).
- **문서:** `docs/manual.md` 보고서 절.
- **미검증:** 실제 모델이 교정으로 앵커를 다는지(18차가 첫 근거).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

# 선행 조사 본문 전달 후속: 리뷰 P2 반영 (#951)

- #952가 병합 안전망으로 먼저 병합됐고(봇 P2만 남은 상태), 그 P2를 고친 push가 병합 뒤에 도착해 main에 들어가지 못했다. 같은 수정을 이 PR로 올린다(#950 때와 같은 경위).
- 복사 실패를 본문마다 격리한다. 한 본문의 바이트가 바뀌거나 한도를 넘어도 이미 복사한 나머지는 `inputs/precedent/`에 남는다(수정 전에는 batch 전체를 비웠다). `_copy`가 임시 파일을 지우므로 부분 파일은 남지 않는다.
- 앞 단계 산출 복사(`stage_inputs`)와 precedent 복사가 러너의 한 byte 예산(`input_hash_max_total_bytes`)을 공유한다. 두 함수에 예산 리스트(`remaining`)를 넘기고 기본값은 이전과 같다.
- 종합 안내는 "열렸다"가 아니라 "기록된 본문이며 러너의 경로·receipt·hash 검사를 통과한 단계에서만 열리고, 단계에 따라 못 읽었을 수 있다"로 쓴다.
- 리뷰 P1 반영: 본문별 격리 뒤, 실패한 복사가 임시 파일을 지우지 못해 inventory 밖 파일이 남았는데 다른 본문이 성공하면 해시를 통과하지 못한 바이트가 노출될 수 있었다. 복사 뒤 폴더에 복사한 본문과 inventory 외의 파일이 있으면 노출하지 않고 폴더를 비우며, 비우지 못하면 앞 단계 입력과 같이 `RuntimeError`로 단계를 시작하지 않는다.
- 리뷰 P2 반영: 디스크 여유 검사를 후보 전체 합계가 아니라 복사할 본문마다 한다. 한 본문이 크거나 byte 한도를 넘어도 들어가는 본문은 전달된다.
- 테스트 `tests/test_precedent_texts_951.py` 13건(새 4건: 한 본문만 깨지는 경우, 공유 예산, 임시 파일 정리 실패, 본문별 디스크 검사). 관련 350건 통과. 실제 요청, 전체 suite, Windows CI는 미확인이다.

Closes #954

15차 선례 조사에서 `fulltext`가 4번 모두 실패한 원인을 고쳤습니다.

- **원인:** `labhq/tools/annot.py`의 `request(response_limit=…)` 경로가 `aiter_bytes()`로 이미 풀린 바이트를 모은 뒤 원래 헤더(`Content-Encoding` 포함)를 붙여 `httpx.Response`를 다시 만들어, httpx가 한 번 더 풀다 `DecodingError`가 났습니다(`TransportError`가 아니라 잡히지 않아 같은 호출의 모든 ID가 함께 실패).
- **수정 1:** 다시 만드는 응답에서 `content-encoding`·`content-length`·`transfer-encoding`을 뺍니다.
- **수정 2:** `fulltext`는 ID 하나의 조회·본문 저장 예외를 그 ID의 `error`(`예외 종류: 설명`)로만 남기고 다른 ID는 정상으로 둡니다. 모든 ID가 실패할 때만 호출이 실패합니다.
- **테스트(`tests/test_annot_literature.py`, 수정 전 3건 모두 실패):** gzip `Content-Encoding` 응답을 합법 OA 사본으로 받아 본문 저장, Http 계층이 헤더를 뺌, 한 ID가 `DecodingError`여도 다른 ID는 `full_text`. literature·annot 묶음 117건 통과(로컬, Windows, Python 3.12).
- **문서:** `docs/manual.md` 본문 조회 항목.
- **미검증:** 실제 NEJM 논문 재조회, 15차 실운전.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

# 선행 조사가 저장한 본문을 연구 단계에 전달 (#951)

- 13·14차 모두 1차 연구 리뷰가 같은 P1으로 revise를 냈다. CP1 전 선행 조사(lit_scout)가 공개 본문을 저장했고 receipt hash도 맞는데, 실행 단계는 새 작업 폴더에서 돌아 그 파일을 보지 못했다. 단계는 다시 조회해 실패했고 종합 보고서는 "원문으로 확인한 수치 0건"이라고 적었다. 2차 라운드가 매번 필요했다(13차 $27.85 → $49.26).
- 원인: `fulltext_receipt`의 `text_file`은 선행 조사 작업 폴더(`.tmp/literature/`) 안 절대 경로이고, 그 파일을 실행 단계로 넘기는 경로가 없었다. 앞 단계 산출은 `upstream_artifacts`로만 `inputs/<step_id>/`에 복사된다.
- 수정:
  - `labhq/research/precedent_texts.py`(새): 저장 본문이 있는 receipt(경로·hash가 있고 hash 기준 중복 제거, 최대 4편)를 고르고, 단계 안내문과 보고서 작성자용 목록을 만든다.
  - CSO가 모든 연구 단계 task meta에 `precedent_texts`를 싣는다.
  - `runner/step_inputs.py` `stage_precedent_texts`: 경로가 작업 root 안의 보통 파일(링크 아님)이고 데이터 구역 밖이며, 본문 옆 `<text>.receipt.json`이 같은 논문·hash를 가리키고, 바이트가 선언한 hash와 같을 때만 `inputs/precedent/`로 읽기 전용 복사한다. 하나라도 어긋나면 그 본문만 사유와 함께 빼고 단계는 계속한다. 앞 단계 산출 복사와 같은 handle 기반 복사·디스크 확인·재시도 정리를 쓴다.
  - 단계 안내(`context`)에 "조회하기 전에 먼저 읽고 파일·줄로 인용"을 넣는다. 고정된 prompt 상수는 건드리지 않았다.
  - 종합 prompt에 요청 전체의 저장 본문 목록을 더해, 한 단계가 본문을 얻지 못한 것(단계 수준 공백)과 요청에 원문 자료가 없는 것을 구분하게 한다.
- 테스트 `tests/test_precedent_texts_951.py` 9건: 저장 본문 선택·안전한 파일명, 러너로 step을 실행해 `inputs/precedent/`에 바이트가 같게 복사되고 읽기 열림·안내가 붙는지, hash 불일치·작업 root 밖·receipt 불일치·상대 경로는 복사하지 않고 단계는 통과, 저장 본문이 없으면 동작 그대로, CSO가 meta와 종합 안내를 싣는지. 관련 741건 통과.
- 미확인: 실제 요청에서 단계가 이 본문을 실제로 읽고 인용하는지(15차), 선행 조사가 `.tmp/literature/` 밖에 저장한 본문, 전체 suite, Windows CI. 이 변경은 선행 조사 본문만 다룬다. 앞선 연구 단계 자신이 `.tmp/`에 저장한 본문(`lit_scout` 단계의 조회 결과)을 다음 단계로 넘기지는 않는다.

Refs #948 (후속: PR #949 병합 뒤 봇 P2 두 건)

- 깨진 초안(정수 `depends_on`, 해시 불가능한 id, dict가 아닌 단계)에서 `repair_synthesis_links`가 예외를 내면 계획 단계에서 요청이 죽을 수 있었습니다. 이제 그대로 돌려 CSO 교정으로 보냅니다.
- synthesis 단계가 여러 개이고 모두 불완전하면 첫 단계가 아니라 **빠진 갈래 수가 가장 적은 단계**(동률이면 첫 단계)를 채웁니다. 한 갈래가 여러 terminal로 갈라져도 갈래 하나로 셉니다(리뷰 반영).
- `docs/manual.md` 계획 검증 절의 "첫 synthesis 단계"를 위 동작으로 고쳤습니다.
- 테스트 6건 추가(`tests/test_plan_synthesis_948.py`, 모두 이 후속 변경): 가장 가까운 synthesis 단계 채우기 1건, 깨진 초안 그대로 돌려주기 4건(정수 depends_on·해시 불가능 단계 id·해시 불가능 갈래 id·dict 아닌 단계), 빠진 terminal 수가 아니라 빠진 갈래 수로 고르기 1건(이전 기준에서는 실패). `test_plan_synthesis_948`·`test_branched_research` 23건 통과(로컬, Windows, Python 3.12), 최종 head의 CI는 아래 PR에서 확인합니다.
- #949는 제가 고치던 중 병합 안전망이 먼저 병합해서 이 수정이 별도 PR입니다. #949에 병합된 핵심 수정(synthesis 보정·갈래 이름 synthesis 예외·교정 3회)은 그대로입니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

Closes #948

12차(`req_a52fd60207`)가 CP1 전에 "synthesis 단계가 갈래 끝을 받지 않음"으로 끝났습니다. 증거 DB의 두 계획(`task_72510404d7`·`task_8ffaef9bae`)에서 원인을 찾아 세 가지를 고쳤습니다.

- **결정적 보정:** `branch: "synthesis"` 단계가 있는데 첫 synthesis 단계가 각 갈래의 마지막 단계를 `depends_on`으로 받지 않으면, 검사 전에 labhq가 채우고 계획 `warnings`에 남깁니다. synthesis 단계가 없거나 채우면 순환이 되는 경우는 고치지 않습니다(`repair_synthesis_links`).
- **새로 찾은 원인:** 두 계획 모두 CSO가 `synthesis`라는 이름의 갈래를 함께 선언했습니다. 그러면 올바르게 연결한 첫 계획도 "synthesis가 자기 갈래의 끝 단계를 읽어야 함"과 "갈래 단계는 갈래 밖을 못 읽음" 두 규칙에 걸려 거절됩니다. synthesis를 예약 lane으로 보고 두 규칙에서 뺐습니다.
- **교정 3회:** 계획 교정을 1회에서 3회로(첫 계획 + 3회 = 4번), 매 회 남은 문제 원문을 줍니다. 3회 뒤에도 실패하면 보고서에 실제 횟수("3번 고친")를 씁니다.
- 테스트 `tests/test_plan_synthesis_948.py` 8건: 12차 모양 보정 후 통과, 기존 의존성 유지, 갈래 이름 synthesis 허용, synthesis 단계 없음은 보정 없음, 순환 방지, 두 번째 교정에서 통과해 CP1 생성, 3회 뒤 실패 보고, 보정만으로 교정 호출 없이 승인. 기존 3개 테스트의 시도 횟수를 2에서 4로 갱신했습니다. 관련 묶음 979건 통과(로컬, Windows, Python 3.12).
- 문서: `docs/manual.md` 계획 검증 절.
- 미검증: 실제 모델이 새 경로로 CP1 카드까지 가는지(13차가 첫 근거). 일반 lane의 교정 횟수(2회)는 바꾸지 않았습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

# 직원이 자기 outputs/에 쓰지 못함 (#946)

- 11차(`req_5aaf34eab7`)의 연구 단계 c1·r1이 `missing outputs`로 실패했다. 직원 settings(`permissions.deny`)에 작업 root(`runner.workspace_root`) 전체의 `Edit(…/**)`·`Write(…/**)`가 있었고, 거부 규칙은 허용 규칙(`Edit(//작업폴더/**)`)보다 우선하므로 자기 `outputs/`도 막혔다.
- 원인: 선행 단계 산출을 읽기 전용 `inputs/`로 받는 경로(#791, `artifact_contract`, `upstream_artifacts`가 dict면 빈 dict여도 해당)가 `private.paths`에 작업 root 전체를 추가한다(`runner/daemon.py`). `claude_deny_private`는 "열린 읽기 폴더를 품은 개인 경로"를 모두 직원 Claude 설정 폴더처럼 다뤄 전체 경로에 Edit/Write 거부를 붙였다. 설정 폴더에는 맞지만 작업 root에는 작업 폴더가 들어 있어 틀리다. `.labhq-system-ca.pem`이 root에 있고 `.credentials.json` 등 이름이 Read 거부에 붙은 것도 같은 경로로 설명된다. 처음 이슈의 "root를 Claude 설정 폴더로 오인"이라는 추정은 맞지 않고, root가 일부러 개인 경로가 된 경우의 규칙 모양 문제다.
- 수정: `claude_deny_private`에 `write_roots`를 받아, 작업의 쓰기 폴더를 품은 holder는 Edit/Write 거부를 전체 경로가 아니라 작업 폴더로 가는 길에 없는 최상위 항목(`closed_entries`)에만 붙인다. 쓰기 폴더가 없는 holder(직원 Claude 설정 폴더)는 그대로 전체 경로 거부다. 러너는 `write_roots=[작업 폴더]`를 넘긴다. 같은 날짜 폴더 안의 다른 단계 폴더는 이전처럼 거부 규칙이 아니라 승인 게이트가 막는다.
- 리뷰로 별칭 작업 root(symlink·junction)를 추가로 닫았다. 개인 경로는 실제 root인데 작업 폴더는 별칭 표기라 쓰기 holder로 못 잡던 것을 쓰기 폴더의 원래·절대·실제 경로를 모두 대조하도록 고쳤고(`claude_deny_private`), 같은 이유로 읽기 거부도 작업 폴더를 열어 둔다. 러너가 artifact contract로 더하는 `inputs` 경로는 실제 경로 표기로도 넣어, 별칭 root에서 실제 inputs 경로가 쓰기 허용에 걸려 조상 단계 복사본이 고쳐지는 일을 막는다.
- 같은 부류의 표기 우회(대소문자만 다른 `INPUTS/`)는 deny 표기를 늘리지 않고 구조로 닫았다. artifact contract 단계의 Claude 쓰기 허용(`--allowedTools`의 Edit 규칙)을 작업 폴더 전체가 아니라 `outputs/`와 `.tmp/`로 좁혀, 그 밖의 쓰기는 사전 허가 없이 승인 게이트로 간다(게이트는 대소문자를 접어 개인 경로를 판정한다). 이 단계의 다른 쓰기가 필요하면 게이트를 거친다.
- 테스트 `tests/test_workspace_write_946.py` 8건(수정 전 실패 확인: 정책 단위 2건, 러너로 step을 실행해 생성된 settings를 검사하는 2건, junction 정책 1건, junction 러너 1건): 작업 폴더 `outputs/`·`.tmp/`에 거부가 없고 root의 나머지 최상위 항목은 닫혀 있음, 직원 Claude 설정 폴더는 전체 경로 거부 유지, junction root에서 outputs는 허용·inputs는 두 표기 모두 거부. 관련 355건 통과.
- 미확인: 실제 Claude CLI가 이 규칙으로 쓰기를 허용하는지(실제 요청), 전체 suite, Windows CI. 12차에서 확인된다.

## #944 요청 예산이 있으면 CP1 뒤 집행 상한은 요청 예산 (base 634c3fca)

- 바꾼 것: `labhq/orchestrator/cso.py` `_research_budget`. 요청에 PI 예산(`budget_usd`)이 있으면(source `request`) CP1 승인 뒤 `cap_usd`는 그 요청 예산이고, 계획 `budget_usd`와 쓴 비용의 합은 `detail.budget`의 `plan_budget_usd`·`spent_before_plan_usd`와 카드 끝줄(`집행 상한 $40.00(요청 예산 그대로). 계획 추정은 참고만 합니다: …`)에 참고로만 남는다. 요청 예산이 없는 요청은 이전 그대로(정책 `per_request_usd`를 넘지 않는 범위에서 합이 상한). 직원 prompt의 상한 문구와 계획 prompt의 `budget_usd` 설명도 맞췄다. manual 예산 절 갱신.
- 근거: 10차(`req_fbaa5e34cf`) 요청 $40, 쓴 돈 $2.70, 계획 $7 → 상한 $9.70. 건당 `per_task_usd` $10 예약 두 건이 겹쳐 예산 카드 두 장, 실제 지출은 $2.98.
- 검사: 새 test 2건(`tests/test_research_notes_budget.py`: 10차 수치로 예산 카드 0장·상한 $40, 묶인 금액이 $40을 넘는 작업은 카드를 띄우고 보내지 않음). 기존 3개 test 파일(`test_research_notes_budget`·`test_cso_scope_estimate`·`test_research_cp2_revise`)은 "요청 예산 10" 기본 fixture를 정책 상한으로 바꾸거나(`_lab_cap`) 새 의미로 단언을 고쳤다(약화 아님: 요청 예산이 없는 경로의 단언은 그대로). 관련 test 300건대 통과.
- 한계: 실제 연구 실행으로는 확인하지 않았다. 요청 예산을 `--budget`으로 적은 PI의 의도가 "계획 추정 이하"인 경우는 없다고 보고 요청 예산을 상한으로 삼았다.

# PI 문구 번역 실패 원인과 수정 (#942)

9차(`req_a81a64ab29`) 번역 기록을 읽기 전용으로 대조했다. 18건이 모두 "번역 못 함"이었고 원인은 세 가지가 겹쳤다.

| 원인 | 근거 | 수정 |
|---|---|---|
| 번역 prompt가 스스로 형식 검사를 어김 | 끝난 답 3건(`pi_text_2`·`131`·`133`)이 `**무엇을…**`처럼 머리말과 줄바꿈이 있는 여러 문단이었고 검사(두 문장 이내, 줄바꿈도 나눔)에서 8~21조각으로 탈락. prompt에 붙은 공용 `PI_TEXT_RULE`이 "무엇을→왜→결과 순서, 목록은 줄바꿈"을 요구해 검사와 충돌. Haiku로 같은 원문 3건을 직접 돌린 결과: 기존 prompt 두 번 반복에서 3건 중 각 2·1건 통과, 새 prompt 두 번 반복에서 3건 중 각 2건 통과(탈락은 원문의 파일 경로를 복사한 경우) | 번역 prompt에서 `PI_TEXT_RULE`을 빼고 "줄바꿈 없는 한 문단·두 문장 이내, 머리말 금지, 파일 이름·경로 풀어 쓰기"를 직접 적음 |
| 번역이 연구 단계 슬롯 뒤에 줄을 섬 | `pi_text_20`·`34`는 `queued`에서 30.7초·30.2초에 `cancelled`(`working` 없음). runner `max_parallel=1`이고 번역도 같은 슬롯을 씀. 두 건 모두 시작 전 취소되어 `outcome_unknown` | `pi_interpretation`을 상담(consult) 슬롯에서 실행 |
| 비용 모름 건의 모델 칸이 직원 기본 모델 | 두 unknown 건이 claude-opus-5-5로 기록(실제 호출 모델은 haiku). 시간 초과 경로가 결과 없이 `outcome_unknown`을 기록하며 직원 모델을 채움 | 그 경로에서 번역에 설정한 모델을 기록 |

- 나머지 13건은 호출 자체가 없었다. 위 unknown 두 건이 건당 $10(`per_task_usd`)으로 묶여 `_diagnosis_budget_available`이 거짓이 됐기 때문으로 보인다(#941이 번역 unknown을 자기 상한만 묶게 바꿈). 이 13건의 건별 조기 반환 사유는 로그에 남지 않아 확인하지 못했다.
- 시작 전에 취소돼 비용이 실제로 0이었을 가능성이 높지만 runner가 "시작 전 취소"를 구분해 알려 주지 않아 `unknown`을 확정 비용으로 바꾸지는 않았다. 이 구분을 기록하려면 runner 결과에 시작 여부를 싣는 변경이 필요하다(범위 밖).
- 테스트 `tests/test_pi_translation_942.py` 4건, 관련 242건 통과. 실제 요청·전체 suite·Windows CI는 미확인.

# 예산 카드: 증액 한 번으로 끝나게 (#940)

- 시험 ② 8차·9차가 모두 예산 카드 두 장으로 끝났다(9차: 21:04:46 첫 카드 승인 → 곧바로 두 번째 카드 → 취소, 비용 $2.88).
- 원인 1: 승인한 새 상한이 `max(limit*2, bound + needed)`였다. 첫 카드의 `needed=0`이면 새 상한이 정확히 지금 묶인 금액이라, 다음 task가 예약(`per_task_usd`)을 잡는 순간 다시 초과했다. 보내기 전 예약을 켠 요청은 이제 `bound + max(needed, 다음 task 예약)`을 쓴다. 카드 문구의 "승인하면 상한을 $X로"도 같은 값이다. 예약을 끈 요청은 그대로다.
- 원인 2: 취소된 요청의 PI 번역 task 2건이 `outcome_unknown`으로 남아 건당 $10(`per_task_usd`)씩 묶었다. `_dispatch_held`가 번역의 예약 상한(ceiling $0.05)이 일반 예약보다 클 때만 더했다. 이제 일반 예약을 번역의 ceiling으로 바꿔 계산한다(위·아래 모두).
- 9차 DB(`req_a81a64ab29`, 읽기 전용): 번역 18건은 모두 "번역 못 함"이고, `outcome_unknown` 2건의 모델이 claude-opus-5-5로 기록됐다. 같은 요청의 정상 번역은 haiku다. unknown 건의 모델 칸이 직원 기본 모델로 채워진 것으로 보이지만 확정하지 못했다. unknown의 원인(시간 초과·취소·결과 누락)도 이 기록으로는 확정할 수 없다.
- 테스트 `tests/test_budget_headroom_940.py` 5건(수정 전에는 4건 중 3건이 실패). 관련 441건 통과. 실제 요청·전체 suite·Windows CI는 미확인.

@codex-tech-lead #929 병합 뒤 #930 branch에 최신 main을 통합했습니다.

- 보존 clone/venv `a5fd6bed`·Python 3.14.7에서 gateway·runner 강제 종료 복구 각 1회 PASS. 설정·freeze hash는 W2 인계와 일치합니다.
- 두 case 모두 재개 승인 1회, 완료 plan/s1 재실행 0, 같은 task 중복 0, 비용·원장 $0.04, 전후 contract 일치, 잔류 0. 조회는 각각 2·3회입니다. 상세 hash와 한계는 [receipt 대조표](../reference/recovery-stop-924.md)에 남겼습니다.
- 최신 main 통합 뒤 consult/recovery 회귀 46 passed. public·diff·patch-notes·notes 검사 PASS. reference 5,303→6,465 bytes; manual 추가 변경 없음.
- 최종 head CI·봇 검토·#930 병합은 대기 중입니다. 실제 모델·연구·HPC·독립 hard-stop·공급자 청구는 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #938

contained runtime 확인(`doctor --runtime`의 `contained_verified`)이 owner receipt 수명을 발급 후 5400초(90분)로 고정해 15시간 연구 실행(#910 6차)을 막았습니다. 그 상한을 설정값으로 만들었습니다.

- 설정 `runner.contained_max_s`: 기본 5400(기존 동작 유지), 하한 60, 상한 86400. 범위 밖 값은 설정 로드 때 거부합니다. `labhq/runtime_readiness.py`는 `deadline <= issued + contained_max_s`로 검사합니다.
- 다른 확인 조건(nonce·provenance·config hash·pipe·운영자 pin·owner 응답 마감 일치)은 건드리지 않았습니다.
- **발급 쪽:** receipt를 발급하는 contained owner는 이 저장소 밖(운영 harness)에 있어 코드로 맞추지 못했습니다. 15시간으로 쓰려면 owner도 같은 값(예: 54000 이상 설정값)으로 발급해야 하고, 아니면 이 검사가 거부합니다.
- 테스트(`tests/test_contained_readiness.py`): 기본 5400 통과·5401 거부, `contained_max_s=54000`에서 54000 통과·54001 거부, 86401·59·0·-1 설정 거부. 수정 전 코드에서는 54000 경계 테스트가 실패하는 것을 확인했습니다. 같은 파일 56건 통과(로컬, Windows, Python 3.12).
- 문서: `docs/manual.md` doctor 절, `docs/reference/contained-runtime.md`.
- 미검증: 실제 15시간 owner·새 설치판·종료 증명. `tests/test_doctor.py::test_doctor_warns_about_parent_session_markers_and_pi_skills`는 이 환경에서 main에서도 실패합니다(무관).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## #675 긴 계산을 로컬 job으로 (base a94c11d8)

- 바꾼 것: `labhq_job` MCP(`labhq/tools/local_job_mcp.py`: `job_submit`·`job_status`·`job_list`·`job_cancel`), runner broker `/localjobs/{op}`, `labhq/runner/local_jobs.py`(wrapper가 종료 코드를 `exit` 파일에 남기고 pid+생성 시각으로 식별, 로그는 runner 상태 폴더), `daemon.py`의 `_on_local_job`·`_submit_local_job`·`_end_local_job`·`_cancel_local_jobs`. 명령은 runner가 `evaluate_tool`(승인 gate 규칙)로 직접 판정하고 ask면 같은 `tool_permission` 카드를 연다. job은 기존 `self.jobs` 행·`_poll_jobs`·`jobs.finished` 경로를 써서 HPC job처럼 단계를 대기시키고 깨운다(HPC watcher와 별도의 로컬 watcher가 첫 job부터 돈다. `scheduler: none`일 때 HPC watcher를 예약하지 않는 기존 계약(`test_scheduler_none`)은 그대로). `task.cancel`·새 메시지 `task.jobs_cancel`(gateway `cancel_request`가 대기 중인 단계에 보냄)로 프로세스 트리를 끝내고 단계를 깨우지 않는다. runner 재시작 뒤에는 pid+생성 시각이 같으면 이어받고 `exit` 파일이 있으면 그 결과를 읽는다.
- 떼어 내기 거절: `labhq/detached_command.py`(`nohup`·뒤쪽 `&`·`setsid`·`disown`·PowerShell `Start-Process`(-Wait/-Verb 없이)·`Start-Job`)를 `evaluate_tool`이 카드 없이 deny하며 `job_submit`을 안내한다. 직원 규칙 문구와 Codex 도구 승인 목록에 `labhq_job` 추가, analyst·bioinfo-agent(Claude 직원)에 `job` 부여(Codex 직원은 OS sandbox 밖에서 도는 job이라 거절), manual 갱신.
- 검사: 새 test 14+35건(실제 프로세스 사용: 제출·완료·실패·취소 시 잔류 0·재시작 이어받기·exit 파일·broker 토큰·gateway 취소 전달·떼어 내기 거절), 수정 전엔 새 모듈이 없어 수집 실패. 관련 4300건대 통과(테스트가 고정한 `bioinfo-agent` builtin 목록은 `job` 추가를 반영).
- 한계: WSL runner는 범위 밖. Claude가 미리 허용한 셸 명령(예: `python *`)은 gate를 거치지 않아 `&` 거절이 모든 경우를 막지는 못한다. runner를 끄면 job은 계속 돌고 다시 켜야 이어받는다(안 켜면 남음). runner가 없는 사이의 request 취소는 job에 전달되지 않는다. 진행률의 작업판 표시는 job 상태 이벤트(`job.submitted`·`job.state`)까지이고 별도 단계 흐름 줄은 만들지 않았다. 실제 모델이 도구를 쓰는지는 확인하지 않았다.

#922 설치 검사기 manual의 보존 옵션 안내를 보충합니다. #925는 이미 병합됐으나 이 단락은 예전 문구가 남아 있어 후속 수정합니다.

- 기본 호출의 자동 삭제, `--work-dir`·`--receipt`, 설치판·분리 로그·JSON의 로컬 전용 증거 경계를 한 단락에 반영했습니다. 나머지 manual은 변경하지 않았습니다.
- 검증: `git diff --check`, `python scripts/patch_notes.py check`, Git Bash `scripts/check_public.sh` 통과. 실행 코드 변경이 없어 설치 재실행은 하지 않았습니다.
- manual LF 기준 187,844→188,073자(+229). 최종 head CI·봇 검토 후 병합합니다.

Refs #922, #925.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #934

통역의 task별 예약을 dispatch 전에 보존하고 request bound와 content-free projection에 포함합니다. 동일 task의 연구 hold와 한 번만 합산하며 orphan 예약은 누락 task로 표시합니다. 기존 aggregate에서 task별 예약을 복원하지 않습니다.

- 관련 회귀: `pytest tests/test_cso.py tests/test_pi_language.py tests/test_task_attempt_projection.py tests/test_resume_cp1_654.py tests/test_followup.py -q` → 409 PASS.
- `scripts/check_public.sh` PASS. CI·봇 review는 PR 최종 head에서 확인합니다.
- 설치판 반영·통합 재고정은 별도 조정 대상입니다. 실제 최초 취소 분기는 UNVERIFIED입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #931

Windows 검사의 병렬 단계가 6분 제한에 반복해서 걸려 무관한 PR(#915·#919·#930)을 막던 문제를 측정에 맞춰 풀었습니다.

| 측정 (최근 test.yml 99 run, pytest-windows) | 값 |
|---|---|
| 병렬 단계, 성공 74회 | 중앙값 264초 · p90 319초 · 최댓값 350초 |
| 6분(360초) 제한에 걸린 실패 | 8회(361·361·367·360·343·367·375초 등, 하나는 1080초) |
| 성공 job 전체 | 중앙값 378초 · 최댓값 448초, serial critical 최댓값 79초 |

- 병렬 단계 6분→9분(성공 최댓값 350초의 1.5배 이상), job 10분→14분(setup + critical 3 + 병렬 9 + report/upload에 여유). 테스트의 assert·timeout은 그대로입니다.
- 병렬 단계 시작 시각을 기록하고 `always()` 단계가 소요를 job summary(`### Windows pool step`)에 남깁니다. 성공 시 `summary.json`의 setup/pytest 시간은 기존 그대로입니다.
- 10분 목표는 `over_ten_minutes`로 계속 잽니다. 제한이 늘어도 느려진 날을 숨기지 않습니다.
- 정책 고정 테스트 `test_ci_windows10m`·`test_ci_windows_phases`의 6/10을 9/14로 갱신했고 `docs/reference/ci-reuse.md`에 반영했습니다. CI 정책 테스트 526건 통과(로컬).
- 미검증: 제한을 올린 뒤의 실제 run(이 PR의 CI가 첫 근거), 느려진 근본 원인(Windows runner 변동·WMI/Chrome 시작 지연 가설은 확정하지 못함).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

@codex-tech-lead

- #928: `Hub.adopt_consult()`에서 기존 helper로 interrupted 결과를 제외합니다. 마지막 허용 attempt에서도 같은 번호의 새 task로 상담을 이어 갑니다.
- 기존 consult 전용 회귀에 종료 전/대기 중 결과·재연결·새 task ID·세션 격리·known/unknown 비용 보존을 추가했습니다. live 채택·성공·일반 실패·시설팀 자동 재실행 금지를 확인했습니다. CSO·attempt 상한·기한·비용·HTTP·예약 정책 변경은 없습니다.
- 수정 전 9 failed·1 passed, 수정 후 관련 회귀 80 passed. Python 3.10 AST·public(LF 정규화한 검사 사본을 Git Bash로 실행)·diff 검사 PASS입니다.
- 최종 head CI·봇 검토·병합은 아직 미확인입니다. 실제 모델·연구·HPC·B/c3 실행은 없습니다. #924의 같은 설치판 최종 receipt는 별도입니다.

Closes #928.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# 반복 fake 최종 검증 (#923)

PR #929: 선행 #925·#932를 포함한 main 통합 뒤 공백 Windows 경로의 새 설치와 동일 fake 2회가 PASS했습니다. Closes #923.

- shutdown 뒤의 최종 Audit counter도 성공 조건으로 검사하고 종료 receipt를 대조합니다.
- 설치 commit `a5fd6bed`, Python 3.14.7. 두 요청 모두 task 10개 완료·비용 $0·동일 산출 hash·근거 연결을 확인했습니다. 개별 receipt·비교 JSON·의존성 목록을 보존했습니다.
- shutdown 뒤 두 Audit counter 모두 0, process exit 0, 잔류 자식 0입니다. 반복·설치 회귀 75 passed; public·diff·patch-notes·notes 검사 PASS입니다.
- 같은 clone/venv를 W3 복구 검증용으로 보존합니다. 최종 head CI·봇 검토가 끝나기 전까지 hold를 유지합니다.
- 실제 모델 연구·외부 HTTP 조회·HPC·B/c3·provider billing·독립 hard-stop 집행은 미검증입니다. [검증 계약](../reference/repeat-fake-923.md). Refs #923.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

@codex-tech-lead

# 강제 종료 복구·중단 조건 (#924)

- runner 재기동 뒤 저장된 interrupted 결과를 다시 반환해 두 번째 resume 카드가 열리던 결함을 고쳤습니다. 성공 결과·live task 재채택은 유지하고 남은 단계만 새 task ID로 실행합니다. 기존 비용·조회 기록을 지우지 않습니다.
- 시험 소유 handle로 gateway/runner 각각을 강제 종료·재기동했습니다. 승인 1회, 완료 s1 실행 1회, 같은 task 중복 실행·수락 0, 비용 $0.04와 원장 일치, 조회 각각 2·3회, 잔류 0입니다.
- 관련 회귀 52 passed, live adoption·비용 회귀 13 passed, 강제 종료·receipt 변조 거부 2건과 source 판본 거부 2건 PASS. direct checkpoint 단독·unknown 비용 기록도 확인했습니다. Windows inventory에 두 검사를 등록했고 CI selection 회귀 85 passed입니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 PASS.
- manual 중단 조건을 추가했습니다: 186,764→187,844자(+1,080). [코드 대조·재현·한계](../reference/recovery-stop-924.md).
- 봇 P2의 새 task 비용 행·합계 동시 누락을 재현했고 모든 실행·수락 task의 known 비용 행과 raw 원장 재합산을 요구합니다. kill 전 완료된 plan·s1의 identity와 단일 실행도 검사합니다.
- 다른 checkout·다른 parent 제품 import는 출력·process 생성 전에 거부합니다.
- #926 main 통합 `36b19cbd`에서 W2 fixture 재사용과 복구·source 거부 4건 PASS를 확인했습니다. #925 병합 뒤 같은 설치판 최종 재현, 최종 head CI·봇 검토·모든 head 미해결 P1=0이 남았습니다. Refs #924.
- 실제 모델·외부 서비스·HPC·PI 인스턴스·B/c3 proof는 실행하지 않았습니다. driver deadline은 제품의 요청 전체 독립 hard-stop 증명이 아닙니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# 같은 fake 요청 2회 계약 (#923)

새 설치 helper를 재사용해 실제 gateway/runner의 동일 fake 요청 두 번을 검사합니다. 선행 PR #925 병합 뒤 최신 main에서 새 설치 최종 재현이 남았습니다. Refs #923; Depends on #925.

- 각 run의 state·task·artifact를 분리하고 비용·시간·산출 구조·근거 연결을 확인합니다. receipt·비교 JSON·stdout/stderr·종료 뒤 잔류 검사를 보존합니다.
- 관련 pytest 29 passed. 실제 gateway/runner 두 실행과 이전 artifact 시각·산출 누락 거부를 포함합니다. 실패 CLI exit 1·`pass=false`, Python 3.10 문법·public·diff 검사 PASS.
- 초기 CI의 binary log 인자·Windows inventory 실패를 고쳤습니다. 새 module 전체를 Windows 필수 cohort로 등록했고 encoding·CI policy 관련 pytest 79 passed입니다.
- 상세: [검증 계약](../reference/repeat-fake-923.md). README·manual·제품 코드는 수정하지 않았습니다. 실제 모델·외부 HTTP·HPC·B/c3·provider billing·독립 hard-stop 집행은 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# Windows 새 설치·인증 경계 (#922)

- 설정·API token·Git/pip override를 격리하고 공백 경로의 launcher와 실패 receipt를 고쳤습니다. 단계별 PASS/FAIL/SKIP·분리 로그·설치 commit·의존성 판본을 보존합니다.
- Windows 공백 경로, 설치 코드 `9c4f0e2d`, Python 3.14.7: 29 PASS·0 FAIL·1 SKIP. Git·pip override를 상속시킨 mock `req_2f80074c0d` verify exit 0, 최종 자식 잔류 0, 보호 경로 mtime 유지.
- 관련 pytest 83 passed. Python 3.10 문법·public·diff·patch-notes·notes 검사 통과.
- README 한국어 11,852→12,205자(+353), 영어 17,866→18,156자(+290). [공개 근거](../reference/clean-install-922.md). 상세 receipt·freeze·로그는 로컬 작업 폴더에 보존합니다.
- 사람 인증·실제 모델은 SKIP. 웹 데모·open·wheel 설치 실행·실제 연구·HPC·B/c3는 미검증입니다. 최종 head CI·봇 검토 뒤 병합합니다.
- manual은 #922 소유권 지시대로 수정하지 않았고, `docs/manual.md:109`의 보존 옵션 보충 위치를 총괄에게 보고했습니다(W3 소유).

Closes #922

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #678 task 시작 때 디스크 여유 확인과 bulk_data_root (base c075b416)

- 바꾼 것: `Runner._disk_gate`가 task를 시작하기 전, 작업 폴더를 만들고 실행 슬롯을 잡기 전에 이번 실행이 쓰는 작업 폴더(재개·재시도의 다른 드라이브 포함) 드라이브의 여유를 읽는다. `runner.disk_fail_free_gb`(기본 2 GiB, 0이면 끔) 아래이면 시작하지 않고 `disk_space` 카드(PI 결정 대기)로 묻는다: 승인하면 시작, 거절·24시간 무응답이면 이유를 남기고 시작하지 않는다(카드 시간 제한 24시간, 웹은 그동안 PI 결정 대기로 보여 줌). 경고 기준(10 GiB·5%) 아래이면 작업판 경고만. 드라이브를 읽지 못하면, 환경 자동 수정(`facilities_fix`)이면 막지 않는다. 선택 설정 `runner.bulk_data_root`가 있으면 `_bulk_data_hint`가 직원 브리핑(task context)에 "큰 원자료는 이 폴더 아래에" 한 줄과 여유 공간을 넣는다. 통제 구역에 걸치면 브리핑에 쓰지 않고 경고를 남긴다. 카드 종류 `disk_space`를 웹 라벨·`pi_text`에 추가(runner가 만든 카드라 gateway의 PI 결정 시간 제한 경로는 쓰지 않는다). manual·예시 설정 두 사본 갱신.
- 정책: `bulk_data_root`는 작업 폴더 밖이라 새 쓰기 허가를 주지 않는다. 쓰기는 기존 승인 판정을 그대로 거친다(정책 엔진은 바꾸지 않았다).
- 검사: 새 test 10건(수정 전 대부분 실패)·웹 1건. 관련 455 passed.
- 한계: 범위 밖으로 둔 것 — 큰 다운로드·압축 해제 직전 검사, doctor의 fail 단계(2 GiB). 실제 디스크·실운영 확인 없음(여유 값은 monkeypatch). `tests/test_doctor.py::test_doctor_warns_about_parent_session_markers_and_pi_skills`는 이 세션의 환경 변수 때문에 로컬에서 실패하며 이 변경과 무관하다.

## #676 외부 응답 대기 경과 표시·상한 경로 점검 (base b09b6994)

- 바꾼 것: 공개 조회가 기다리는 동안의 진행 알림(`agent.log`의 `external_lookup`, 60초 간격)을 gateway가 task 활동 `external_wait`로 남기고(끝·시간 초과·실패면 지움), 웹 직원 카드가 `외부 응답 대기 n분째 / 상한 20분`을, `labhq status`의 단계 줄이 같은 문구를 보인다(`step_waits`, 마지막 알림 뒤 경과 시간을 더하고 상한을 넘지 않게 표시). manual의 20분 상한 절 갱신.
- 경로 점검: `labhq_annot`의 모든 `@server.tool`(variant·regulatory·문헌·`key_fulltext`·AlphaGenome)은 `_run`/`_run_literature`/`_run_key_fulltext`를 거쳐 `run_lookup`(20분)에 닿는다. 빠진 경로는 없어 연결한 것은 없고, 우회하는 새 도구가 생기면 실패하는 AST test를 추가했다. gateway의 출처 실시간 확인은 별도 상한(요청 10초·보고서 60초, 꺼짐이 기본)이다. HPC job 도구와 임의 셸·curl·타사 MCP는 이번 범위 밖.
- 검사: 새 test 4건·웹 1건, 수정 전 코드로 수집 실패·웹 1건 실패, 수정 후 관련 229 passed·1 skipped·web fail 0.
- 한계: 실제 95분 사례와 실제 UI·`labhq status` 화면 재현은 하지 않았다. 표시는 60초 간격 알림에 의존한다.

## #891 출처 실시간 확인 조회도 요청 HTTP 상한에 계수 (base 01b100ad)

- 바꾼 것: `LiveSourceResolver`(`labhq/evidence/verify.py`)가 매 HTTP 요청(재시도 포함) 전에 `LookupAdmission.admit`을 부른다. `annot.request_lookup_limit`과 같은 runner 상태 폴더 SQLite 파일·같은 요청 단위 카운터를 쓴다(`admission_for`). 거절되면 보내지 않고 `LookupFailed("lookup_limit")` → `requires_verification`(확인 못 함), `not_found`와 구별. 주 조회가 통과한 뒤 보강 요청이 거절되면 found 기록을 유지하고 `enrichment_failures`에 남는다. gateway(`cso.py`)와 `labhq verify`(`audit.py`) 두 곳에 연결. 상한이 없으면 세지 않고 파일도 만들지 않는다. manual 갱신.
- 검사: 새 test 6건(수정 전 수집 실패), 관련 348 passed·2 skipped, 조회 상한 test 44 passed.
- 한계: 같은 설정을 쓰는 PC에서만 runner와 한 파일을 센다(runner가 다른 PC면 각자). `live_source_check` 기본값(false)과 ② 설정은 바꾸지 않았다. 실제 Crossref/NCBI 호출·gateway 실행은 확인하지 않았다(fixture 전송으로만 검증).

## #670 논문 스킬: 초록뿐인 논문 생략·메타분석 스킬의 추출 단계 부착 (base f65dcf5c)

- 대조: 본문 receipt 없이 스킬을 만들지 않는 것은 #830으로 이미 있었다. 이번에 사유를 밝히고(초록만 확인/접근 확인 못 함 + 계획 참고로만) 메타분석 부착을 더했다.
- 바꾼 것: `labhq/recruit/paper_skills.py`의 `no_fulltext_reason`·`cited_access`·`extraction_steps`. 메타분석 제목이면 계획의 `lit_scout` 단계를 `step_ids`·`agent_ids`에 더하고 `extraction_step_ids`를 남긴다. manual 논문 스킬 절, Windows 경계 목록에 새 test 등록.
- 검사: 새 test 5건(수정 전 수집 실패, 수정 후 통과), 논문 스킬 관련 187 passed.
- 한계: 메타분석 판정은 제목 문구(영·한)만. 초록만 확인 판정은 선행 연구 인용의 `access_level=` 문구에 의존. #906(PR #909)이 같은 파일을 바꾸므로 병합 순서에 따라 rebase한다. 실제 모델 계획에서의 동작은 확인하지 않았다.

Closes #669

직원이 받은 자료를 읽으려고 작업 폴더 안에 패키지를 설치하는 것을 계획이 막지 않게 하고, 읽을 도구가 끝내 없으면 CP2에서 보이게 했습니다.

- 계획 prompt: 작업 폴더 안 설치(`./.pylib`, 작업 폴더 venv, `./.rlib`, 판본 고정)는 기본 허용이며 `resource_limits`에 "새 패키지 금지"를 쓰지 않습니다. 시스템·공유 환경 변경만 금지합니다.
- 단계 prompt·작업 폴더 규칙: 필요한 패키지를 승인 없이 `./.pylib`에 설치해 읽습니다. 끝내 읽을 도구가 없으면 `status_detail`이 `도구 없음:`으로 시작하는 `unavailable` 근거 행을 남기고, 그 줄이 CP2 경고에 올라옵니다(`tool_missing_lines`).
- 정책 게이트는 이미 `--target ./.pylib` 설치를 허용하고 공유 환경·작업 폴더 밖 설치를 거부합니다(기존 테스트와 새 테스트로 확인). 설치한 패키지·판본 기록 규칙(`outputs/env/<step id>.txt`)은 기존 그대로입니다.
- 테스트: `tests/test_task_local_install_669.py` 4건, 관련 묶음 590건과 352건 통과(로컬, Windows, Python 3.12).
- 미검증: 실제 요청에서 모델이 새 규칙을 따르는지, 웹 CP2 카드 표시.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

@codex-tech-lead 보고서와 정리본에서 주장별 원문 위치·접근 상태·최초 실증 출처를 함께 확인합니다. Closes #907.

- 기존 claim anchor·출처 링크를 유지하고 미해결 주장·원문 미확보·재인용·순환·보충자료 누락과 Methods/QC 위치를 표시합니다. 결과 승인(CP2)이 거부한 원출처의 재인용은 synthesis·anchor에서도 차단하며 이전 형식 문헌 요약의 분모도 유지합니다.
- #908·#909와 main #916의 인계를 재사용합니다. 원문 접근 기록의 nullable 보충자료 판정과 OA/PDF metadata를 보존하며 synthesis 입력은 기존 context 길이 안에 둡니다.
- 정리본은 기존 internal/restricted scope 판정으로 내부 단계·하위 단계의 원문 metadata를 제외합니다. 귀속 불명확 시 요청 전체에 보수적인 규칙을 적용하고 기존 report 표도 다시 생성합니다. 공개 파일 정책을 통과한 집계 artifact 링크와 원본 ledger는 유지합니다.
- CP2 거부 원출처의 재인용은 결론 근거 논문 집계와 hash 검증에서도 제외합니다. 공유 helper가 근거별 lookup·CP2·lineage를 판정합니다. 독립적인 승인 근거가 함께 있으면 claim은 유지하고 그 근거만 집계하며 승인 근거의 hash는 필수입니다. 수정: `db0f1d17`, `a8db61b0`.
- 단일 fake 요청의 report→portfolio, 근거 표 단일 표시·legacy 호환성·내부 metadata 차단·결론 분모·기존 집계표 링크를 포함한 관련 pytest 150 passed. 합성 노출 회귀 8 failed→8 passed, 혼합 근거 오집계도 재현·수정했습니다. Python 3.10 AST·Windows inventory·public·diff 검사 통과. 실제 HTTP/PDF·기관 접속·연구·HPC는 실행하지 않았으며 실제 원문 독해 품질은 미검증입니다.
- 최신 main #920을 통합했습니다. manual 해당 절은 최신 main 대비 LF 기준 186,475→186,764자(+289). 상세 필드·검증 경계는 `docs/reference/claim-source-display-904.md`(977자)에 둡니다.

필수 CI와 같은 최종 head의 완료된 bot review·미해결 P1=0을 확인한 뒤 병합합니다. #904·#826은 닫지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Closes #911

- 같은 configured identity의 amend·`-C/-c`에서도 Git author·timestamp와 현재 agent trailer를 보존합니다. `--reset-author`는 configured identity·새 timestamp를 유지합니다.
- 전역 commit-msg는 post-commit이 일반 commit/revert를 확인한 뒤 private message copy를 정리합니다. 모델명을 먼저 전달하고 bot amend에 copy를 반영합니다. source·임시 상태는 worktree별로 격리합니다.
- 수정 전 reuse 회귀 **10 failed·11 passed**, 모델 전달 회귀 **2 failed**. 수정 후 hook **43 passed**, rewrite 거절·실패·직원 세션 **3 passed**(총 46건), Windows selection **7 passed**. author·timestamp·agent 표식, replay·merge·revert·전역 hook 부재와 중단 뒤 후속 작업을 확인했습니다.
- public·diff·shell syntax·Python 3.10 AST·notes-index 검사 PASS. Windows 필수 hook cohort와 기존 검사는 유지했습니다.
- 머신 전역 hook·운영 설정은 변경하지 않았습니다. 최종 head CI·봇 리뷰 확인 뒤 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# 전역 Git 훅 연결과 원저자 보존 (#902)

- 전역 commit-msg·post-commit이 있으면 일반 새 커밋·revert를 위임해 봇 author·committer와 모델명을 남긴다. 전역 훅 부재·merge 커밋·reflog 비활성화는 자체 trailer 규칙을 쓴다.
- P1 4362054678 수정: rebase·cherry-pick·충돌 후 continue는 원저자와 메시지를 보존한다. linked worktree의 상태 경로도 검사하며, 상태 파일이 지워진 continue는 reflog로 구분한다. reflog를 끈 저장소는 자체 trailer 처리만 쓴다.
- 임시 Git 저장소·가짜 전역 훅 검사: continue 원저자 변경 1 failed·3 passed, merge·revert 각 2 failed, amend·명시 author 3 failed를 수정 전 재현했다. 수정 후 20 passed: checkout/worktree, rebase merge/apply, 신규 커밋·전역 훅 부재·reflog 비활성화·merge·revert·연속 cherry-pick·amend·명시 author를 확인했다.
- P2 4362840932 수정: 자동 merge에는 post-commit이 호출되지 않아 전역 commit-msg만 실행하면 표식이 사라졌다. merge는 두 훅 모두 전역 위임을 생략해 trailer를 보존한다.
- P2 4362909633 수정: 충돌 없는 revert는 commit-msg를 건너뛴다. 새 inverse commit의 post-commit에서 전역 author 처리를 실행해 봇 작성자를 남긴다.
- P1 4363000119 수정: amend는 기존 author를 유지한다. 명시한 다른 author도 보존하며 author와 committer가 다른 메시지는 자체 trailer 규칙을 쓴다.
- 수정 커밋 79579618의 author·committer는 Codex (gpt-6.1-sol) 봇이며 같은 에이전트 trailer는 제거됐다. 기존 커밋은 재작성하지 않았다.
- Windows CI의 새 test inventory 거부를 해결해 훅 검사 전체를 필수 cohort로 등록했다. selection 회귀 8 passed. patch-notes·notes·public·diff·Python 3.10 AST 검사 통과.
- 최종 head CI·같은 head 봇 P1=0 확인 뒤 병합한다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #906 OA/PDF receipt 인계

- 실제 접근 수준과 실패 사유, text/PDF 경로·hash, 추출 범위와 보충자료 상태를 보존합니다. OA 후보·403·closed·기관 로그인·receipt만으로 전문 독해나 Methods/QC 확인을 인정하지 않습니다.
- `source_access_metadata(row)`가 #905 SourceRef 필드로 변환합니다. 보충자료 의존 여부 기본값은 null이며 scout의 별도 판단이 필요합니다. Paper2Skill의 초록 receipt 승격을 막았습니다.
- 관련 mock/static pytest 198 passed·7 subtests passed. Windows inventory·public·Python 3.10 AST·diff 검사 통과. 새 회귀 검사는 기존 receipt cohort에 포함합니다. 전체 suite는 CI에서 확인합니다.
- 기존 schema가 생략한 optional metadata는 허용하고 전달된 값은 원본과 비교합니다. 핵심 artifact/hash/coverage 검증은 필수입니다. 본문 소실 시 receipt를 제거하고 접근 상태를 다시 계산합니다.
- README 문헌 문단 갱신: 11,829→11,852자(+23). #905 SourceRef 실제 model에 접근 상태 5종 전달 검증 5/5 PASS.
- 공유 파일 순서: main #916 → #906 접근 metadata → #907 schema/표시 통합. 문헌 추출 단계 연결과 접근 metadata를 함께 검증했습니다.
- W3 인계: PRECEDENT_SCHEMA의 `supplements_required`는 boolean/null, receipt의 optional source/PDF metadata를 보존합니다. [field 인계](https://github.com/ehojune/bioinfo-team-3d/issues/906#issuecomment-6371305285).
- 실 HTTP·PDF 다운로드·기관 접속·연구 실행 없음. 실제 원문 독해 품질은 미검증입니다. 최종 head CI·봇 리뷰 완료·미해결 P1=0 뒤 병합합니다.

Closes #906

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 결과
Closes #905

접근 metadata와 최초 실증 출처 계보를 기존 evidence contract에 추가했습니다.

## 변경
- `SourceRef`: 접근 범위·Methods/QC 위치·보충자료 상태. `Evidence.status`와 구분합니다.
- `Evidence.lineage`와 `assess_lineage`: 확인 근거가 있는 원출처만 확정하고 같은 source identity를 합칩니다. 순환·누락·미확인은 context로 남기며 독립 근거에서 제외합니다.
- `independent_groups`·verifier가 원출처 group을 재사용합니다. 같은 source로 병합된 원출처 행도 모두 검증하며 추가 URI·artifact가 미확인이면 통과시키지 않습니다. legacy payload·group 동작은 유지합니다.
- 원출처·중간 인용의 결함과 철회는 claim의 `defective` 판정으로 전달해 CSO가 인용을 차단합니다. 조회 미완료는 `unverified`로 남깁니다.
- 미해결 계보를 salvage할 때 문제 link만 거부해 접근 기록·필수 slot·다른 context link를 보존합니다.

## 검증·인계
- 관련 pytest 420 passed. P1 회귀 18건·P2 회귀 6건은 수정 전 실패를 재현했습니다.
- 확장한 result schema hash를 고정하고 새 metadata의 optional 계약을 검사합니다. plan schema·planning prompt·기존 plan hash 검사는 유지합니다.
- Windows selection inventory 검사 통과. 계보 회귀 검사는 기존 evidence test 모듈에 포함합니다.
- Python 3.10 AST·public·diff 검사 통과. 전체 suite·최종 head CI·봇 판정은 PR 기록을 따릅니다.
- 공용 contract: https://github.com/ehojune/bioinfo-team-3d/issues/905#issuecomment-6371292848
- helper는 `labhq.evidence`에서 import합니다. `assess_lineage`와 `independent_groups` 모두 optional `artifact_paths`를 받습니다.
- `original_evidence_ids`는 병합된 원출처 행을 모두 보존합니다. `source_keys`·`independence_groups`는 중복 제거합니다.
- report·manual·fake 통합은 #907 담당입니다. 실제 원문 독해 품질·기관 조회·연구 실행은 검증하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# 블라인드 리뷰어 초안 (#889)

- `research.blind_draft`(기본 false)를 켜면 CP1 승인 직후 첫 단계 전에 `reviewer_agent`가 원 요청만으로 초안을 쓰고 `research_contract.blind_draft`에 본문·sha256·UTC 시각으로 봉인한다. 주 결과가 이미 있는 재개는 초안 없이 `unavailable(results_exist)`, 초안 완료 시점에 결과가 있었으면 `late`, task 실패는 `unavailable`이며 요청은 계속된다.
- 연구 리뷰 prompt에 봉인된 초안(해시 일치 시)을 붙이고 review JSON에 optional `blind_comparison`을 받는다. 읽을 수 없는 값은 그 필드만 버린다. 일치를 근거로 세지 않는다.
- 순차 실행(병렬 아님). 새 task kind `blind_draft`는 read-only 프로파일과 기존 예약·취소 경로를 쓴다. 파일 격리는 하지 않는다(입력 기준 격리).
- 테스트: `tests/test_blind_draft_889.py` 7건(봉인 순서·입력 격리·리뷰 전달·꺼짐 무변화·실패 무중단·late/재시작·부분 폐기) 통과. 관련 연구 lane 테스트 247건 통과. 실제 Codex 직원, 실제 요청, Windows CI는 미확인.

## #890 계획에 선택 기록 고정 (PR #901, base 545a973b)

- 바꾼 것: 연구 계획의 선택 사항 `selection_record`(질문·전제·성공 기준·검토한 대안과 배제 이유·판단을 바꿀 관측). plan hash에 포함, 없으면 직렬화에서 빠져 이전 hash 유지. CSO prompt에 작성 요청, CP1 카드 요약 한 줄·웹 카드 항목(없으면 `선택 기록 없음`, 승인은 막지 않음). manual CP1 절.
- 검사: 새 test 6 + 웹 1. 수정 전 코드로 python 수집 실패·node 2 실패, 수정 후 연구 관련 348 passed·웹 fail 0.
- 한계: 연구 lane 계획만(일반 `PLAN_SCHEMA` 제외). 실제 모델의 작성 품질·지어낸 대안 걸러내기는 확인 안 함. 전체 suite는 CI에 맡김.

Refs #898

- R version/syntax subprocess의 elapsed·timeout·returncode를 test fixture에 따로 남기고 기본 10초 경계 두 개를 fake로 검증합니다.
- mock SIGTERM/SIGHUP은 work 준비 뒤 호출하며 durable result·gateway resume·ACK·entry 완료를 단계별로 기다립니다. 각 fixture 단계와 제품 shutdown은 5초를 유지합니다. ACK 전 entry 생존과 shutdown 반환 시 outbox 0을 확인하며 기존 단언도 유지합니다.
- ACK 3.1초 지연 주입: 수정 전 1 failed(`TimeoutError`), 수정 후 1 passed. 실제 CI의 DB/writer 지연 원인을 재현한 결과는 아닙니다.
- 관련 검사 serial 및 4-worker `loadfile`: 각각 130 passed·3 skipped. 변경한 4개 case는 serial/4-worker `load` 각각 20회 통과했습니다. 리뷰 수정 뒤 SIGTERM/SIGHUP도 같은 조건에서 각각 20회 통과했습니다(80 PASS).
- Python 3.10 AST·public·diff 검사 통과. 제품 코드·coverage/receipt 계약은 바꾸지 않았습니다.
- Rscript가 없는 로컬의 실제 R 두 case와 symlink 권한이 필요한 한 case는 skip됐습니다. R 실제 지연 원인은 미확인이므로 #898을 닫지 않습니다. CI·같은 head 봇 리뷰·병합은 PR에서 확인합니다.

[관측과 해석 경계](../reference/windows-test-boundaries-898.md)

Closes #654
PR #899

재시작 뒤 재개 카드를 승인하면 CP1(계획 승인) 카드가 바로 또 뜨던 문제를, 재개 카드 한 번으로 줄였습니다.

- CP1 전에 멈춘 첫 연구 계획이면 재개 카드에 단계·직원·지시 앞부분·비용 추정·집행 상한을 싣고, 승인하면 같은 계획 hash의 CP1 영수증(`via=resume_card`)을 한 번만 기록합니다. 집행 상한은 기존 CP1 승인 경로로 설정됩니다.
- hash 불일치·이어 가기·단계 실행 뒤 재승인·멈춘 의미 장치(`configured_off` 제외)·paper skill 줄·재배정 안내는 CP1 카드를 그대로 묻습니다. 계획이 없으면 재개만 묻습니다.
- `tests/test_resume_cp1_654.py` 6건: 수정 전 4건 실패·대조 2건 통과, 수정 뒤 6건 통과. 관련 묶음 283+92건, resume·interrupt 언급 파일 838건 통과(Windows, Python 3.12, 로컬).
- 봇 리뷰 반영(2차): 카드 detail에 CP1과 같은 `plan_canonical` 등을 싣고 웹이 계획 전체를 그립니다. `resume_cp1`은 영수증 저장 save에서 지웁니다. paper skill·재배정 안내가 있는 계획에는 통합 승인 카드를 만들지 않습니다. python 7건·web 1건 추가(수정 전 실패 확인), 관련 묶음 303건 통과. web 7건은 main에서도 실패하는 브라우저 의존 테스트입니다.
- CI: head `c529d574`의 `pytest (3.12)` 실패 1건은 무관한 timing 테스트(`test_policy` 경로 스캔 선형성)였고 재실행에서 통과했습니다. 부분 재실행(attempt 2)은 Windows 커버리지 artifact가 없어 `validation-manifest`만 실패해 전체를 새 push로 다시 돌렸습니다.
- 미검증: 웹 UI 표시(summary 텍스트만 사용·`web/` 변경 없음), 실제 PI state·실요청, CI(3.10·Windows).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>

## #658 종료하기: 멈춘 '마무리 중' 표시 (PR #895, base 611f8ea4)

- 대조: 이슈 4항목 중 1(끝나면 지움)·4(옛 gateway)는 main에 이미 있다. 2·3을 구현했다.
- 바꾼 것: gateway readiness의 bundle 행에 `elapsed_s`·`stuck`(10분 초과)을 더하고 stuck 행은 `ready`에서 뺀다. CLI `down`·웹 종료창이 단계(취소 기록·보고서 묶음 저장·결과 저장·PI 해석)와 `n분째`를 적고, 멈춘 마무리는 알리되 막지 않는다. 묶음은 `bundle_pending`이 남아 다음 시작에 다시 만든다. manual의 `down` 절 갱신.
- 검사: 새 test 2·문구 test 1 수정·JS 1. 수정 전 코드로 3 python·1 node 실패, 수정 후 관련 124 passed·2 skipped, node fail 0.
- 한계: stuck은 경과 시간 기준(진척 신호 없음). 묶음 단계만 적용, 취소 기록·결과 저장·PI 해석은 시작 시각이 없어 기존대로 막는다. 15분 남은 원래 사례의 원인은 로그가 없어 확인 못 함. 실제 gateway에서 `down`을 누른 확인과 전체 suite는 하지 않았다(CI에 맡김).

# Scheduler user 이름 확인

- USERNAME·USER·LOGNAME이 없는 Windows에서 `getpass.getuser()`가 POSIX 전용 `pwd` import로 `ModuleNotFoundError`를 내며 `Scheduler` 생성이 실패했다(proof c1 runner 시작 실패의 원인).
- `labhq/tools/scheduler.py`: `getpass.getuser()` → 실패하면 Windows `GetUserNameW`로 실제 로그온 계정을 구한다. 둘 다 실패하면 `self.user`는 비고, `queue()`(SGE·PBS·Slurm)는 `hpc.user`를 설정하라는 `RuntimeError`를 낸다. 이름을 지어내지 않는다(가짜 이름은 `qstat -u`·`squeue -u`가 실제 작업을 못 보게 한다). `mock`·`none`은 영향 없다.
- `tests/test_scheduler.py`: env 변수를 지워도 생성, getuser 실패 시 Windows API 계정(Windows에서만), user를 모를 때 queue 명령이 실행되지 않음(sge·slurm·pbs). 14건 통과. 전체 suite·실제 Windows CI는 미확인.

## 단계
P1 — Windows 종료 테스트의 fixture 준비 대기. Refs #854.

## 한 일
- 부모·손자의 준비를 최대 10초 handshake로 확인한 뒤 기존 1초 timeout과 두 프로세스 종료를 검사합니다.
- startup_delay 0·1.5초를 검사하고 준비 실패 때도 fixture tree를 정리합니다. 제품 코드·timeout·종료 단언·skip/retry·receipt 조건은 그대로입니다.

## 테스트 결과
- 독립 Windows 검증: 패치 전 3 PASS, 패치 후 5회 × 2조건 = 10 PASS. 보고상 잔류 프로세스 0, WinError 5 없음. 같은 검증은 반복하지 않았습니다.
- [main CI 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39905683733/job/120257047265)는 timeout 단언 뒤 PID 파일이 없는 경우입니다. 원래 간헐 실패는 로컬에서 재현되지 않아 해결 확정 근거는 아닙니다.
- 준비를 마친 트리의 종료를 검사합니다. 기동 지연을 포함한 제품 timeout은 이 테스트의 검증 범위 밖입니다.
- Python 3.10 문법·diff·public·기록 검사는 아래 확인란에 실제 결과를 반영합니다.

## 바꾼 파일
- `tests/test_runner_safety.py`
- `patch_notes/entries/codex--windows-timeout-ready-854-pr.yaml`
- 이 단계 보고

## 막힌 점
- 2026-10-24 02:17 KST 확인: head `58a41aeb`의 [CI 39979410026](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39979410026) attempt 1은 Windows R parse timeout 1건으로 실패했습니다(3545 passed, 30 skipped, 51 subtests passed). coverage receipt 오류는 pytest 실패 뒤 발생했습니다. 실패 job 재실행은 아직 쓰지 않았습니다.
- 같은 head의 [봇 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/885#discussion_r4362288677)는 완료됐으며 이 보고의 오래된 CI 상태를 P1로 지적했습니다. 해당 문장을 고쳤습니다. 수정 head의 CI·리뷰·병합 결과는 [PR #885](https://github.com/ehojune/bioinfo-team-3d/pull/885)에 기록합니다. #854 전수/receipt 완료 조건은 남습니다.

## PI·Claude에게 물을 것
- 없음.

## 확인
- [x] Python 3.10 문법·diff 검사
- [x] `scripts/check_public.sh`
- [x] `scripts/patch_notes.py check --base origin/main`
- [x] `scripts/notes_index.py --validate`

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# #833 Windows 필수 웹 검사 시간 계약

- [#876 Windows CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39897352658/job/120230799229), [#880 Windows CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39898639671/job/120234981537): Chrome가 생성됐지만 약 18~19초 동안 DevTools 포트가 나타나지 않아 UI 검사는 시작하지 못했다. 제품 결함 여부는 미확인이다.
- 총괄 판단으로 기존 viewport별 합산 30초를 60초로 바꾼다. Node 시작과 정리 8초를 포함하며 Chrome 시작은 최대 30초, UI 검사는 시작 뒤 최대 20초다. 실제 브라우저와 desktop·phone 단언, 실패 시 정리, Windows 필수 게이트는 그대로 요구한다. 재시도·워밍업·skip은 없다.
- [#881 리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/881)은 두 viewport가 각각 60초를 쓰면 기존 serial 2분에는 native 검사·collection 시간이 남지 않는다는 지적이다. routine CI의 serial 3분·pool 6분으로 재배분했고 Windows job 전체 10분, nightly/manual 전수 상한은 유지했다. 새 head의 실제 단계 시간은 미확인이다.
- 가짜 시계·자원과 정적 검사: Node #833 13건, Python CI 계약 57건 통과. 실제 Windows CI, 총 10분 목표, 제품 원인은 새 head에서 확인해야 한다.

## 단계
네 데몬 readiness의 부분 해결. Refs #877.

## 한 일
- 선택형 `contained_verified`에 state 밖 운영자 계약 pin과 실행 중 verifier 바이트 검증을 더했습니다.
- owner PID·생성 시각·이미지, fresh pipe challenge, 현재 네 역할의 정확한 Job 포함과 독립 마감을 묶습니다.
- 기존 detached 판정과 인증·연결 실패는 유지합니다. 독립 정적 검토에서 운영자 trust 경계 안의 P1은 해소됐습니다.

## 테스트 결과
- fake/static 회귀: 180 passed. 실제 process 생성·종료와 연구 요청은 없었습니다.
- CI 초기 실패 두 건 수정: Windows inventory 누락과 binary `Path.open`의 encoding AST 오인. 새 모듈을 Windows 필수 cohort에 포함했습니다. 수정 후 집중 회귀 197건·CI cohort 134건·profile 검증 PASS.
- 별도 후보 owner RPC의 fake 검사: 4 passed. 공개 제품의 runtime 검증을 대신하지 않습니다.
- Python 3.10 AST, public, diff 검사 PASS. CI는 PR에서 확인합니다.
- UI 변경 없음.

## 바꾼 파일
- `labhq/runtime_readiness.py`, `tests/test_contained_readiness.py`
- `docs/manual.md`, `docs/reference/contained-runtime.md`, 변경 기록

## 막힌 점
현재 B는 NO-GO입니다. 새 verifier 배포·owner RPC 전환·실제 doctor 검사는 남았습니다. 직원 adapter CLI·손자 Job 포함과 owner crash·실제 90분 종료 receipt도 별도 게이트입니다. WMI·service 우회는 미증명입니다. 네 데몬 판정으로 요청 전체 종료나 #877 완료를 주장하지 않습니다.

## 기존 PR 통합
- 공통 Chrome 시작 예산 수정 #881을 포함한 main을 병합했습니다. 제품 구현은 앞선 검토판과 같습니다. 새 head의 CI·리뷰가 남았습니다.
- 기존 B의 네 daemon은 독립 기한에 소유 Job 프로세스0으로 종료됐습니다. 직원 자식·실제 adapter proof는 미증명이며 B를 다시 시작하지 않았습니다. 개발 HANDOFF의 이전 세대 doctor PASS 표기를 바로잡았습니다.

## PI·Claude에게 물을 것
추가 PI 승인을 요청하지 않습니다. 실제 전환과 GO 판정은 총괄의 증거 검토 뒤 진행합니다.

## 확인
- [x] `scripts/check_public.sh` 통과
- [x] 같은 보고를 `docs/status/`에 기록
- [x] 실제 B·연구 실행 없음

🤖 Co-Authored-By: Codex (gpt-6-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #833 브라우저 시작 실패 진단

- 변경: DevTools 포트가 끝내 생기지 않은 **실패 때만** profile의 파일·폴더 수(최대 96항목, 깊이 2), Job·Chrome 생성 표식, 포트 읽기 실패 횟수, stderr 크기를 한 줄에 남긴다. 경로·파일명·내용은 출력하지 않는다.
- 계약: desktop·phone의 실제 브라우저 단언과 합산 30초 제한, 실패 시 소유 자원 정리, 필수 Windows 검사는 그대로다. 재시도·skip은 없다.
- 확인: 가짜 디렉터리 스트림 검사 1건 통과; Node 문법 검사와 Python 컴파일 통과. 실제 Chrome/Windows CI는 새 head에서 확인해야 한다.
- 첫 CI [run 39893812961](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39893812961)은 브라우저 전에 검토된 `test_web_3d.py` 해시가 낡아 Windows 선택을 거부했다. 새 해시와 가짜 Node 검사의 Windows 유지 항목을 갱신했고 `validate_profile()`·관련 가짜 검사 11건이 통과했다. 따라서 이 run에는 새 profile 관측값이 없다.
- 남음: 첫 Chrome 시작이 늦는 원인은 아직 미확인이다. 이 변경은 원인 조사용이며 #833 해결 판정이 아니다.

# 전기능 B 시운전 인수인계 현행화

## 단계
개발 문서 정정. B는 NO-GO입니다.

## 한 일
- `HANDOFF.md`의 현행 큐에 #877의 독립적인 요청 전체 90분 hard stop을 B GO 차단 P1로 올렸습니다.
- #739의 B 격리 설치·doctor PASS·요청 0건과 남은 원문·출처·상한·GO 조건을 구분했습니다. #876은 #833 Chrome 시작 구간의 Windows CI 실패로 병합 보류 중입니다.
- 옛 현황은 기존 `docs/reference/` 링크로 찾도록 했습니다. `HANDOFF.md`는 3,508→3,481자입니다.

## 테스트 결과
- `git diff --check`, 공개 검사, notes index, patch-notes 검사 통과.
- 제품 코드·설정·실제 연구 요청은 변경하거나 실행하지 않았습니다.

## 막힌 점
#877의 격리 hard-stop 증명과 별도 GO 전에는 B를 실행할 수 없습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
P2 — 이어 가기 계획 거절 뒤 복구 선택 보존 (#780)

## 한 일
- 이전 round를 복원한 종료 outcome과 이번 모델 거절 원인을 구분해 복구 카드를 표시합니다.
- 다른 모델 선택은 같은 요청·예정 round를 새 계획과 CP1부터 재개합니다. 이전 결과·archive·누적 비용·예산을 유지하며, 문구 수정은 기존처럼 명시적으로 새 요청을 만듭니다. 종료 선택은 실행하지 않습니다.
- 카드의 요청 시각·거절 task·round·계획과 승인 맥락 snapshot을 확인하고 바뀐 카드·이미 전송된 round는 거부합니다. 복구 선택 ID를 기존 recovery matcher에 묶어 같은 round의 예전 거절 결과를 다시 채택하지 않습니다.

## 테스트 결과
- 생산 종료·identity·카드·선택 코드를 추출한 synchronous inert fake 13건, Python 3.10 문법과 Windows 새 파일 13node 선택 PASS.
- 실제 Hub 카드/resolver의 trigger 2종×선택 3종 회귀는 추가만 했고 로컬에서는 실행하지 않았습니다. UI runtime 확인도 CI에 맡깁니다.
- 제품 import·pytest·async loop·프로세스·브라우저·모델 실행 없이 기록·공개·diff 검사만 수행했습니다.

## CI 후속
- 첫 CI의 Linux 3.10/3.12는 기존 metadata 회귀의 plain namespace에 새 helper 연결이 빠져 각각 1건 실패했습니다. 실제 helper를 연결하고 같은 원래 회귀를 동기 추출 검사에 포함했습니다. 제품 코드는 바꾸지 않았습니다.
- Windows는 기존 #833 브라우저 시작 30초 초과(30 PASS/1 FAIL)로 중단됐습니다. 이 수정은 해당 실패를 해결했다고 주장하지 않습니다.
- 공통 시작 예산 수정 #881이 병합된 뒤 main을 충돌 없이 통합했습니다. 복구 제품·기존 fake 검사·CI 선택기는 검토된 a142462d와 같은 Git blob입니다. 새 head의 실제 CI·리뷰는 별도로 확인하며 새 연구 요청·worker 호출은 없습니다.

## 최종 리뷰 수정
- b3f13b8a는 CI10개 모두 통과했으나 봇이 단계 이름 `plan`과 계획 거절 기록의 충돌을 지적했습니다. 이전 단계 결과를 덮어쓰고 같은 요청의 복구 카드를 잃는 문제를 review·CP2 두 경로에서 재현했습니다.
- 계획 제어 결과는 진단 기록에 보존하고 이전 단계 결과와 분리했습니다. 같은 단계 이름의 결과 보존→카드 생성→다른 모델로 같은 요청 복구를 검증한 동기 fake 회귀를 추가했습니다.
- 수정 전 두 경로 FAIL, 수정 후14검사 PASS. 제품 import·async loop·외부 process·network·signal 없이 실행했고 Python3.10 AST·profile도 통과했습니다. 새 head CI·리뷰는 다시 확인해야 합니다.

## 막힌 점
실제 연구 성공이나 시운전 GO를 뜻하지 않습니다. 독립 검토·CI·병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
#833 resizable 브라우저 시험의 기한·정리 경계 보정 (PR #875)

## 한 일
- 같은 pytest node `test_office_in_node[web_resizable_panels.cjs-fail 0]`가 스크립트를 `LABHQ_RESIZABLE_VIEWPORT=desktop`, `phone`으로 차례로 두 번 실행합니다. 각 실행은 timeout 30초와 새로 계산한 절대 끝 시각을 받습니다. 첫 판의 공유 기한은 job 120174823730에서 phone에 812ms만 남겼으므로 바꿨습니다.
- 각 실행 안에서는 그 끝 시각에서 `CLOSE_RESERVE_MS`(정리 5초, profile 삭제 2초, 종료 1초)를 뺀 작업 기한을 단계끼리 공유합니다.
- 브라우저가 필수일 때 각 실행은 pass 3, skipped 0과 해당 viewport의 `ok 3` 제목을 보여야 합니다. 어느 한쪽이라도 실패·skip·누락이면 node가 실패합니다.
- JS는 두 정적 시험을 유지하고 선택된 viewport의 브라우저 시험만 등록합니다. 잘못된 선택값과 viewport 없는 바깥 기한은 거부합니다. 직접 실행(변수 없음)은 두 viewport를 차례로 돌립니다. desktop·phone 판정은 그대로입니다.
- demo driver는 기한이 있을 때만 Chrome 시작·DevTools 조회·CDP socket·CDP 호출·navigate·boot 대기를 기한 안에서 끝냅니다. 남은 fetch는 abort하고 CDP 호출은 pending에서 뺀 뒤 기존 `close()`로 한 번 정리합니다. 기한이 없는 호출은 그대로입니다.
- `config/ci/windows-boundary-v1.json`에서는 `tests/test_web_3d.py`의 원본 sha256만 갱신했습니다. node·suite·workflow·critical 목록은 그대로입니다.
- 시간 기록(run 39881000670 뒤 추가): Python은 실패·timeout 출력에 launch 직전 wall clock, 그 값으로 만든 기한, monotonic으로 잰 실행 시간을 한 줄 붙입니다. Node는 첫 줄·require 뒤·각 viewport open 직전에 process 시작 시각, monotonic 값, wall clock, 남은 outer와 작업 시간을 남깁니다. 기한·판정은 그대로이고 증거 수집일 뿐입니다.
- 진단: Chrome Popen 뒤 `LABHQ_BROWSER_CREATED` 줄(`chrome-created`), DevToolsActivePort 읽기 실패의 고정 코드별 횟수, 기한에 걸린 단계 한 줄을 남깁니다. 경로·메시지·stderr 원문·PID는 남기지 않습니다.

## 테스트 결과
- 바꾼 `.cjs` 3개에 `node --check`, Python 파일에 3.10 문법 파싱을 했습니다.
- Job wrapper 가짜 시험 17건이 통과했습니다.
- `test_web_3d` wrapper는 `node_wrapper`와 정적 source 확인으로 27건 통과입니다(두 번의 필수 실행, 실행마다 다른 기한, timeout 30초, 선택값 격리, 어느 쪽의 실패·skip·다른 제목·timeout도 거부, 실패 출력의 Python 시간 줄, Node 시간 기록 위치). f8e8aed의 파일로는 새 시간 확인 3건이, 그 전 00a7e91의 파일로는 21건이 실패합니다.
- Node 가짜 시험(workBudget, vm 안의 driver, 표식 파싱)은 작성만 했습니다. 로컬에서 Node를 실행하지 않았으므로 CI에서 처음 돕니다.
- 기록 검사·공개 검사·validate_profile·git diff --check를 했습니다. pytest·브라우저·실제 Windows는 돌리지 않았습니다.

## 막힌 점
- 2cbd958 이후 CI는 아직 확인하지 않았습니다. 직전 f8e8aed의 Windows run 39881000670은 desktop 작업 기한(chrome-start)으로 실패했고, driver trace 전 약 11.5초의 원인은 미확인입니다.
- Chrome 시작이 느린 원인은 미확인이고 이 변경은 그 해결이 아닙니다.
- 이 node는 이제 최대 약 60초(30초 두 번)가 걸릴 수 있습니다. 직렬 critical 단계의 기존 상한 안에 들어가는지 총괄 확인이 필요합니다.
- 한 viewport의 작업이 약 22초를 넘기면 기한 실패로 끝납니다.
- 커밋 작성자는 전역 post-commit hook이 `claude-ehojune[bot]`으로 바꾸고 trailer를 지웠습니다. 예상된 동작이고 기록은 고치지 않았습니다.

🤖 Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## 단계
P1 — 계획 채용 변환에 상위 비용 예약 전달 (#862)

## 한 일
- 상위 계획 채용 task의 예약액과 남은 금액을 실제 내부 변환 task까지 전달합니다. 예약 모드에서는 채용 설정 상한과 함께 작은 값을 택하고, 잘못됐거나 없는 allowance는 보내지 않습니다.
- Claude CLI의 cent 단위로 내림해 예약액보다 큰 상한이 전달되지 않게 합니다. 1cent 미만은 거절하며 기본 예약-off와 수동 채용은 유지합니다.
- 내부 결과는 기존 task ID 회계로 한 번만 집계합니다. 내부 실행 뒤 Python 반환을 놓친 경우 실제 nested ID와 같은 writer lane의 finished receipt를 확인합니다. 이미 저장된 내부 비용을 상위 UNKNOWN으로 중복 예약하지 않으며 근거가 없으면 UNKNOWN입니다. 명시 취소·반복 취소와 map 정리도 처리합니다.

## 테스트 결과
- 생산 코드·Task 선언·Claude 예산 argv 구간을 추출한 순수 fake 검사 17건과 Python 3.10 문법 검사 통과.
- Windows 경계에 해당 새 파일만 등록했고 17개 node 선택을 확인했습니다. 기록·공개·diff 검사 통과.
- 제품 import·pytest·실제 async loop·실제 runner/CLI·프로세스·모델 실행은 하지 않았습니다. 실제 task 실행과 전체 회귀는 CI에서 확인합니다.

## 막힌 점
공급자 청구 상한이나 실제 A/B 성공을 입증하지 않습니다. 독립 검토·CI·병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
#844 Windows receipt·ACK 읽기 보강

## 한 일
- 기한이 있는 `_poll`에서만 winerror 32 또는 winerror 없는 EACCES를 다시 읽습니다. 명시된 다른 winerror·다른 errno·첫 request 읽기는 바로 실패합니다.
- 부모 receipt 대기와 bootstrap ACK 대기가 각자 `denied_reads`를 0부터 세어 실패 기록·정지 줄에 정수만 남깁니다.
- provider 첫 단계를 고정 문자열로 바로 flush하고 stdout 종류·byte 수를 기록합니다. 내용은 남기지 않습니다.
- clean-install `STARTUP_DIAGNOSTIC`과 시험 쪽 투영도 provider의 stdout 종류·byte 수와 receipt `denied_reads`를 남깁니다. 숫자는 0 이상 2**32 이하 정수만 받고, 16KiB·8개·4096바이트 상한은 그대로입니다.
- 20초 기한·0.02초 간격·생존·만료·nonce·identity·환경·Job 확인과 WMI create 1회는 그대로입니다.

## 테스트 결과
- Python 3.10 문법 파싱을 했습니다.
- broker 원본을 가짜 `windows_launch`로 실행하고 세 시험 파일(broker·phases·frames)을 가짜로만 돌렸습니다. 새 코드는 119건 통과입니다. main의 broker에서는 28건이 실패하며, 야간과 같은 증상인 winerror 없는 EACCES 정지가 포함됩니다.
- clean-install 투영과 시험 쪽 투영을 AST로 꺼내 가짜 기록으로 돌렸습니다. 새 코드는 36건 통과이고, main의 투영에서는 값이 남아야 하는 26건이 실패합니다.
- 기록 검사·공개 검사·git diff --check를 했습니다.
- pytest·실제 Windows·WMI·Job·CI는 돌리지 않았습니다.

## 막힌 점
원래 Win32 원인은 미확인입니다. CI·병합은 총괄 담당입니다.

🤖 Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## 단계
야간 Windows 실패 증거 보강 (#854, #844)

## 한 일
- run 39858916081의 `test_consult_restart` settle 시간 초과에 작업 상태·완료 callback 시각·경과 시간을 남기는 시험 전용 진단을 넣었습니다. 3초 상한·예외·판정은 그대로입니다.
- callback 실행 시각은 실제 작업 종료보다 늦을 수 있어, 이 값만으로 event loop 지연이나 작업 정체를 확정하지 않습니다.
- supervisor 실제 gateway 시험은 시작 증거를 이미 출력합니다. 야간 로그에 `stage=ack_wait error=PermissionError`(winerror 없음)가 남아 있어 시험은 바꾸지 않았습니다. 앞선 분류의 "증거 없음"은 틀렸고 여기서 정정합니다.
- 코드 읽기로 찾은 단서입니다(재현하지 않음): Windows에서 Python `open()`이 낸 PermissionError에는 winerror가 없을 수 있습니다. 그렇다면 #868의 winerror 32 재시도가 ACK 읽기에 적용되지 않을 수 있습니다. 제품 코드라 이 PR에서는 고치지 않습니다.

## 테스트 결과
- Python 3.10 문법 파싱, AST로 꺼낸 진단 함수·새 순수 시험을 가짜 작업으로 실행, settle 구조 AST 확인, 기록 검사·공개 검사·git diff --check만 했습니다.
- pytest·asyncio·제품 import·process·WMI 검사는 하지 않았습니다.

## 막힌 점
원인은 UNKNOWN입니다. 전체 Windows 야간 성공을 주장하지 않습니다. CI와 병합은 총괄 담당입니다.

🤖 Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## 단계
개발 인수인계의 현행 정책 정정

## 한 일
- 낡은 새 Codex 작업 보류 문구를 개발 재개·같은 담당자 우선 배정으로 바꾸고 현행 한도는 #760에 연결했습니다.
- #739의 A/B 승인·A 먼저 준비·실제 연구 요청 0건을 적고 구현·CI와 실운전 성과를 구분했습니다.
- HANDOFF 3,488→3,452자, 운영 기준 1,183→1,142자. 합계 4,671→4,594자입니다.

## 테스트 결과
- 문서·기록 검사와 공개 검사, git diff --check만 수행했습니다.
- 제품 import·pytest·process fixtures·모델·runtime 검사는 하지 않았습니다.

## 막힌 점
실운전 성공을 주장하지 않습니다. 병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
#869 — 채용·논문 스킬 기능의 명시적 비활성화

## 한 일
- `recruit.enabled`는 strict boolean이며 기본 true입니다. false이면 채용·설치·계획 준비·지연 작업·논문 스킬 부착을 거부합니다.
- gateway/runner에서 복구·위조 task도 작업 폴더나 모델 실행 전에 거부합니다. 계약 활성화·연장·재고용은 막고 해제는 허용합니다.
- doctor는 명시적으로 꺼진 Paper2Agent만 skip하고 --fix도 설치하지 않습니다. 기본 recruiter 누락과 다른 인증·runtime 검사는 유지합니다. 이미 발생한 변환 비용은 보존합니다.

## 테스트 결과
- 추출한 실제 함수와 fake 경계를 쓰는 새 검사 14건, 기존 paper-skill 정적 검사 9건 PASS. 제품/app/Hub/runner import와 event loop 없이 검사했습니다.
- Python 3.10 AST 9개 파일, integrations --check, public·diff 검사 PASS.

## 막힌 점
실제 설치·runtime·모델·network·process·Job 검사는 하지 않았습니다. CI·독립 검토·병합은 총괄 담당이며 이 PR만으로 운영 준비나 GO를 주장하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
PR #867 진단 전용 branch에 최신 main 통합

## 한 일
- main f5f56b67ff7220bd86e775f6c3f209f35a98edc3(#865 포함)을 충돌 없이 merge했습니다.
- 자동 merge-tree 47011c70a8ad6e6dd1758c4aae5fbeaa79b83fc5와 merge 결과가 같습니다. 기존 작성자 이력과 진단 코드·test 4개 blob을 보존했습니다.
- 제품 변경은 main에서 온 내용뿐입니다. 브라우저 시작 표시와 대기 수치는 진단이며 원인·시작 성공을 증명하지 않습니다.

## 테스트 결과
- in-process fake Job/start 12건, 변경 Python 2개 3.10 AST PASS. 실제 Job을 불러오는 test는 실행하지 않았습니다.
- notes_index validate, patch_notes check, public check, diff check를 확인합니다.

## 한계
실제 browser·Node·process·Job·전체 pytest는 실행하지 않았습니다. CI와 병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #865 main 통합

- base: `386edba` (#868 merge `3e1b787` 포함). 충돌 없이 합쳤고 tree `e35868b`는 자동 merge-tree와 같다.
- 예약 제품·테스트·예제 설정 8개 파일은 검토판 `bc0c4a2`와 byte 단위로 같다. Windows 경로 비용·WMI 수정은 main에서 받았다.
- 로컬 검증: 예약 동기 fake 6건과 논문 스킬 순수 계약 12건을 통과했다. 실제 gateway·runner·프로세스·WMI·Job 검사는 실행하지 않았다.
- 예산 예약은 공급자 청구 hard cap이 아니며 Codex의 진행 중 비용 초과를 막지는 못한다. 새 head CI·리뷰·병합은 총괄이 맡는다.

## Linux 경로 비용과 Windows ACK 대기 수리 통합 (#868)

- base: main `c7671a0`. 검토된 #861 `ba95796`을 merge해 원래 커밋·작성자를 보존했다.
- `policy.py`·`test_policy.py`는 #868 검토판 `973c543`, WMI 제품·테스트는 #861 검토판과 byte 단위로 같다.
- [CI 39854597294](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39854597294): Windows provider 생성·Job 비소속 확인 뒤 bootstrap `ack_wait PermissionError`로 실패했다. 로그에 winerror가 없어 32라고 확정하지 않는다.
- #861은 receipt·ACK 대기의 winerror 32만 기존 deadline 안에서 재읽는다. 다른 오류·첫 request 읽기·nonce/identity/Job 검사는 그대로다. timeout·skip·보안 조건을 바꾸지 않았다.
- Linux의 이차 경로 정규화는 component stack으로 고친다. 전체 731,072자 입력·8배 비용 계약을 유지한다.
- 로컬 WMI fake 16건과 문자열 검사 22건을 통과했다. 문자열 검사의 touches_resolved는 파일시스템 대신 lexical mock을 썼다. 로컬 검증은 fake·정적 검사뿐이다. 실제 Windows/WMI·프로세스 시험과 새 head CI 확인은 하지 않았다. #861 정리와 #868 병합은 총괄 몫이다.

## 경로 정규화의 Linux 이차 비용 수리와 비율 계약 (source 5b803cb·e7a6b26, base 0f80516)

- 관측:
  - PR866 CI(run 39845342493)에서 절대 2초 예산이 2.046·2.267초로 실패했다.
  - 같은 시험을 1/4→전체 비율 계약으로 바꾼 PR868 head 831084d의 Linux 3.12 CI(run 39849219342)에서는 4배 입력에 mentions_zone 11.2배, touches 10.2배가 나왔다.
  - 처음의 "스캐너는 선형"이라는 판단은 Windows에서만 잰 결과라 틀렸다.
- 원인: Codex 독립 진단이 재현했다.
  - `_norm`이 131,072자 구분자 후보를 `ntpath.normpath`에 넘긴다.
  - Linux가 쓰는 pure-Python 판은 빈 성분을 list에서 하나씩 `del`해 이차 비용이 된다.
  - Windows의 native normpath는 이를 가렸다.
- 변경(제품): `labhq/policy.py`의 `_windows_normpath`를 더했고, `_norm`의 Windows 분기가 이것을 쓴다.
  - 실행 중인 stdlib의 `splitroot`(3.12+)·`splitdrive`(3.10/3.11)로 drive·root를 나눈다. 3.10/3.11에서 장치·literal 접두는 stdlib 그대로다.
  - 성분 규칙(빈·`.` 제거, `x\..` 상쇄, root 아래 맨 앞 `..` 제거, 빈 결과 `.`)을 stack으로 한 번에 적용한다.
  - 구분자 run, `.` run, `x\..` 쌍, root 아래 `..` run이 모두 선형이다.
- 변경(시험):
  - 호출 스레드 CPU 기준 1/4→전체 8배 계약(+ 시계 한 tick)을 둔다.
  - 기존 731,072자 입력과 늦은 `/restricted`·file URI 검사는 그대로다.
  - 실행 중인 CPython의 pure-Python normpath(AST 추출, Windows에서도 실행)와 짧은 입력 전수·UNC/장치/drive 사례를 비교한다.
  - 다섯 가지 긴 모양에 같은 계약을 건다.
- 검사(로컬, 문자열만):
  - 짧은 입력 약 50만 건에서 3.12·3.14 pure-Python normpath와 불일치 0건이다.
  - 3.12 Windows native와는 `_norm`이 닿는 입력 중 `\?\UNC\` 1건이 달랐다. 이 경우 Linux·3.14와 같은 결과를 낸다.
  - Linux 동작을 흉내 낸 수정 전 스캐너는 mentions_zone 전체 3.2초, touches 비율 8.5배로 실패했다. 수정 후에는 0.07초, 비율 4배다.
  - 시험 22건 in-process 통과, `validate_profile`, 3.10 AST를 통과했다.
- 한계:
  - 3.10 pure-Python 판은 로컬에 없어 CI 몫이다.
  - `private_paths.path_field_text`와 Windows 전용 `claude_write_input`의 `ntpath.normpath`는 이번 범위 밖이다.
  - 비율 계약은 해당 입력에 대한 것이며 모든 입력의 선형성 증명이 아니다.

## 변경
자동 monitor의 terminal→PI 취소 race를 막고 요청 소유 task attempts를 읽는 변경안입니다.

- `/cancel-active`는 exact created_at·현재 active 상태를 await 없이 확인합니다. terminal은 PI 결정 없이 생략하며 구판404 뒤 legacy 취소로 fallback하지 않습니다. 수동 취소는 유지합니다.
- 인증된 `/task-attempts`는 durable briefing/review/retry 전체와 같은 snapshot의 비용·hold를 묶습니다. accounting/result에만 있는 ID는 누락 UNKNOWN입니다. identity 오류·1000행·1MiB 초과는 incomplete입니다. prompt/argv/workdir는 반환하지 않습니다.
- cleanup은 허용필드만 반환합니다. merged TaskResult 저장 경로를 확인했으며 Job close 계약과 측정된 ActiveProcesses0을 구분합니다. budget_holds 의미는 미병합 #865에 의존합니다.
- fake 취소7건·projection10건 PASS. 실제 HTTP/process/auth/model/연구는 미실행이고 runtime은 UNVERIFIED입니다. Windows inventory는 순수 test로 등록했고 기존 안전 경계는 유지합니다.
- main0f80516(#864 포함)을 통합했습니다. manual 충돌1건은 취소/cleanup 설명을 모두 보존했습니다. CI는 총괄 watcher가 확인합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #833: Windows 브라우저 시작 지연을 wrapper와 Chrome으로 나누는 진단 (source bd401f7, base f47e841)

- 관측: PR861 CI의 critical `web_resizable_panels.cjs`가 30초를 넘겼다.
  - 단계 기록은 `chrome-spawned`(651 ms)에서 멈췄고, DevToolsActivePort는 30초 안에 생기지 않았다.
  - 앞선 run 39835363858에서는 port가 생기기까지 20.4초가 걸렸다.
  - 이 시간이 Python wrapper·Job 준비인지, Chrome 자체인지 가를 기록이 없었다.
- 변경(진단 전용):
  - `windows_job_browser.py`: Job을 만든 뒤 Chrome을 시작하기 직전에 고정 줄 `LABHQ_JOB_STARTING_BROWSER`를 stderr에 한 번 쓴다.
  - `demo_driver.cjs`:
    - 그 줄이 stderr 첫 줄일 때만 관측 `job-ready`를 남긴다.
    - port가 5초 지나도 없으면 관측 `chrome-waiting` 한 줄을 남긴다. 담는 값은 표시를 받았는지, profile 최상위 항목 수(드라이버가 만든 `home` 1개 포함), Chrome stderr byte 수뿐이다.
    - 두 행 모두 기존 관측 6행 예산 안이다. 경로·stderr 내용·URL·argv·환경 값은 남기지 않는다.
- 바꾸지 않은 것:
  - 시작 판정: DevToolsActivePort의 port만으로 대기가 끝난다.
  - START_MS 60초, node 30초, 정리·receipt·재시도·skip·CI 설정·제품 코드.
  - 표시가 없거나 Chrome 출력 중간에 같은 문자열이 있어도 판정에 쓰이지 않는다.
- 검사:
  - wrapper 시험(가짜 Job·start) 13건을 in-process로 통과했다. 기존 정확 일치 2건에는 표시 줄을 앞에 붙였다.
  - watcher 순수 검사와 `node --check`를 통과했다.
  - Python 3.10 AST를 통과했다.
  - 실제 브라우저·process·pytest·node test 실행은 CI 몫이다.
- 다음 재발 때 판정:
  - `job-ready`가 늦으면 wrapper·Job 준비 쪽이다.
  - 빠른데 port가 없을 때 profile 항목·stderr가 늘었으면 Chrome이 일하는 중이다.
  - 늘지 않았으면 Chrome이 아직 실행되지 않은 것이다(이미지 로드·검사 등).
- 독립 검토 P2 수정(`733b644`): 표시 줄의 CR과 LF가 다른 chunk로 오면 표시를 놓쳤다. 이제 표시+CRLF/LF의 앞부분인 동안만 기다린다(최대 표시 길이+2 글자). fake case 1건을 더했다.
- 원인은 아직 모른다. #833은 열어 둔다.

## #862 모델 호출 사전 예산 예약 (source bb23573, base f47e841)

- 배경: `_check_budget`은 이미 쓴 비용·비용 모름 예약·논문 준비 예약만 보고 허용했다. 보낼 호출과 진행 중인 호출은 세지 않았다. 그래서 동시에 시작한 briefing·precedent, 병렬 단계, 마지막 호출이 상한을 넘을 수 있었다. recruit 작업은 `recruit.max_budget_usd`(예: 25)로 Claude에 넘어갔다.
- 바꾼 것(opt-in `policy.budget.reserve_before_dispatch`, 기본 false):
  - `dispatch_with_retry`가 보내기 직전, 마지막 await 뒤에 `_reserve_dispatch`를 부른다. budget lock 안에서 이미 쓴 비용 + 비용 모름 예약 + 진행 중 예약 + 이번 상한이 요청 상한 이하일 때만 예약하고 보낸다.
  - 이번 상한은 task·직원 `max_budget_usd`·`per_task_usd` 중 최소이고, 없으면 남은 예산이다. 보낸 task의 `budget_usd`·`request_budget_remaining_usd`를 그 값으로 바꾼다.
  - 넘으면 일반 호출은 기존 예산 카드로 묻고(`reserve_usd` 표시), 채용·환경 진단은 카드 없이 거부한다.
  - 예약은 요청 기록 `budget_holds`에 저장한다.
    - 이 orchestrator가 그 task의 비용(또는 비용 모름 예약)을 집계할 때까지 bound에 남는다.
    - dispatch가 정상으로 돌아온 뒤에만 지운다. gateway가 답하기 전에 비용을 기록했거나 보내지 않은 경우다.
    - 취소·예외·재시작이면 남긴다.
  - 상한 값이 유한한 양수가 아니면 카드 없이 거부한다. 남은 예산 0이면 보내지 않는다. 설정의 NaN·음수·무한은 옵션을 켤 때 거부한다.
  - 논문 스킬 준비(`paper_budget_holds`)와 PI 통역(`pi_translation_budget`)은 기존 예약을 쓰며 중복 예약하지 않는다.
- 검사:
  - `tests/test_cso.py`에 test 9개(case 19개)를 더했다. FakeHub와 가짜 dispatch만 쓴다.
    - 기본 꺼짐
    - 동시 시작 경쟁
    - 마지막 호출과 잔액 0
    - 잘못된 상한 5종과 설정 3종
    - recruit·진단의 clamp와 카드 없는 거부
    - 취소 뒤 예약 유지·재시작·비용 모름 대체
    - 복구된 이전 task와 중복 집계
    - 논문 준비 비중복
  - 로컬 in-process(gateway·runner import만 stub): 새 case 19개 통과, `test_cso.py` 215 passed.
  - 실패 8개는 stub 때문이며 main export에서도 같다. 관련 파일 5개의 결과도 main과 같다.
  - 3.10 AST, public, diff 검사를 통과했다.
- 남은 것과 한계:
  - labhq 비용 기록·추정 기준의 상한이고 공급자 청구액 보장이 아니다.
  - Codex는 실행 중 비용 제한 플래그가 없어 예약을 넘을 수 있다. 그 초과는 끝난 뒤 기록되어 다음 호출을 막는다.
  - 요청 상한이 없으면(0·비움) 예약하지 않는다.
  - 실제 gateway·runner·모델 실행은 확인하지 않았다.
- 독립 검토 후속(`be76b41`, main `690a588` 통합):
  - CI run 39843720353 Linux 3.10: `test_paper_skill_waiting_static`의 AST 추출 fixture 2개가 `_budget_bound`의 새 의존(`_dispatch_held`·`_usd_amount`·`hub.requests`)을 몰라 실패했다(11249 pass/2 fail). fixture에 그 의존을 넣었고 단언은 그대로다. `test_paper_skill_recovery_identity`의 복제 dispatch에는 `_recovery_choice`를 더했다.
  - P2-1(복구 이중 예약): 재시작 복구가 이전 task를 이어받는 호출도 새 attempt를 예약해, 원래 상한에서 거짓 예산 카드가 떴다.
    - gateway에 `_recovery_choice`(dispatch가 쓰는 같은 선택)와 `recovery_task_id`를 두었다.
    - CSO는 예약 직전 그 id가 있으면 새 예약 없이 이전 task 예약을 쓴다. 예약이 없으면 카드 없이 이전 task를 잡는다. 예약 판단과 dispatch의 복구 판단 사이에는 await가 없다.
  - P2-2(보내지 않은 호출의 영구 예약):
    - gateway는 저장 뒤 보내다 실패한 task를 비용 모름으로 기록한다(기본 동작에도 적용).
    - CSO는 요청 (재)시작 때 저장도 비용 기록도 없는 id의 예약만 푼다. 이 process가 보내는 중인 attempt는 제외한다.
    - 같은 process에서 저장 전에 취소·예외가 난 attempt도 푼다. 보냈을 수 있는 예약은 남긴다.
  - 검사:
    - 새 fake case 6개(원래 상한 2.0): 복구 이어받기 2개, 재시작 정리 3개, 저장 전 취소 2개(저장 뒤면 유지). 그리고 gateway test 1개.
    - in-process: 예약 test 25개 통과, `test_cso.py` 221 passed(stub 8 실패는 main과 같음), static 파일 12 passed.
    - 이전 head `977c6de` 코드로 돌리면 복구 case가 원래 상한에서 `budget reservation … approval denied`로 실패해 검토 재현과 같다.
    - gateway test와 `test_paper_skill_recovery_identity`는 실제 Hub가 필요해 CI 몫이다.
- 재검토(P1·P2 0) 후속(`aeb9055`, main `0f80516`·#864 통합):
  - 매뉴얼에서 '끄면(기본) 이전처럼'은 보내기 전 예약에만 해당하게 고쳤다.
  - 저장 뒤 보내기 실패를 비용 모름으로 기록하는 것은 설정과 무관하다는 문단을 따로 두었다(P3-1). 제품·test 코드는 그대로다.
  - main 통합은 자동이다. 이 PR의 code·test blob은 `41e6fac`와 같다. #864는 `server.py`를 바꾸지 않았다.

## #863 — 작업 취소 접수와 직원 프로세스 정리 증거 구분 (base f47e841)

- 문제: 요청은 먼저 `cancelled`로 저장되고, CLI cancel 성공과 `task.result`만으로는 직원 CLI 자손이 끝났다고 말할 수 없었다. `_kill`은 종료 결과와 wait timeout을 버렸다. Windows wrapper의 Job 활성은 기록되지 않았다.
- 변경(기존 wrapper 확장, 새 launcher·pipe·제어면 없음):
  - `runner/memory_limit.py`: wrapper가 Job에 들어간 직후, CLI를 만들기 전에 stderr 첫 줄 `LABHQ_JOB_ACTIVE pid=<wrapper> kill_on_close=1`을 쓴다. Job 생성·handle 보유·KILL_ON_CLOSE·wait·returncode 처리는 그대로다.
  - `adapters/base.py`:
    - runner는 wrapper stderr의 **첫 줄**이 자기 wrapper PID의 그 줄일 때만 Job 활성으로 본다. CLI는 그 뒤에야 같은 stream에 쓸 수 있고, stdout은 보지 않는다. 확인된 줄은 CLI stderr에서 뺀다.
    - `_kill`이 tree 종료 수와 wait 완료 여부를 돌려준다.
    - spawn은 shield 안에서 돈다. 도중에 취소되면 끝난 spawn이 만든 wrapper를 넘겨받아 끝내고 `UNKNOWN`(spawn_cancelled_before_handover)으로 남긴다. 실행 파일이 없으면 `not_started`다.
  - `models.TaskResult.cleanup_evidence`(새 필드): status는 `job_close_contract_enforced`(활성 확인 + wrapper wait 완료) / `UNKNOWN`(reason) / `not_started`이고, `active_processes_measured: false`를 함께 둔다. 종료 코드가 0이 아니어도(labhq가 끝낸 경우 포함) 정리 실패로 보지 않는다.
  - `runner/daemon.py`:
    - 취소로 끝난 run은 adapter가 보고한 증거를 받는다. 보고가 없으면 UNKNOWN(no_runner_observation)이다.
    - runner 재시작만 있는 결과는 UNKNOWN(runner_restart_only)이다.
    - `_shutdown_result`는 `shutdown_evidence`를 덮어쓰지 않고 합치며, 중단일 때만 `cleanup`을 더한다. 정상 종료의 shutdown_evidence는 계속 비어 있다.
  - manual 요청 취소 문단에 한 단락을 더했다.
- 범위 밖: Job ActiveProcesses 조회, handle 복제, 광범위 kill, COM·WMI·서비스 등 Job 밖 실행, 제품 전체 제어면 개편.
- 가정(manual에 명시, 독립 검토 P1/P2 0 뒤): runner가 띄운 wrapper의 코드·실행 환경을 믿고, wrapper가 복제·상속하지 않은 유일한 Job handle을 끝까지 쥔다. OS sandbox도, ActiveProcesses 0 측정도 아니다. 실행 코드는 바꾸지 않았다.
- 검사(가짜만):
  - `tests/test_step_memory_limit.py` +12 함수, 18 case: 알림 순서, Job 배정 실패, Job handle 비상속·wrapper 보유, 첫 줄·PID 판정, verdict 표(메타데이터 누락·wait 미완료 포함), 정상·0이 아닌 종료·위조 줄·다른 PID·무알림·Job 꺼짐, 실행 중 취소, spawn 중 취소(만들어짐/없음), daemon 합치기·재시작.
  - 프로세스 없이 in-process로 실행했다: adapter·wrapper 14 case 통과, daemon 두 메서드(AST 추출) 통과. 이전 코드에서는 새 API가 없어 실패한다.
  - Python 3.10 AST 통과. 실제 Job·프로세스·WMI는 CI 몫이다.

## #844 — WMI bootstrap 대기 중 공유 위반을 다시 읽기 (base fc22600)

- 관측: PR859 head `c6e43c4` CI(run 39838111863, Windows job 120031772167)에서 `test_with_restarting_on_a_role_that_keeps_failing_is_started_again_and_then_left`가 실패했다.
  - 실행 기록: 직접 기동이 winerror 5로 거부된 뒤 `wmi_attempt` → `wmi_verified`(child Job 밖).
  - 감독 로그: `labhq WMI bootstrap stopped before the role started: stage=ack_wait error=PermissionError`(#851의 단계 기록). 종료 코드 2.
- 원인 분석: ack_wait 단계에서 `PermissionError`를 낼 수 있는 호출은 ACK 파일 읽기(`_read`)다.
  - `Native.alive` 실패는 `OSError(GetLastError())`로 만들어져 `PermissionError`가 되지 않는다.
  - Python의 파일 열기는 FILE_SHARE_DELETE 없이 열기 때문에, 다른 handle이 DELETE 권한으로 같은 파일을 열고 있으면 공유 위반(winerror 32)이 난다. 쓰는 쪽의 `os.replace`나 파일 검사기가 그런 handle을 잡을 수 있다.
  - 대기 루프는 FileNotFoundError만 '아직 없음'으로 보고 나머지는 실패로 끝냈다. 부모의 receipt 대기도 같은 구조다.
  - 어떤 handle이 잡았는지와 실제 winerror 값은 기록에 없었다. 이번 변경으로 정지 기록에 winerror를 남긴다.
- 변경(`labhq/windows_broker.py`):
  - `_poll`: 공유 위반(winerror 32)만 '아직 없음'으로 보고 다시 읽는다. 부모 receipt 대기와 bootstrap ACK 대기 두 곳에만 쓴다.
  - 기존 deadline·nonce·caller 생존·Job 확인은 그대로다. 접근 거부(5) 등 다른 오류와 첫 request 읽기는 이전처럼 바로 실패한다.
  - 정지 기록에 `winerror=<숫자>`를 덧붙인다(정수일 때만).
- 검사(mock 전용, `tests/test_windows_broker.py` +4):
  - receipt 대기·ACK 대기 각각 winerror 32는 다시 읽어 성공, 5는 그대로 실패.
  - 프로세스 없이 in-process로 실행했다: 새 코드 16/16 통과. 이전 broker 코드에서는 공유 위반 2건이 CI와 같은 증상(ack_wait의 PermissionError, 부모의 native_error code 13)으로 실패한다.
  - Python 3.10 AST.
- 미확인: 실제 Windows에서 잡은 handle의 정체, 실제 WMI·감독 재실행. #844는 열어 둔다(이전 두 실패의 단계는 기록이 없어 같은 원인인지 모른다).

## 단계
직원 기본 모델 설정 변경

## 한 일
- engineer와 facilities의 기본 모델만 gpt-6.1-sol로 바꿨습니다.
- 기존 model 단언, 생성 README 한영 inventory, manual 직원 표를 맞췄습니다.
- Luna·Astra·Claude, bench arm·CSO 선택은 유지했습니다. 실행 인스턴스와 시운전 후보는 변경하지 않았습니다.

## 테스트 결과
- AgentSpec 13건과 직원 model 2개만 변경됐다는 정적 대조 PASS.
- registry/facilities 기존 단언과 생성 inventory의 정확한 값 대조 PASS.
- integrations --check, check_public.sh, git diff --check PASS.
- pytest는 로컬 미설치로 실행하지 않았습니다. 기존 test의 CI 검증이 남았습니다.

## 막힌 점
제품 모델 가용성·실요청·성능은 UNVERIFIED입니다. 모델·연구·브라우저·runtime 실행은 없었습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #858 요청 전체 공개 조회 시도 상한 (source 9a10aef·d6d1372, base 5ea970f → b4e18f9 통합)

- 배경: 시운전 B(#739)의 고정 계약은 한 요청에 공개 문헌 조회 시도 9회다(실패·재시도 포함). 제품에는 단계별 20분 상한과 기관 경로 상한만 있었고 요청 단위 admission이 없었다(독립 검토 P2-5).
- 바꾼 것:
  - `annot.request_lookup_limit`(선택, 기본 없음) 설정을 더했다.
  - runner broker에 `/lookup/admit`·`/lookup/finish`를 더했다. 기록은 `runner/lookup_admission.py`가 `runner.state_dir/lookup_admission.sqlite3`에 남긴다.
  - 상한이 켜지면 runner가 작업 env에 `LABHQ_LOOKUP_ADMISSION=1`을 넣는다.
- 세는 단위(`d6d1372`에서 바꿈): `labhq_annot`이 공개 서비스로 보내는 **HTTP 요청 하나**가 한 시도다.
  - 이전 head `9a10aef`는 도구 호출만 셌다. 그래서 한 호출 안의 재시도(최대 5회)·redirect·논문별 요청이 무제한이었다(독립 검토 P1).
  - 이제 도구 httpx client의 `request` hook이 보내기 직전마다 admission을 받는다.
  - httpx는 따라가는 redirect hop마다 hook을 부르고, annot의 재시도·페이지·항목별 요청은 client를 다시 부르므로 모두 한 번씩 센다.
  - 거부되면 hook이 예외를 내 그 요청은 나가지 않는다. 같은 호출의 뒤 요청은 broker에 다시 묻지 않고 거부한다.
  - 행에는 tool·host·HTTP status·호출 결과를 남기고, query는 남기지 않는다.
- 요청 구분과 원자성:
  - 요청 id는 broker가 작업 token에 묶은 값만 쓴다. 호출 body가 다른 요청·작업을 적으면 403이다.
  - SQLite `BEGIN IMMEDIATE`로 세고 기록하므로 같은 요청의 직원·단계·재시작·동시 요청이 한 상한을 쓴다.
- fail closed: broker token·runner ledger·request id가 없거나 broker에 닿지 못하면 보내지 않는다.
  - 상한이 켜지면 요청마다 셀 수 없는 AlphaGenome 도구는 거부한다.
  - `key_fulltext`의 기관 browser 경로는 쓰지 않는다.
- 기본: 설정이 없으면 broker를 부르지 않는다. 기존 동작과 같다.
- 검사:
  - `tests/test_external_wait.py`의 #858 test를 다시 썼다. MockTransport·가짜 backend만 쓴다.
    - ledger
    - 24 thread 동시성
    - broker 인증 경로
    - 상한 3에서 계획 3·5건: 3건만 나가고 4번째는 보내기 전에 거부
    - 503 재시도와 302 redirect가 각각 한 번씩 셈
    - 한 호출 안 동시 요청 8건 중 3건
    - fail closed와 미설정
    - runner env
  - 로컬 in-process 결과는 파일 전체 35 passed다.
  - broker·runner 연결 test 3개는 로컬 venv에 fastapi·websockets가 없어 CI 몫이다.
  - 3.10 AST, public, diff 검사를 통과했다.
- 남은 것:
  - 웹 검색·외부 HTTP MCP·셸 네트워크는 막지 않는다. 시운전 직원 구성에서 빼야 한다.
  - runner가 둘이면 따로 센다.
  - DNS 조회는 HTTP 요청이 아니라 세지 않는다.
  - 실제 runner·provider·시운전에서는 확인하지 않았다.

## #833 — Windows 브라우저 검사 시작·첫 화면 지연 진단 (base 5ea970f)

- 관측:
  - PR852 run 39709142121의 critical `web_resizable_panels.cjs`가 30초를 넘겼다. desktop의 차가운 Chrome 시작이 debug port 7.0초, page target 8.4초였다. 이어 phone의 첫 `Page.navigate`가 돌아오지 않았다.
  - #850 run 39701871339 focus 검사에서도 첫 navigate가 15.5초였다.
  - 두 경우 모두 정리는 정상 종료했다(#842와 다른 지점).
  - Chrome 쪽 지연인지 정적 서버 쪽 지연인지 가를 근거가 없었다.
- 변경(진단 전용, `tests/support/demo_driver.cjs`):
  - `target-ready` 행에 `/json` 시도·무응답·page 없음 횟수를 붙인다.
  - navigation마다 정적 서버가 `index.html` 요청을 받은 때(`server-request`)와 응답을 보낸 때(`server-response`, status)를 남긴다.
  - navigation이 5초 넘게 돌아오지 않으면 그때까지의 요청·응답 여부를 `navigate-waiting`으로 한 번 남긴다.
  - 이 행들은 기존 lifecycle 16행과 따로 6행까지만 쓰고, 마지막 경계 시각을 옮기지 않는다. 출력은 기존처럼 실패 때만 보인다.
- 바꾸지 않은 것: 제한시간(30초·170초, START 60초), 재시도, skip, 정리 계약, CI 설정, 제품 코드.
- 검사: `tests/web_visual_tooling.cjs`의 trace 검사에 순수 test 1건(관측 행 예산·경계 불변·lifecycle 16행 유지)을 더했다. 로컬에서는 `node --check`만 했고, test 실행은 CI 몫이다.
- 다음 재발 때 판정:
  - `navigate-waiting`에 요청이 없으면 Chrome이 요청을 보내지 않은 것이다.
  - 요청은 있고 응답이 없으면 서버 쪽이다.
  - 둘 다 있으면 응답 뒤 Chrome 쪽 commit 지연이다.
  - 시작 지연은 `/json` 무응답과 page 없음 비율로 가른다.
- 원인은 아직 모른다. #833은 열어 둔다.

# 논문 인용·토픽 선정 과정

- Virtual Biotech: Science 게재본 DOI를 기본 링크로 사용. 공개 구현 출처는 별도 유지.
- 토픽: 후보 출처, 기존 분류·EDAM 대조, PI 선정, 논문 수집·PMID 통합·보강을 짧게 정리.
- 근거: #420·#402·#446·#455·#462, PubMed42752167. 당시 문헌43분야와 현재 점검표45분야·서지 확인과 전문 평가를 구분.
- 문서·공개 검사만 수행하며 제품 코드·데이터·실행은 바꾸지 않음.

# README 출처·문헌 규모 보완

- 소개: The Virtual Biotech 연구팀 구조에서 출발했음을 명시.
- References: 한컴 노마디안의 3D 공간 구성·디자인 참고 출처 추가.
- 점검표: 논문 근거 목록 1,286편 추가, 한영판 함께 반영.
- 확인: TSV 고유 PMID 1,286개, 설정45분야·135항목, 공식 소개 URL·diff·공개 검사. 제품 코드는 변경하지 않음.

## #854 Windows 야간 subtest 오판 (source 3590827, base 059b910)

- 확인한 사실: run 39681301430 attempt 1(schedule, main `99f1c49`)의 `pytest-windows` job 119504243006.
  - 인증된 gh로 로그를 받아 확인했다. critical 단계는 `26 passed`, parallel 단계는 `11125 passed, 50 skipped, 7 subtests passed`였다.
  - 첫 오류는 그 뒤 `ci_suite report`의 `validate_reports`에서 난 `ValueError: duplicate node execution`이다.
  - pytest 9.1.1이 설치됐고, `tests/test_paper_skill_waiting_static.py`가 `self.subTest`를 쓴다.
  - 판단 코드는 99f1c49와 059b910 사이에 바뀌지 않았다.
- 원인: pytest 9는 subTest 결과마다 같은 nodeid의 `call` 보고(`SubtestReport`)를 한 번 더 낸다. `ci_suite`가 이것을 `stage_reports`에 그대로 넣어 검증기가 두 번째 call로 봤다. 실제 재실행 증거는 없다.
- 고친 것:
  - `ci_suite`는 subtest 보고를 `subtest_reports`에 따로 남긴다. checkpoint·파일 시간에는 넣지 않고, 실패는 failed로 남긴다.
  - `ci_windows_phases`는 subtest마다 선택된 노드의 통과한 call 안에 있는지 확인한다. critical에서는 passed만 받고, worker 합이 controller와 같아야 한다.
  - 중복 허용, 집합 dedup, 실패의 성공 처리는 없다. 같은 노드의 두 번째 call은 계속 `duplicate node execution`이다.
- 검사: `tests/test_ci_windows_phases.py` 한 파일만 in-process로 돌렸다(가짜 JSON·tmp만 씀, process 없음).
  - 121 passed.
  - 새 영수증 case 6개와 observer case 1개는 이전 코드에서 5건이 실패한다. 통과하는 2건은 정상 subtest와, 그대로 유지되는 실제 재실행 거부다.
  - Python 3.10 AST를 통과했다.
- 미확인: 실제 Windows 야간 전수 run, PR CI, 독립 검토. 전수 PASS만으로 검증기가 맞다고 보지 않는다.

## #849·#850 병합 뒤 독자 문서 대조 (source 3671b5c·fa5d91c, base 30e1712)

- 대조 범위: README.md, README.en.md, AGENTS.md, HANDOFF.md 전체. 매뉴얼은 기관 셋업(첫 설정 문단), 선행 연구 목록, `labhq talent`, 구조 목록 줄만 봤다. 기준은 승인 main `30e1712` 객체다.
- 고친 것:
  - README·README.en 비교표의 문헌 칸: "검색·인용만"을 "검색·인용·본문 요청"으로 바꿨다. `key_fulltext`(합법 OA → 기관 세션 → PI PDF 요청 카드)가 이미 있다.
  - 매뉴얼 첫 설정 문단(main 623자 → 498자):
    - 주 독자에게 필요한 내용만 남겼다: 선택 질문, 공식 HTTPS 프록시 접두, email, Enter로 건너뛰기, 로그인 뒤 주소 붙이지 않기, 새 설정의 세션 꺼짐, 기존 파일 무변경.
    - "Web of Science Research Assistant·Nexus Connect 전용 자동 연결은 아직 없다"를 적었다.
    - 일반 기관 경로(기관 세션·프록시·PDF 요청)는 그대로 둔다.
  - 세부는 `docs/reference/institution-literature-826.md`(새 문서)로 옮겼다:
    - 기존 기관 경로
    - 받는 프록시 형식과 세션 탐지 한계(`_proxy_prefix`와 대조)
    - 전용 연결 미구현, 기관 MCP endpoint·client 지원 미확인
    - Clarivate 공식 페이지 2개의 확인 범위
    - 기관 이름·프록시 주소는 넣지 않았다.
  - `init_wizard.py` WoS 안내 문자열: "안내하면 그때 연결합니다"는 없는 기능을 약속해서, 전용 연결 미지원과 기관 제공 접속 조건 확인으로 고쳤다. 질문·URL 검증·저장·`--yes`/`--dry-run`/비대화형 논리는 그대로다. 기존 test가 묻는 문구("확인하지 않았고", "자동으로 연결하지 않습니다")도 남아 있다.
- 고치지 않은 것과 근거:
  - #849 매뉴얼 문단: UI(`ui/literature.js`)와 보고 부록 문구와 같다.
  - `labhq talent` 줄: 정확하다.
  - HANDOFF: 기본 역할 13개(Claude Code 8·Codex 5)가 `agents/core`와 같다.
  - AGENTS의 web 설명과 구조 목록(`ui/*.js` 와일드카드): 정확하다.
- 문자 수(LF):
  - README 11,686→11,691
  - README.en 17,627→17,639
  - manual 178,125→178,000
  - 새 reference 2,562(`aff7c00` 2,414에서 아래 정정으로 늘었다)
- 독립 peer P3 정정(`2fd0172`): reference의 일반 기관 경로 조건을 코드에 맞췄다. 기관 세션은 `institution_enabled`·`institution_tdm_allowed`·`external_model_allowed`를 모두 켜야 쓰고, PI PDF 요청 카드는 `external_model_allowed`가 필요하다. 구현된 조건과 실제 기관 연결 검증(하지 않음)을 나눠 적었다.
- 검사:
  - 바꾼 안내 함수를 AST로 뽑아 가짜 입력으로 실행했다. 기존 test 문구 단언을 충족하고, 프록시·email 저장값은 그대로다.
  - Python 3.10 AST를 통과했다.
  - pytest와 실제 init은 CI 몫이다.
- 미확인: 실제 UI 표시, 기관 연결, 원문 확보.

## #678 일부 — 앞 단계 입력 복사의 저장 공간 사전 거부 (source 31494ef, base 3c07c1c)

- 조사: labhq가 직접 크기를 알고 큰 파일을 쓰는 경로는 단계 입력 복사(`runner/step_inputs.stage_inputs`, 파일당 2 GiB·합 20 GiB 상한)다. 직원 셸 다운로드(curl·wget 등)는 policy가 쓰기 위치만 보고 크기를 모른다. 문헌 PDF(≤45 MB)와 ENCODE cCRE(≤500 MB)는 상한이 작다.
- 이전 동작: 복사는 여유를 보지 않았다. 같은 드라이브에 gateway DB가 있으면 중간에 디스크를 다 채운 뒤에야 실패했고, 이유는 `OSError`로만 남았다.
- 현재 동작:
  - 파일 내용을 복사하기 전에 알려진 크기 합과 작업 드라이브 여유를 비교한다. 크기 합이 여유보다 크면 거기서 멈추고, 필요·여유 크기를 기존 거부 경로(활동 기록 warn·직원 안내)로 알린다. 압축 볼륨처럼 실제 사용량이 다른 드라이브는 보장하지 않는다.
  - 원본은 그대로이고 같은 단계를 다시 시도할 수 있다. 여유 조회 실패는 이전처럼 복사하고, 복사 중 ENOSPC는 `disk full during copy`로 알린다.
- 넣지 않은 것(새 정책): 예비 공간 기준, 셸 다운로드나 크기 모르는 다운로드 차단, 새 PI 카드.
- 검사:
  - `tests/test_step_inputs_665.py`에 2건을 더했다. 기존 test 파일이므로 inventory 변경은 없다.
  - in-process 실행(disk 조회는 mock, tmp만 씀): 새 코드에서 기존 staging 5건과 새 2건이 통과했다. 이전 코드에서는 새 2건이 실패했다.
  - 3.10 AST, diff 검사를 통과했다. 실제 pytest는 CI 몫이다.
  - 독립 검토 후속(`865ced9`): 기존 `link_swap` case가 새 크기 확인에서 먼저 거부돼 복사 열기 시점의 거부를 더 확인하지 않았다. 바꿔치기 주입을 공간 검사 뒤로 옮겨 다시 `_copy`에서 거부되는지 확인한다(in-process 추적). 제품 코드는 그대로다.
- 남은 #678 범위: task 시작 검사와 PI 카드, fail 단계, 큰 원자료 위치와 직원 브리핑, 셸 다운로드 범위. 이 issue 전체를 해결한 것이 아니며 시운전의 새 선결로 삼지 않는다.

## #844 — supervisor 시작 실패 진단에 실행 기록·gateway 로그 꼬리 추가 (base 3c07c1c)

- 변경: `tests/test_supervisor_process.py`의 시험 전용 `_print_start_evidence`가 `gateway-supervisor.log`·`gateway-supervisor-diagnostics.jsonl`에 더해 `detached-launch.jsonl`과 `gateway.log`의 꼬리를 출력한다(`START_EVIDENCE_FILES`). 16KiB 상한, token 마스킹, 제어문자 치환, symlink 거부, 원래 실패 예외 보존, 진단 오류 비전파는 그대로다. 제품 코드, 제한시간, CI 선택 정책은 바꾸지 않았다.
- 이유: #844의 두 실패(run 39684337037, 39689292875)는 supervisor가 gateway 기록 전에 끝났다는 사실만 남겼다. 기존 진단으로는 Windows 자식 기동 거부(`child_start_failed`의 세부)와 gateway 즉시 종료(`child_exit`의 원인)를 가를 수 없었다.
- 검사: 기존 순수 test에 두 파일의 부재와 존재(마스킹·제어문자 포함)를 더했다. 실제 helper와 test를 AST로 뽑아 in-process로 실행했다: 수정 전 helper는 새 test에서 FAIL, 수정 후 PASS. Python 3.10 AST도 통과했다. 실프로세스 test와 pytest는 실행하지 않았다.
- 공개 준비: 승인 main `30e1712`(#850 포함)를 merge `3731fb2`로 명시 통합했다. 충돌은 없고, `tests/test_supervisor_process.py`는 local `1b45979`와 byte 동일하다. AST 비교에서 함수 27개·test 9개가 모두 남았고, 바뀐 것은 진단 함수와 그 순수 test 두 개, 새 상수 하나다.
- CI(head `12e9782`, run 39704958735): Windows pool에서 `test_a_role_killed_from_outside_is_restarted_but_one_stopped_on_purpose_is_not`가 60.66초 뒤 `supervisor=[]`로 실패했다. 이 PR이 바꾸지 않은 test다. 실행 기록에서 직접 기동은 winerror 5로 거부됐고, WMI bootstrap이 Job 밖으로 검증됐다. 그 뒤 감독 diagnostics는 0행이고 로그도 비어 있었다. coverage 단계의 `incomplete/stale worker receipt`는 gw2·master의 exitstatus 1을 정상 거부한 2차 결과다(artifact digest·run·head 일치 확인).
- 확인된 결함(`1136231`): `windows_broker`의 `__main__`이 모든 Exception을 출력 없는 `SystemExit(2)`로 바꿔, stderr가 role 로그로 넘어간 뒤 감독 스크립트에서 난 시작 예외까지 숨겼다. 이제 role 단계 예외는 다시 던져 traceback이 role 로그에 남는다. ACK 대기·최종 확인·정리·진입 단계 정지는 같은 로그에 `stage=… error=…` 한 줄만 쓰고 2로 끝난다. 메시지·명령·환경·경로는 쓰지 않는다. 재시작 검사 실패 메시지에 감독 프로세스 종료 코드를 붙였다. 순수 확인: 실제 함수를 가짜 Native로 실행했다. 수정 전에는 세 경우 모두 출력 없는 2, 수정 후에는 단계 줄 1개·role 예외 재발생·receipt 전 무출력이다. `_bootstrap` 단계는 정상=cleanup, 호출자 종료=ack_wait, Job=final_check, 환경 불일치=없음이다.
- 남음: 원인은 미확정이다. 이 수정은 다음 재발 때 원인을 보이게 할 뿐 #844를 해결하지 않는다. #844는 열어 둔다.

## #826 일부 — 첫 설정의 기관 문헌 접근 질문 (source c60adba, base 06dfb1b)

- 변경: `labhq init`이 사람이 터미널에 있을 때만(`--yes`·`--dry-run`·비대화형 제외) 기관 이름, 도서관 프록시 주소, 이용 중인 문헌 서비스, Unpaywall 연락 email을 묻는다. 모두 Enter로 건너뛸 수 있다.
  - 새 설정에는 기존 항목 `literature.institution_proxy`와 `annot.unpaywall_email`만 저장한다. 프록시는 https, 계정 정보 없음, `url=`/`{url}` 외 query 없음일 때만 받는다. 기관 세션·TDM·외부 모델 전송은 꺼 둔다. schema는 바꾸지 않았다.
  - 기관 이름과 서비스는 저장하지 않고 안내에만 쓴다. Web of Science·Nexus는 기관 연결 주소·권한이 확인되지 않았고 자동 연결하지 않는다고 알린다.
  - 기존 설정은 다시 쓰지 않는다. 현재 상태(프록시·세션·email 유무)를 보이고, 넣을 항목만 YAML로 출력한다.
  - 비밀번호·cookie·API 키는 묻지 않으며, 접속·로그인·다운로드를 하지 않는다.
- 문서: 매뉴얼 첫 설정 절에 안내 한 문단을 더했다(177,200→177,632자).
- 검사: `tests/test_init_wizard.py`에 프록시 판정 10건, 새 설정 저장, 전부 Enter, 거부, 재실행 미변경, 비대화형·`--yes`·`--dry-run` 미질문 검사를 더했다. 로컬에서는 helper를 AST로 추출해 실제 `LiteratureSettings`·`Settings`와 가짜 입력으로 순수 실행해 모두 통과했다. init 흐름 test(`wizard.run`)는 doctor import에 필요한 의존이 로컬에 없어 CI 몫이다.
- 남은 #826: 연결 뒤 검색·원문·추출 점검, 제공자 표, 호출 기록과 실패 인계, WoS/Nexus 실제 연결, 구독 원문. 기관 계약·endpoint는 외부 미확인이다.
- 공개 준비(09:35): 승인 main `4ff7c12`(#848 `92b4f9a`)를 merge `591c387`로 통합했다(자체 변경 없음, init·test hash 불변). #848 peer P3에 따라 매뉴얼 Unpaywall email 항목의 문장 순서를 고쳐 `enabled: false`가 `unpaywall` 도구의 답이고 `key_fulltext`는 email이 없으면 Unpaywall만 건너뛴다고 적었다(`3f31354`, 177,687→177,758자). 코드·timeout·기관·PDF 경로는 그대로다.
- 독립 검토 P2 수정(09:47, `a5fb4eb`): 기존 설정 재실행은 "아무것도 저장하지 않았다"고 말하고 현재 세션 상태와 채울 설정 이름만 보인다. 입력한 주소·email은 다시 출력하지 않는다(위 줄의 "YAML로 출력"을 대체). 프록시는 host의 userinfo·`{url}`, `;` matrix, CAS형 `ST-…`, session/ticket/token 이름 경로, 범위 밖 port를 거부하고 `{url}`은 `url`/`qurl` 값이나 마지막 경로 한 자리로만 받는다. 경로 속 임의 토큰은 다 가려내지 못한다고 안내한다. manual 177,758→177,950자. 순수 전후 재현: 이전 코드는 검토 값 4개 허용·재실행 "저장하되"·값 재출력, 수정 후 모두 거부·미출력, 기존 10행 그대로.

## #660 — 여우 선행 연구 목록과 논문별 자문 스킬 카드 (base 9d4836c)

- 변경: `paper_skills.literature_view`가 저장된 요청의 `analysis_precedents`·`paper_skill_results`·`recruit_results`·단계 receipt를 묶는다. 이를 `GET /api/requests/{id}`가 `paper_literature`로 함께 돌려준다. 새 route, 파일 읽기, 빌드, 다운로드는 없다. 2.5D 여우 시트(`ui/literature.js`)는 논문마다 쓰임(계획 참고·자문 스킬·재현 채용)을 보이고, 행을 누르면 스킬 카드가 열린다. 표시는 text만 쓰고, 링크는 확인한 DOI/PMID로만 만든다.
- 결과 참조(`called`)는 도구 호출이나 효용의 증거가 아니라고 표시한다. 실행 기록의 스킬 줄도 "호출됨" 대신 "결과 참조: 확인/미확인/실행 전"으로 바꿨다.
- 손상 기록은 스킬 표시를 생략한다(`damaged`). 제목은 300자로 자르고 제어문자를 지운다. 본문·talent 경로는 내보내지 않는다.
- 로컬: 새 serializer 테스트 2건을 in-process로 직접 실행해 통과했다. 변경 JS는 `node --check`를 통과했다. API·Node renderer 테스트는 CI에서 확인한다(로컬에 fastapi·pytest가 없고 JS 실행은 금지).
- 통합: 승인 main `06dfb1b`(#847 `9588695`, doctor·manual 정정 포함)를 merge `3b5957a`로 명시 통합했다. 충돌은 없다.
- 3D: 기본 진입은 2.5D(`/`, `labhq open`)이고 3D는 `/3d`다. 3D 선택 패널에서 여우를 고르면 같은 `ui/literature.js`로 목록을 그린다. 데이터는 `live.js`가 기존 detail GET으로 요청 갱신마다 한 번 읽는다. `live.js`에는 새 import가 없다.
- manual: 선행 연구 절에 여우 목록·행 카드 설명을 추가했다. 0908 patch의 나머지 두 hunk는 #847로 이미 main에 있어 다시 적용하지 않았다.
- 독립 검토 P2 수정(`aadca75`): 목록이 아닌 `recruit_results`와 receipt의 목록형 status·reason이 `TypeError`로 요청 상세 GET 전체를 깨던 것을, 해당 입력만 좁게 검사해 `damaged`로 표시하게 했다. 같은 순수 fuzz 585건에서 수정 전 실패 경로 2개 → 수정 후 0. 기존 테스트에 serializer 1건·API 200과 저장 기록 불변 1건을 보탰다.
- CI(head `ffa0ed3`, run 39700661073): Linux 3.10·3.12·slow·clean-install은 통과했다. Windows job 119568904814는 pytest 시작 전 `Bind changed-test range to checkout`에서 `unbound selection merge parents`로 실패했다. 원인은 PR event base `06dfb1b`와 GitHub 시험 merge `78ad532`의 첫 parent `4ff7c12`(#848 병합 뒤 main)가 달랐던 것이다. 이 base로 diff를 만들면 #848 파일까지 변경으로 잡히므로 가드가 거부한 것은 의도대로다. 가드는 바꾸지 않고 main `4ff7c12`를 merge `eb2c44b`로 명시 통합했다. 순수 재현: 실제 값은 거부, event base가 merge parent와 같으면 통과, head·parent 순서·base=head가 다르면 계속 거부.
- 미실행·남은 것: 실제 요청에서의 부착·결과 참조·효용, 실제 브라우저 표시. #660은 닫지 않는다.

## manual 논문 스킬 절 정정 (source 8105613, storage branch 동반)

- 이미 main에 병합된 #830(a2a7477) receipt 보호를 "아직 main에 반영되지 않았다"고 한 문장을 지웠다.
- `labhq talent` 기록이 효용을 확인해 주는 것처럼 읽히던 문장을 "어느 단계에 붙고 결과에서 참조됐는지"로 고쳤다.
- C3 patch(`talent660-manual0846.patch`)를 `git apply --check`와 내용 검토 뒤 적용했다. 두 문장만 바꿨고 manual은 177,255→177,214자(LF, `wc -m`)다.
- doctor·test 코드와 #678 상태 드라이브 변경은 그대로다. 이 문서 정정은 #846 PR이 아니라 이후 storage PR에만 동반한다. 실제 CI는 미확인이다.

## #678 일부 — 볼륨 번호 0의 중복 억제 방지 (source dea14b8, 이전 384db0c)

- root 선정 C1 P3: `_volume`이 `st_dev` 0(UNC·특수 볼륨)을 알려진 같은 볼륨처럼 비교해, 0끼리 같다는 이유로 gateway 상태 행을 뺄 수 있었다.
- 수정: 0이면 `OSError`(판정 불가)를 내 기존 경로대로 행을 남긴다. None을 돌려주면 None끼리 같아 다시 누락되므로 쓰지 않았다. 정상 볼륨의 중복 억제, 10 GiB·5% 기준, 폴더 무생성은 그대로다.
- 검사: `tests/test_disk_preflight.py`에 순수 회귀 1건을 더했다. 실제 `_volume`·`_disk_rows` 분기에서 모든 경로의 `st_dev`가 0이고, 기존 행은 넉넉하며, 상태 드라이브만 부족한 경우다. 로컬은 venv에 `websockets`가 없어 doctor를 import할 수 없어서 AST 추출로 확인했다. 새 코드는 경고하고 0360ac1 코드는 누락했다. 기존 세 시나리오는 그대로 통과했다. 3.10 AST·profile·public·diff 검사 통과. pytest는 CI 몫이다.
- doctor 부모 탐색 중복(P3)은 이번에 refactor하지 않았다. #678의 나머지 범위는 그대로 열려 있다.

## #678 일부 — doctor 저장공간 경고에 gateway 상태 드라이브 포함 (source 384db0c, base e41cfe2)

- 이전 동작: doctor `disk` 행은 작업·임시·Codex/Claude 직원 홈 드라이브만 읽었다. 그래서 작업 폴더를 D:로 옮겨도 gateway SQLite·이벤트(`gateway.state_dir`)가 여유 없는 C:에 있으면 경고가 없었다. 10-16 사례의 gateway 정지 위험이다.
- 현재 동작: gateway 상태 폴더의 볼륨이 이미 보인 행들의 볼륨과 다르면, 같은 읽기 전용 기준(10 GiB·5%)으로 `gateway state disk` 행을 더한다.
  - 볼륨은 `disk_space.usage`와 같은 방식(가장 가까운 기존 부모)으로 판정하며 폴더를 만들지 않는다.
  - 같은 볼륨이면 행을 반복하지 않는다. 볼륨을 구분할 수 없으면 행을 남긴다. 경로와 조회 예외 문구는 노출하지 않는다.
- 검사:
  - `tests/test_disk_preflight.py`에 3건을 더했다: 다른 볼륨의 부족 경고, 같은 볼륨 미반복, 없는 폴더의 부모 판정과 구분 불가 시 행 유지. 기존 단언은 그대로다.
  - 로컬 venv에 `websockets`가 없어 doctor를 import할 수 없었다. 그래서 같은 세 시나리오를 AST로 추출한 순수 실행으로 확인했다. 새 코드는 3건 통과, origin/main 코드는 다른 볼륨·구분 불가 2건에서 실패해 누락이 재현됐다.
  - 3.10 AST, profile, public, diff 검사 통과. 실제 pytest 결과는 CI에서 확인한다.
- 남은 #678 범위: task 시작·큰 다운로드 전 중단과 PI 카드, fail 단계 기준, 쓰기 가능한 큰 원자료 위치와 직원 브리핑. 이 issue 전체를 해결한 것이 아니며 시운전의 새 선결로 삼지 않는다.

## #842 — Windows Job wrapper의 표준 handle 전달 (source 87146f4)

- root 최종검토 P1: 제품 `labhq/runner/memory_limit.py`의 `_run_windows_job`은 `subprocess.run(command)`를 표준 handle 인자 없이 부른다. 그래서 Windows CPython 3.12의 `_get_handles`가 모두 -1을 돌려주고, `STARTF_USESTDHANDLES`도 없고 `close_fds=True`라 Chrome은 Node의 stderr pipe를 상속하지 않는다. root가 stdlib source로 확인했고 이전 1ab33f8 기록에서 미확인으로 둔 한계다. 그 상태에서는 Chrome 오류 출력이 사라지고, stdio close는 wrapper 종료만 뜻한다.
- 수정: 제품 코드는 그대로다. test 전용 `tests/support/windows_job_browser.py`(stdlib)가 제품 `_WindowsJob`을 그 source 파일에서 불러 재사용하고, child에 자기 stdin/stdout/stderr만 명시해 넘긴다. CPython은 이 세 handle만 상속 목록에 넣는다.
  - 인자 검사, 오류 줄, 종료 코드(126/127, child 코드), KILL_ON_CLOSE, 메모리 상한, 비상속 Job handle은 제품과 같다. Job 준비에 실패하면 child를 만들지 않는다. 광범위 `close_fds=False`는 쓰지 않는다.
  - driver의 `LABHQ_TEST_PYTHON` 실패 시 차단, 5/3초 정리 예산, 실제 close 판정, 30/170/90초, noskip, 필수 26, Linux 직접 경로는 그대로다.
- 검사: 새 순수 `tests/test_demo_windows_job.py` 11 passed(표준 handle 명시 전달, Job 실패 시 child 없음, 잘못된 인자, 제품 코드·줄, 제품 Job class 재사용)와 Windows inventory 등록. VM fake의 `__dirname`을 driver 폴더로 바로잡았다. bundled Node `--check`, 정적 CI 210 passed, 3.10 AST, profile·public·diff 검사 통과.
- 미확인: 실제 Windows에서 Chrome stderr가 Node까지 오는지, Job close 뒤 2초 안에 stdio close가 오는지. 새 head 전체 CI가 필요하다.

## HANDOFF 작업 배정 정책 현행화 (source c602ad1, base 99f1c49)

- HANDOFF "현재 우선순위"의 "신규 일반 작업 보류 해제" 문장을 현재 PI 결정으로 바꿨다: Codex 총괄 유지, 새 일반 Codex 작업 보류(기존 PR 마무리·긴급만), 개발은 Claude Opus 동시 최대 3, Claude 사용량은 Codex 예산 밖.
- README·manual·AGENTS는 일시적 운영 상황이라 바꾸지 않았다. 예산 수치는 적지 않았다.
- 검사: `check_public.sh`, `git diff --check`, `patch_notes check`, `notes_index --validate`(정적). 제품·process 실행 없음.
- 첫 CI(head `e9ee408`, run 39689292875): 문서 diff라도 HANDOFF 문구 변경과 새 status 파일 때문에 full로 선택됐다. Windows에서 `test_supervisor_process.py`의 감독 조기 종료 1건이 실패했다(#844와 같은 node, 원인 미확정). Linux는 당시 진행 중이었다.
- 통합: 승인 main `e41cfe2`(#843 `bf51c58`, #841 `e7c3159` 포함)를 merge `3ab4f9a`로 명시 통합했다. 충돌은 없고 main 대비 차이는 HANDOFF 한 문단과 이 기록 2파일뿐이다. 새 CI가 한 번 통과해도 #844 해결로 보지 않는다.

## #842/#843 — Windows demo Chrome 소유 Job과 supervisor 시작 실패 진단 (source 1193f98, c8751ad)

- 이전 실패: PR843 첫 CI(18abfff)의 Windows forced 실Chrome 회귀는 소유 launcher exit 뒤에도 stdio pipe가 마감 후 13초 열려 있었다. 소유 범위 밖에 남은 하위가 쥐었을 가능성이 높지만 미확인이다.
- 선택: C1 설계안을 root가 채택했다.
  - Windows에서만 Chrome을 제품의 `labhq/runner/memory_limit.py --windows-job` 아래에서 띄운다. 실소스 확인: 자기 자신을 Job에 넣은 뒤 Chrome을 시작하고, Job handle은 비상속이며, `JOB_MEMORY|KILL_ON_CLOSE`다. 메모리 상한은 소유 목적의 큰 값(64 GiB)이다.
  - 소유 child는 wrapper다. forced 정리는 그 wrapper만 끝낸다. 정상 graceful 경로의 Node kill은 0이다.
  - interpreter는 `test_web_3d.py`가 넘기는 `LABHQ_TEST_PYTHON`(=`sys.executable`)만 쓴다. PATH나 Store alias로 대체하지 않으며, 없으면 profile·server를 만들기 전에 실패한다.
  - Linux 경로는 바뀌지 않았다. 제품 Job 코드도 바꾸지 않았다.
- 유지: 실제 exit·stdio close·socket·server close와 profile 정리로만 완료한다. 5초(3+2), 30/170초, noskip, 필수 26은 그대로이고 회귀 제거는 없다.
- 함께: `tests/test_supervisor_process.py`의 실 gateway 시작 실패(`SystemExit: 2`, 감독 프로세스가 기록 전에 종료) 때 이 test 자신의 supervisor 로그 꼬리를 남기는 진단(C3 patch 검토 후 통합). 원래 예외와 90초 단언은 그대로다.
- 검사: bundled Node `--check`(CJS 2개), fake 갱신(Windows 실행 명령 형태, 실패 5종은 자원 생성 전, Linux 직접 실행). 정적 CI 계약 199 passed, supervisor 순수 검사 1 passed, 3.10 AST·profile·public·diff 검사 통과.
- 미확인: 실제 Windows에서 Job close 뒤 2초 안에 stdio close가 오는지, Chrome 하위가 Job을 벗어나지 않는지, supervisor 재발 여부와 원인. 새 head 전체 CI가 필요하며 이전 head 결과는 재사용하지 않는다.

## #842 — PR843 첫 CI 실패 분리 (source c7a18c6, 첫 CI는 18abfff run 39684337037)

- 수정(3플랫폼 공통): `web_visual_tooling.cjs` startup fake 5·6·7은 내 예산 배분 회귀였다. Chrome이 없는 부분 시작에서도 3초 phase timer를 만들어, 5초 deadline 하나만 허용하는 VM sandbox의 `setTimeout` 단언이 cleanup 안에서 실패했다. 지금은 소유 Chrome을 획득했을 때만 그 timer를 만든다.
- 미해결(Windows): 새 실Chrome forced test 42가 실패했다(5.6초, `demo cleanup incomplete`).
  - 기록: forced(3초) → timeout(5초) → `late-chrome-stdio-close` `after_deadline_ms` 13030. `late-chrome-exit` 행은 없다. 따라서 소유 launcher의 exit는 마감 전에 관측됐고, 그 뒤 stdio pipe가 약 13초 더 열려 있었다(#832와 같은 형태).
  - 정직한 완료 규칙(실제 stdio close 필요)으로는 Windows forced 경로가 5초 안에 끝날 수 없다. 규칙 결정이 필요하다(lookup842-ci-fix0728.md).
- 별도: Windows `test_supervisor_process.py::test_a_real_gateway_runs_as_its_supervisors_child_and_down_ends_both`가 `SystemExit: 2`로 실패했다. 이 PR 범위 밖이라 보고만 한다.
- 검사: bundled Node `--check` 통과, 정적 CI 계약 199 passed, profile·public·diff 검사 통과. 실제 JS·Chrome·CI는 미실행이다.
- 새 head에 전체 CI가 다시 필요하다. 18abfff의 결과와 리뷰는 새 head의 성공 근거로 쓰지 않는다.

## #842 — hosted Windows에서 정리 실Chrome 회귀의 내부 skip 금지 (source 06a8a22)

- 빈틈: focus layout과 visual tooling의 실Chrome test는 `skipReason()`으로 안에서 건너뛰어도 `fail 0`을 출력해 부모 pytest가 PASS한다. 그래서 새 forced 회귀가 실제로 돌았다는 직접 증명이 없었다(C3 hook 0710·peer 0714). critical resizable의 Chrome 조건은 정황일 뿐이다.
- 수정: `test_office_in_node`가 hosted Windows(`os.name == 'nt'`이고 `GITHUB_ACTIONS == 'true'`, `test_windows_job_ci.py`와 같은 판정)에서 두 파일만 TAP `# skipped 0`을 추가로 요구한다. returncode·`fail 0`·기능 단언·node id·30/170초 상한·필수 26은 그대로다. Linux와 로컬의 선택적 Chrome은 넓히지 않았다.
- 등록: `config/ci/windows-boundary-v1.json`에서 `test_web_3d.py` mixed digest 한 줄만 바꿨다. 보호 node와 선택 규칙은 그대로다. 이 PR에서는 바뀐 mixed 파일이라 기존 규칙대로 파일 전체가 Windows에서 돈다.
- 검사: AST로 추출한 순수 실행(가짜 subprocess·os) 8경우가 기대대로였다. 새 head에서는 hosted Windows 내부 skip이 FAIL이고, 이전 head에서는 같은 경우가 PASS였다(빈틈 재현). 정적 CI 계약 199 passed, Python 3.10 AST, profile 검증, public·diff 검사 통과. guard blocked 0.
- 미실행: 실제 Node·Chrome·Windows. 이전 head의 CI 성공은 이 빈틈 전 결과라 새 head 성공으로 재사용하지 않는다. #842는 열어 둔다.

## #842 — 강제 정리 회귀 위치 조정 (source 3dd6180)

- peer P2: 새 실Chrome 회귀가 focus layout 파일의 30초 상한 여유를 줄였다. 그 파일은 #839 진단과 #834·#835·main 로그에서 Windows 실측 6.6~12초다.
- 같은 회귀를 `web_visual_tooling.cjs`로 옮겼다. 이 파일은 driver 자체 계약 파일이고 실Chrome 검사 1개가 이미 있다. Windows 실측은 3.3~5.6초, pytest 상한은 170초다. focus 파일은 `95346f0` 상태(두 demo·기능 단언·시작 trace)와 같다.
- 5초 cleanup, 3초 graceful, 30/170초 상한, `test_web_3d.py`·config, Windows 선택(`test_office_in_node` 보호 node)은 바뀌지 않았다.
- 검사: bundled Node의 parser 전용 `--check`로 변경 CJS 3개를 통과했다(NODE_OPTIONS·NODE_PATH·NODE_COMPILE_CACHE 제거, 프로그램은 실행하지 않음). 정적 CI 계약 199 passed, public·diff 검사 통과, guard blocked 0.
- 미실행: 실제 Node·Chrome·Windows 실행 결과. #842는 열어 둔다.

## #842 — demo cleanup 5초 예산 배분 (source 21afaa2, 앞선 진단 95346f0)

- 구조 원인(확인): 5초 전부를 graceful `Browser.close` 대기에 쓰고, 마감 콜백에서야 소유 child를 `kill()`한 뒤 같은 콜백에서 바로 실패했다. 그래서 kill 결과를 관측할 시간이 0이었다(C3 `lookup-842-cleanup-design-0650.md`).
- 수정: 총 5초 마감은 그대로다. graceful 3초가 지나도 끝나지 않았으면 소유 ChildProcess와 CDP socket만 한 번 닫는다. 그 뒤 남은 2초 안에 실제 child stdio `close`·socket `close`·server close가 모두 와야만 완료한다.
  - kill 반환값, `killed`, 예외 삼킴으로는 성공하지 않는다.
  - exit만 오고 stdio가 열린 #832 형태는 실패다.
  - 정상 graceful 경로의 kill은 0회, forced 경로는 1회다. 마감 콜백은 kill을 다시 하지 않는다.
  - 늦은 관측(`late-*`)은 유지한다.
- 검사:
  - fake 계약(`web_visual_tooling.cjs`): forced 뒤 마감 전 close가 오면 PASS(이전 구조에는 3초 phase가 없어 FAIL). kill이 아무것도 반환하지 않거나, true를 반환하거나, 예외를 던져도 close가 없으면 FAIL. exit만 있고 stdio가 열려 있으면 FAIL. graceful이면 kill 0. phase timer 해제.
  - 실제 Chrome 회귀 1개(`web_office_focus_layout.cjs`의 세 번째 test): 테스트 전용 `testWithholdBrowserClose`로 forced 경로를 타고, `['forced','closed']`를 관측해야 성공한다. 기존 두 demo와 기능 단언은 그대로다.
  - 로컬: 정적 CI 계약 199 passed, JS 정적 괄호 균형, public·diff 검사 통과. guard 유지, blocked 0.
- 미실행·미확인:
  - 실제 Node·Chrome·Windows 결과
  - Chrome이 graceful로 닫히지 않는 내부 원인
  - forced close가 Windows에서 2초 안에 오는지. 하위 프로세스가 pipe를 쥐면 계속 실패한다.
  - 새 회귀가 Chrome 시작 1회와 graceful 3초를 더해 focus 파일의 30초 상한 여유를 줄이는 정도
- #842는 열어 둔다.

## #826 일부 — key_fulltext의 Unpaywall email 전달 (source a6c9e86)

- 변경: `annot_mcp._run_key_fulltext`가 `_run_literature`와 같은 `annot.unpaywall_email`을 `LiteratureAnnotator`에 넘긴다. 설정한 사용자의 핵심 논문 조회도 Unpaywall 합법 OA 위치를 먼저 확인한다. 미설정은 이전 기본값(비활성)과 같다. 기관 세션·PDF 카드·timeout·접근 receipt 경로는 그대로다.
- 검사: 기존 `tests/test_external_wait.py`를 고쳤다.
  - 새 검사: 가짜 `pi@example.org`와 빈 값이 key_fulltext와 fulltext 생성자에 같게 전달되는지 본다. backend는 생성 시점에서 멈추므로 요청이 나가지 않는다.
  - PDF 대기 검사: 빈 값 전달을 단언으로 추가했다.
  - 두 settings stub에 `annot`를 넣었다.
- 로컬: 생성 인자 계약을 AST 추출로 순수 실행했다(HEAD FAIL, 수정 후 PASS). Python 3.10 AST/compile, `git diff --check`, `check_public.sh`를 통과했다. async 검사와 파일 전체는 CI에 맡긴다.
- 남음(#826 open): 셋업의 기관·문헌 질문, 연결 점검, 제공자 표, WoS/Nexus 연동. 실제 조회·기관 인증은 하지 않았다. 매뉴얼은 이미 "허용 OA → 기관 세션 → PI PDF"로 설명하고 있어 바꾸지 않았다.
- 공개 준비(09:21): 승인 main `9d4836c`(#846·#845·#843·#841)를 merge `888b774`로 통합했다(충돌·자체 변경 없음, core 두 파일 hash 불변). 매뉴얼 Unpaywall email 항목에 `key_fulltext`도 같은 email을 쓴다는 한 문장을 더했다(`376af69`, 177,138→177,193자). 새 head의 독립 검토와 CI는 남았다.
- 작업 중 main이 #847(`9588695`)로 움직여 `06dfb1b`를 merge `2a78722`로 한 번 더 통합했다. 매뉴얼은 서로 다른 줄이라 자동으로 합쳐졌고 main 대비 차이는 이 branch의 5파일뿐이다.

## #842 — Windows demo cleanup 실패의 다음 근거 (source 95346f0, base 99f1c49)

- #841 run 39677646617 job 119491647712에서 확정된 사실:
  - focus layout 하위 5검사는 ok였다.
  - `Browser.close` 5초 뒤에도 소유 Chrome의 `exit`가 관측되지 않았다. `stderr_closed=false`, active는 PipeWrap×3, ProcessWrap, Timeout이었다. 그래서 `demo cleanup incomplete`였다.
  - 부모 test는 11.87초에 끝났지만 node는 30초 상한까지 끝나지 않았다.
  - `websocket` pending은 graceful 경로에서 Chrome close를 기다리는 설계에서 나온 것이다. 별도 원인이 아니다.
  - #832 실패는 이와 달랐다. exit는 관측됐지만(ProcessWrap 없음) stdio pipe가 열린 채였다.
- 미확인:
  - Chrome이 5초 안에 끝나지 않은 원인
  - kill 뒤 exit·stdio close가 실제로 왔는지
  - 남은 18초가 어디에 쓰였는지(두 번째 demo 시작이었는지, 남은 pipe가 node를 붙잡았는지). 두 번째 test의 TAP는 없다.
- 그래서 수정은 미루고 근거만 더했다.
  - `closeDemoResources`가 마감 시점에 아직 오지 않은 소유 Chrome `exit`·stdio `close`·CDP socket `close`만 fallback kill 전에 관측하고, `late-*` 행에 `after_deadline_ms`를 stderr로 남긴다.
  - `web_office_focus_layout.cjs`는 두 demo의 시작 단계 시각을 stderr로 남긴다.
  - 늦은 이벤트는 실패를 뒤집지 않고 profile을 지우지 않으며 closed를 표시하지 않는다. timer나 handle도 늘리지 않는다.
  - 5초·30초·job 10분·parallel 7분·필수 26·Windows 선택은 그대로다.
- 검사: `web_visual_tooling.cjs`에 fake 계약 2건을 더했다(실행은 CI). 정적 검사 `test_ci_windows10m` 계약 2줄을 더했다. 로컬 CI 정책 199 passed, JS 괄호·문자열 정적 균형 HEAD와 새 파일 모두 balanced, public·diff 검사를 통과했다. guard는 유지했고 blocked는 0이다.
- 미실행: 실제 Node·Chrome·Windows. #842는 다음 Windows 실패 기록이나 원인 확인 전까지 열어 둔다. #833과 같은 원인이라고 단정하지 않는다.

## #779 — 모델 거부 뒤 새 계획 요청의 meta 보존 (source dec9f80)

- 변경: `Hub._model_refusal_request`가 원 요청 meta(bench `case_id`, 임의 tag)를 새 요청에 복사하고 `revised_from`만 뺀다. `revised_from`은 CP1 수정 요청 찾기(`revised_request_of`)와 `result_storage`의 부모 연결이 읽는 CP1 계보 키다. 거부 재계획의 연결은 기존 top-level `model_refusal_from`이다. 다른 제어 키는 없다(`rounds`는 meta를 그대로 기록만 한다). meta는 `dict[str, str]`이고 `RequestIn`이 새 dict를 만든다.
- 유지: 새 요청 id와 독립 계보, 부모 요청과 meta 불변, CP1 찾기 결과, 거부·그대로 종료·취소·예산 처리, 사용자 매뉴얼.
- 로컬(순수): 새 순수 검사 본문을 `RequestIn`과 두 Hub 메서드의 AST 추출로 in-process 실행했다. HEAD `99f1c49`에서 FAIL, 수정 후 PASS였다. fastapi가 없어 승인 route 검사와 파일 전체는 CI에 맡긴다. Python 3.10 AST/compile, `git diff --check`, `check_public.sh`는 통과했다.
- 범위 밖: #780(continuation 거부 뒤 카드)은 선택이 이전 round 없는 새 연구가 되는 설계 문제라 바꾸지 않았다.

## #660 — `labhq talent`에 빌드된 논문 자문 스킬 표시 (base c16dcdb)

- 변경: `labhq/cli.py`의 `talent_lines`가 기존 contract 줄 뒤에 `paper-skill.json` receipt를 한 줄씩 출력한다. "생성됨"만 뜻하며 사용·효용은 요청 기록에서 본다. 손상·폴더 불일치·대용량 receipt는 값 없이 표시하고 중복은 한 번만 나온다. [참고](../reference/paper-skills-798.md#10-22-labhq-talent의-논문-스킬-목록-660).
- 로컬: 새 테스트 4건의 본문을 in-process로 직접 실행해 PASS를 확인했다(pytest 미설치, 무거운 test 모듈 import는 `markdown_it` 미설치로 불가해 helper만 잘라 실행). Python 3.10 AST, `check_public.sh`, `git diff --check`를 통과했다.
- 미실행: pytest 전체, 실제 Paper2Skill 빌드·스킬 사용·효용, 웹 UI. #660의 선행문헌 목록 UI와 실요청 효용 확인이 남아 있어 닫지 않는다.
- 통합: 승인 main `99f1c49`(#840 `18b4fbd` 후손, 이후는 생성 목차만)를 merge `37f7ce0`로 명시 통합했다. 양쪽 patch-id와 merge-tree가 일치하고 `cli.py`·새 테스트 blob은 그대로다. #840의 직원 env 수정도 그대로 들어 있다.
- 이전 CI: head `7217330` run 39677646617은 Linux PASS, Windows 실패 1건(`test_web_3d.py` focus_layout의 demo cleanup 미완료, Chrome stdio·websocket 미종료로 30초 초과)이었다. `labhq/cli.py`와 새 테스트와는 무관한 기존 정리 실패다.
- 2차 통합: 승인 main `e18af0e`(#843 `bf51c58` 후손, 이후는 생성 목차만)를 merge `98af4d2`로 명시 통합해 #843의 Windows 정리 helper를 받았다. 충돌은 없고 main 대비 차이는 이 branch의 6파일뿐이다. `cli.py`와 새 테스트 blob은 그대로다. 옛 Windows 실패의 해소는 새 CI로만 판정하고, #844(감독 조기 종료) 원인 미확정은 별도로 남는다.

## #813 — 직원 세션의 GitHub 봇 신원 제외 (source 0299a33)

- 변경: `AgentAdapter.staff_env`가 제품 직원 자식 환경의 최종 값을 `LABHQ_COMMIT_AGENT=none`으로 고정한다. 상속값·engine env·task env와 대소문자 변형을 지우고 하나만 남기며, 읽기 전용 직원도 같다. #301 token 제거, 상위 `CLAUDE*`/`CODEX_*` 정리, 부모 env·입력 dict 불변은 그대로다. 개발 CLI·lead·PC 래퍼·OAuth·gateway token은 바꾸지 않았다. [참고](../reference/staff-gh-identity-813.md).
- 로컬(순수): 새 검사 본문을 in-process로 실행했다. HEAD의 이전 `staff_env`에서 4건 FAIL, 새 코드에서 4건 PASS였다. 기존 #301 검사 6건도 PASS다. 추가로 Python 3.10 AST/compile 2파일, `check_public.sh`, `git diff --check`를 통과했다. pytest는 이 PC에 없어 CI에 맡긴다.
- 미실행: 실제 직원 CLI, gh, commit, push, 네트워크 인증, 실제 작업판 표시.
- 문서(9a16138): 매뉴얼 GitHub 토큰 문단에 사용자 설명 한 문장을 더했다(176,983→177,059자). 참고 문서에서는 래퍼 경로 표현을 바로잡았다. source 두 파일은 그대로다.
- 남은 조건: `none`은 봇 주입만 끄고 저장된 사용자 로그인 권한은 그대로다. 실제 GitHub 표시 신원은 미확인이며, #813은 부분 해결로 실제 직원 표시 확인이나 PI 수용 전까지 열어 둔다.

## #823 — 진단 검토 P2 반영과 main 통합 (source 07cbf33, merge 1d6352c)

- 독립 검토(READY739 `out-ci823-peer0442.md`, 대상 `6bddac7`)는 P1 0건이었다.
- P2-1: 진단 중복 키를 잘린 nodeid 대신 원본 nodeid의 sha256(`nodeid_sha256`)으로 바꿨다. 앞 512자가 같은 서로 다른 긴 parameter 실패도 따로 남는다. 출력 nodeid는 그대로 512자로 자른다.
- P2-2: crash 첫 줄은 앞 4096자만 검사하고 300자만 기록한다. `check_public.sh`가 막는 credential 형식(GitHub·`sk-`·`AKIA`·`AIza`·`xox*`·private key)과 Bearer 값을 고정 패턴으로 가린다. 패턴은 literal prefix와 단일 문자 class만 쓴다.
- 진단 파일이 proof·metrics·receipt·재사용 판정과 분리된 점, 실패 50·slow 30 상한, 5초 갱신은 그대로다.
- `origin/main` `ecf1c1b`(#837 `10303fd` 포함)를 명시적으로 merge했다. 들어온 것은 공개 검사 4파일과 생성 목차 2파일뿐이고 충돌은 없었다. 제품 코드 변경은 없다.
- 로컬 검사(guard 유지, blocked 0):
  - `tests/test_ci_windows_phases.py` 114 passed(긴 id 구분, credential 8형식, 20만 자 한 줄 포함).
  - CI 정책 검사 4파일 381 passed.
  - Python 3.10 AST, `check_public.sh`, `git diff --check`, `notes_index --validate`, `patch_notes check` 통과.
- 미실행: consult case와 Windows 실제 진단 기록은 CI에 맡긴다. #837의 원래 예외는 미확인이다.

## #823 — Windows pool 실패 진단과 시설팀 fallback 검사의 대기 단언 (source c873b85, base 86a5c05)

- `scripts/ci_suite.py`의 `StageDiagnostics`가 process별 `diagnostics-<worker>.json`을 쓴다.
  - 진단 전용이며 재사용하지 않는다(`diagnostic_only=true`, `reusable=false`).
  - 실패 stage마다 duration·예외 kind·crash 첫 줄을 즉시 원자적으로 기록한다. 첫 줄은 300자로 자르고 제어문자를 지우며 token을 가린다.
  - 느린 stage 상위 30개를 남긴다. 실패는 50건, nodeid는 512자, duration은 유한한 0~86400초까지 받는다. 중복과 쓰기 실패는 test 결과를 바꾸지 않는다.
- proof·metrics 형식, receipt, 재사용 판정은 바꾸지 않았다. phase 검증은 `collection-`·`metrics-`·`*error*` 이름만 읽으므로 새 파일은 그 판정에 들어가지 않는다.
- `tests/test_consult_restart.py::test_facilities_ask_falls_back_to_the_cso_when_facilities_never_returns`:
  - 실제 `wait_agent_online`을 감싸 `("facilities", 0.2)`가 offline(`False`)으로 끝났고 0.2초 동안 기다렸는지 확인한다. 하한은 clock resolution을 고려했다.
  - CSO fallback, `routed_to`, `state=resolved`, answer의 `from`도 확인한다.
  - `wait_for`는 기존 case처럼 60초 hang 감시로만 둔다. 다른 2~3초 case는 바꾸지 않았다.
  - offline 종료는 이 case가 실제 함수로 확인하고, 등록 신호와 timeout 0은 `tests/test_cso.py:893-906`이 확인한다.
- 이 case는 Windows 선택에 그대로 남는다(cohort). production 대기, MCP 20분, CI job 10분·step 7분, 필수 26, native 실패, 선택 범위는 바꾸지 않았다.
- 로컬 검사(guard 유지, blocked 0):
  - `tests/test_ci_windows_phases.py` 104 passed. 새 순수 진단 검사가 포함된다.
  - CI 정책 검사 4파일 381 passed.
  - `validate_profile`, `include_windows`, Python 3.10 AST, `check_public.sh`, `git diff --check` 통과.
- 미실행: consult case(Hub async)는 CI에 맡긴다. #837 실패가 TimeoutError였는지는 미확인이며, 다음 Windows run의 진단 파일로 확인한다.

## #676 — 조회 상태 기록 독립 검토 반영 (source a1edf63, 검토 대상 b8d452f)

- P1 두 건을 고쳤다.
  - 새 테스트 `tests/test_external_lookup_record_676.py`, `tests/test_external_lookup_flow_676.py`를 `config/ci/windows-boundary-v1.json`의 known_tests에 정렬해 넣었다.
  - 패치노트 entry에 `pr: null`을 넣었다.
  - `validate_profile()`은 통과했다. `include_windows()` 판정은 record 테스트가 변경된 직접 OS API(`os.symlink`)라 Windows 경계에 포함되고, flow 테스트는 일반 logic이라 full Linux와 nightly/manual Windows에서 돈다. cohort·mandatory·mixed 계약은 줄이지 않았다.
- P2 세 건을 반영했다.
  - `run_lookup`이 호출마다 무작위 12자리 `lookup` id를 남긴다. 중간에 끊긴 호출은 같은 도구의 뒤 성공이 있어도 끝난 기록 없음으로 남는다. id가 없는 이전 기록은 기존처럼 도구별로 판정한다.
  - link, 일반 파일이 아닌 경우, 읽기 오류는 `unreadable`로 남겨 파일 없음과 구분하고 보고에도 표시한다.
  - 연구 표시 범위를 문서에 "성공한 단계"로 좁혀 적었다.
- P3는 초 상한만 7일에서 366일로 넓혔다. 도구 이름 허용 목록은 writer의 method 이름과 묶이게 되는 반면 위험이 낮아(패턴이 markup을 막음) 하지 않았다.
- 로컬 검사:
  - 동기 검사 69 passed, 1 skipped(Windows symlink 권한), 2 deselected(async).
  - 정적 CI 정책 검사(`test_ci_parallel_policy`·`test_ci_windows10m`·`test_ci_cohort823`) 199 passed.
  - `notes_index.py --validate`, `patch_notes.py check --base e2fb83c`, `check_public.sh`, `git diff --check`, 7파일 Python 3.10 AST 검사 통과.
  - guard 환경을 유지했고 blocked 이벤트는 0이다.
- 미실행: `tests/test_external_lookup_flow_676.py`와 `tests/test_external_wait.py`(async; key 집합 검사가 `lookup`을 포함하도록 강화됨)는 CI에 맡긴다. 실제 제품, process, MCP, 네트워크, WSL, HPC, 실제 작업판, 실요청은 확인하지 않았다.
- 한계: 직접 쓴 스크립트·curl과 타사 MCP는 범위 밖이고, CP2 전에 실패한 연구 단계의 조회 줄도 한계에 나오지 않는다. #676은 열어 둔다. [근거](../reference/external-wait-676.md)

## #676 — 외부 조회 상태 기록의 결과·보고 연결 (source 4a611ed)

- runner는 단계 turn이 끝난 뒤 `outputs/external_lookup_status.jsonl`을 다시 읽어 `TaskResult.external_lookups`에 도구별 결과를 남긴다. 결과는 ok·시간 초과·실패·취소 건수, 끝난 기록 없음, 경과·상한 초다. 파일이 없으면 필드를 생략하고, 다른 판본이 보낸 잘못된 값은 요약만 버린다.
- link 파일과 link 폴더는 읽지 않는다. 마지막 1MB·5,000줄만 읽고, 한 줄 4,096자·도구 50개·도구 이름 패턴·다섯 상태·유한 초(0 이상 7일 이하)만 받는다. 중복 줄은 한 번만 센다. 줄의 자유 text는 보고에 쓰지 않는다.
- 표시 위치는 작업판 warn, 일반 `## 보고서 경고`, 연구 CP2 카드, 종합 gaps, 최종 `## 한계`다. 원장에 대응 행이 없어도 표시하며, workspace 기록일 뿐 호출·독해 증명이 아니라는 문구를 붙인다.
- 기존 optional/required/CP2 판정과 증거 칸은 바꾸지 않았다. 필수 산출물이 빠진 단계는 그대로 실패한다. optional_outputs의 의미 확장과 공통 조회 CLI는 하지 않았다.
- 로컬에서는 새 동기 검사가 26 passed, 1 skipped(Windows symlink 권한)였고, 기존 `tests/test_output_checks.py`의 동기 검사를 포함하면 59 passed, 1 skipped, 2 deselected(async)였다. guard 환경을 유지했고 blocked 이벤트는 0이다. `scripts/check_public.sh`, `git diff --check`, 5파일 Python 3.10 AST 검사를 통과했다. manual은 176,731→176,944자(LF Unicode)다.
- 미실행: runner fake CLI와 연구 CP2·한계 async 흐름(`tests/test_external_lookup_flow_676.py`)은 CI에 맡긴다. 실제 제품, process, MCP, 네트워크, WSL, HPC, 과거 95분 요청, 실제 작업판 표시와 뒤 단계 진행은 확인하지 않았다.
- 한계: 직원이 직접 쓴 스크립트·curl과 타사 MCP는 공통 상한·기록 밖에 있다. #676은 열어 둔다. [근거](../reference/external-wait-676.md)

## #831 — 공개 전 검사 대기 줄이기

- 전: `check_public.sh`가 추적 파일마다 `find`를 띄워 Windows에서 84초(1,891개) 걸렸다. 목록이 공백·따옴표로 쪼개져 `-`로 시작하거나 공백·따옴표·비ASCII가 든 이름은 비밀값·크기 검사에서 조용히 빠졌다. Windows xargs가 여러 묶음으로 나뉘면 비밀값을 출력하고도 ✓로 끝났다.
- 후: NUL 구분 목록과 묶음 단위 `grep`/`find`로 약 4초. 위 이름들과 여러 묶음에서도 exit 1이다. 없거나 읽지 못하는 추적 파일과 끊긴 링크도 실패로 판정한다. 5MB 경계(`find -size +5M`), 링크 자체 크기, 종료 코드 0/1, 데이터 확장자 규칙은 그대로다. bash와 GNU식 xargs/find/grep(Git Bash, Linux CI)을 가정한다.
- 로컬: 정적 검사 1건의 본문을 순수 실행해 옛 스크립트 FAIL, 새 스크립트 PASS를 확인했다(pytest는 이 환경에 없음). Python 3.10 AST와 compile 통과. Git Bash synthetic 사례 11가지를 옛/새로 대조했고 저장소 자체 검사도 통과했다. 공개 검사는 자기 자신을 승인하는 근거가 아니며, 근거는 사례별 대조다.
- CI 남음: `tests/test_private_paths.py`의 subprocess 계약 검사 12건은 작성만 했다. 줄바꿈 이름·끊긴 링크·권한 000·링크 크기 검사는 POSIX만 실행되며 Linux/Windows CI 결과가 필요하다.

# 논문 스킬 안내·검증 근거 갱신

논문 준비를 기다리지 않고 분석을 시작하는 동작과 #835의 실제 CI 통과 근거를 안내서에 반영했습니다. Refs #671.

- HANDOFF는 현재 동작과 lookup-only 근거를 연결하고 과거 PR 나열을 덜었습니다. 참고 문서의 미검증 비동기18건은 Python3.10 실제 PASS로 갱신했습니다. 실제 UI·청구·원격 종료·과거 세 번째 빌드 원인은 여전히 미확인입니다.
- 문서 두 곳 합계 5,862자→6,084자입니다. 제품·테스트·CI 정책은 바꾸지 않았습니다. 문서 정합·패치노트·공개·diff 검사는 게시 전에 확인합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #835 / #671 — 첫 CI의 fixture 계약 보정

- Waiting/recovery 17건의 필수 plan·step 누락을 공유 순수 builder로 고쳤다. Strict ResearchPlan·승인 hash·제품 동작은 유지한다.
- Attachment 검사는 승인된 upstream 단계의 준비 시간 뒤 실제 DAG 변환 1회·장착·격리·bundle을 확인한다. Ready-step 대기나 늦은 주입을 되살리지 않는다.
- 로컬: 원 fixture 16/24/32 schema 오류→4형태 검증·hash 일치, 새 동기 순수 2 PASS, Python 3.10 AST/compile 4파일. Async·runner 실행은 하지 않았으며 18건의 green은 새 CI가 필요하다.
- Root의 inventory 6건 보정·config는 보존했다. 실제 빌드·비용/효용·진행 화면·원격 종료·세 번째 빌드 원인은 미확인이고 #671을 닫지 않는다. [근거](../reference/paper-skill-waiting-671.md).

새 논문 스킬 검사3파일을 등록하고 수명·복구 계약2모듈을 Windows 경계에 포함했습니다. 정적 선택 검증은 통과했고 새 head CI를 확인합니다. 필수26·10분 상한은 유지합니다. Refs #671.

## #671 — 단계 시작 snapshot·복구 ID P1 보정

- Ready-step은 완료된 스킬만 snapshot하고 미완료면 즉시 생략한다. 같은 단계는 나중 완료돼도 바뀌지 않으며 백그라운드 빌드의 기존 수명·취소 한도는 별도로 유지한다. 6시간을 단계 대기 시간으로 쓰지 않는다.
- Gateway가 recovery await 전에 고른 durable ID에 취소·예약을 연결한다. 복구 결과/미확인 비용과 중복 계상하지 않으며 취소 요청을 종료 증거로 보지 않는다.
- 새 동기 source/pure P1 2건은 기준 `5fa1844e` red→green. 이전 8건은 반복하지 않았다. Python 3.10 AST/compile 8파일, DAG·선정/호출·금액 정책·gateway recovery 14함수 AST 동일 확인.
- Async fake는 CI용이다. 실제 빌드·UI·청구·원격 종료와 당시 세 번째 빌드의 원인은 미확인이다. 두 편은 기존처럼 계획당 선정 상한이며 #671을 닫지 않는다.
- manual 176,703→176,731자(+28). [조건·근거](../reference/paper-skill-waiting-671.md). 다른 owner의 Node/CI 파일은 바꾸지 않는다.

Refs #833

Browser startup 실패 때 server/profile을 정리하고 listen 오류를 반환하도록 고쳤습니다. 실제 main timeout 원인은 미확정입니다. [로그 구분·정리 계약·reuse 거부 근거](../reference/node-main833.md)를 남겼으며 새 PR과 병합 main의 native 검증이 필요합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## #671 — 승인 뒤 논문 스킬 준비·대기 변경안

- CP1 metadata·선정 hash를 유지하며 승인 뒤 DAG에서 최대 두 논문을 병렬 준비한다. 같은 descriptor의 담당 단계는 빌드를 공유한다.
- 기존 task timeout 뒤에는 `unavailable/paper_skill_late`를 단계 입력·receipt에 고정한다. 기존 runner cancel·정리 유예를 쓰며 본 작업 몫을 남겨 논문 비용을 예약한다. 확인 안 된 비용은 지우지 않는다.
- 로컬: 기존 cap 문제 2건 red → 동기 source/pure 8 PASS, Python 3.10 AST/compile 6파일. 기존 DAG 본문·metadata/선정/본문/호출 판정의 AST 동일성을 확인했다.
- async fake lifecycle은 CI용이며 로컬 미실행. 실제 runner·논문·UI·금액/효용·당시 세 번째 빌드는 미검증이다. 기본 6시간보다 짧은 전용 대기 상한은 PI 결정이 필요하다.
- manual 176,305 → 176,460자(+155). [상세·미해결 조건](../reference/paper-skill-waiting-671.md). 로컬 커밋만 준비하며 issue 종료·게시·배포는 하지 않는다.

브라우저에 정상 종료를 요청한 뒤 실제 종료를 확인합니다. [종료 계약·진단 한계](../reference/browser-fixture-cleanup.md)를 참고하세요.

# 개발 문서 현행화

- `HANDOFF`의4-arm 기록기는 #815·#824 병합 상태로 정리했습니다. 주제 선택·운영 runtime과 실제 bench는 남았습니다.
- [CI 기록](../reference/ci-windows-cohort-823.md#실제-ci-측정)은 [run39603485198](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39603485198)의Windows8분14초·선택3680/11037·필수26PASS·병렬3626PASS/28skip을 남깁니다. 다른head·실운영 성공으로 확대하지 않습니다.
- [개발 위임](../reference/lead-operations.md)은10/22부터Codex동시2/Opus3·Claude계수제외와무응답리뷰대체를반영합니다. 제품CSO·직원설정은바꾸지않았습니다.
- README한영·AGENTS의독자구분과직원13개를대조했고일시적인큐·사용량을추가하지않았습니다. 문서/공개·설정정합검사만수행했으며제품/연구실행과테스트재실행은없습니다.

문서길이(문자): HANDOFF3333→3347,운영기준1047→1183,CI규칙9887→10012,조회용CI기록7339→7546. 자동STATUS/patch목차는수정하지않았습니다.

PR #830

브라우저 fixture의 로드 지연을 구간별로 확인하도록 진단을 추가했습니다. 실패 원인을 고쳤다는 뜻은 아닙니다.

- 기존 trace는 desktop 정리 완료 후 phone load 안에서 끊겼습니다. Navigate 응답·demo boot·frame 대기 중 어느 구간인지와 누적 소요 시간은 미확정입니다.
- Resizable fixture에만 monotonic 경과 시간을 최대16행/viewport로 기록합니다. 동기 fake3·기존 wrapper/static11 PASS. 삽입된 trace 외 원래 source·세 subtest·단언·viewport 격리를 대조했습니다.
- 30초 deadline·cleanup·critical/pool/job 상한·Windows 필수 coverage와 fulltext/제품은 그대로입니다. 실제 Node/browser 실행·새 CI 검증은 하지 않았으며 총괄 검토 후 통합할 로컬 커밋입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #828 · #825 병합 후 로컬 통합

- 병합된 #825와 자동 목차 main을 통합했습니다. 충돌 난 selector/suite/cohort test 3파일은 main 정본 blob을 보존했고 profile에는 #828의 known_tests/정확한 파일 cohort 두 줄만 추가했습니다. 기존6 mixed/5 mandatory는 유지합니다.
- feature runtime/tests7개 blob은 검토본과 같습니다. manual 두 절을 보존하고 CI를 현행 설명으로 바꿨으며 #828 receipt 보호는 아직 main 미반영·실제 기관/PDF 독해와 효용 미검증이라고 구분했습니다. manual LF176270→176305(+35).
- source-only profile(known318), 파일/13개 symbol 포함14판정, Python3.10 AST10파일 PASS. source가 같아 기존92 fake를 재실행하지 않았고 실제 collection/native/연구도 실행하지 않았습니다. 상류 #825 CI 통과는 이번 #828 head의 CI 성공을 뜻하지 않습니다.
- #828은 부분 해결입니다. 실제 PDF 범위 검증·OCR·보충자료 연결은 남으며 push·PR·GitHub 병합·원격 CI 조회·배포는 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #823 · PR #825

- 실제 Windows run39598623883은 539초 FAILURE입니다. Critical26 PASS 뒤 pool420초 제한으로 중단됐고 master metrics가 없습니다.
- 반복되는 module alias·scope·import 분석을 content cache로 묶고 경로 resolve를 파일별로 줄였습니다. Helper·변경 범위·profile 판정은 다시 확인합니다.
- 순수/fake 137개 고유 case PASS. 보존된 11033 node ID의 선택을 source만으로 대조해 eligible3680·critical26·pool3654와 제외240파일의 순서·사유를 보존했습니다. 새 test collection이나 native 실행은 하지 않았습니다.
- 기존 coverage·timeout·phase/manifest 증거 계약과 제품은 그대로입니다. 실제 collection 개선 폭·native case 원인·새 CI 성공은 미확인입니다. 로컬 커밋만 준비하며 push/CI 조회는 총괄에게 맡깁니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #829 · #828/#825 후보와의 로컬 준비

- scheduler:none 기존 구현과 test blob을 그대로 두고 fulltext 후보를 충돌 없이 병합했습니다. manual의 세 변경안 설명도 보존했습니다.
- 새 test 하나를 정렬된 Windows 목록과 정확한 경계 cohort에 등록했습니다. 명령·SSH 거부, watcher 차단, 저장 job 보존 계약이며 OS 실행은 fake입니다. 기존 6 mixed/5 mandatory 계약은 유지합니다.
- source-only profile(known319)·포함 18판정·Python 3.10 AST 4파일을 확인했습니다. public·기록·diff 검사도 수행하며 기존 37 mock은 재실행하지 않습니다. 새 통합의 실제 CI/main 반영은 미확인입니다.
- push·PR·CI 조회 없이 로컬 준비만 합니다. 총괄이 최종 #825/main을 확인한 뒤 #828→#829 순서로 다시 통합해야 합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #828 · #825 변경안과의 로컬 준비

- 새 문헌 receipt test를 Windows known_tests에 정렬 등록하고 파일 전체를 owned 경로·UTF-8/hash 경계 cohort에 추가했습니다. 기존6 mixed/5 mandatory 계약은 유지합니다.
- 검토된 feature runtime/tests7개 blob을 보존했습니다. manual 자동 merge는 원문 receipt 절과 PR825 변경안 설명을 모두 유지하며 충돌이 없었습니다. 정책이 실제 main에 출시됐다는 뜻은 아닙니다.
- source-only validate_profile PASS(known318), 새 파일/13개 test symbol의 cohort 포함14판정 PASS, Python3.10 AST10파일 PASS. 기존92 fake와 pytest/실제 collection·native·연구는 실행하지 않았습니다. 실제 Windows/통합 CI 결과는 UNVERIFIED입니다.
- 두 미병합 변경의 호환성 준비만 보존합니다. 최종 PR825와 실제 main이 정해지면 그 exact head 위에서 다시 통합해야 하며 push·PR·CI 조회·배포는 하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR #825 process-tree fixture의 readiness 분리

- immutable 871add022f9f68ba6ca217da1382663db19a645c 기준이다. 기존 sanitizer와 none829 branch, 다른 담당자의 encoding/CI/Windows 코드는 보존했다.
- fixture의 PID 기록을 완성한 뒤 replace로 게시한다. 기존 30초 startup 범위에서 live 3-PID tree를 확인한 후 제품에 process를 반환하고, 제품 drain 시작 뒤 테스트 취소 신호를 허용한다. 준비 실패는 보관한 process를 정리하고 오류로 전파한다.
- 두 arm×세 timeout/cancel case, 실제 PID 수·생존/종료 검사, 1/15/30/10초 경계와 기존 assertion은 유지했다. unknown은 live로 간주하지 않는다. 제품 deadline/cancellation/kill 코드는 바꾸지 않았다.
- 초기 fake 9 failed/2 passed 뒤 최종 추출 helper·fake process/event/clock 18건 PASS. 실제 native 실패는 제공된 CI 로그로만 확인했으며 로컬에서 재실행하지 않았다. 소스상 빈 child 파일 게시와 readiness 이전 deadline race는 가능하지만 실제 실패의 단일 원인은 미확정이다.
- Python 3.10 AST/compile·source-only validate_profile·public·notes·diff와 postcommit 기록 검사는 별도 로컬 보고에 남긴다. push/GitHub/CI 조회·배포·연구/bench 실행은 하지 않는다.

# PR825 — browser fixture close 경계

- Source `e017c44b81104a7c44a6978db43c733314907b2f`: 기존 close는 종료 요청 뒤 실제 완료를 기다리지 않았습니다. 생성 시점부터 socket/Chrome stdio close를 추적하고 server callback 뒤 profile을 지우며, 정리5초 watchdog 실패와 원래 오류를 보존합니다. ChildProcess close는 exit와 달리 stdio 완료까지 구분합니다([Node 계약](https://nodejs.org/api/child_process.html#event-close)).
- Retained CI는 세 subtest와 desktop/phone close 로그까지 완료했지만 Node 종료·최종 TAP summary 없이30초 wrapper에서 실패했습니다. 어떤 handle이 남았는지는 미확정이며 실제 CI가 고쳐졌다고 주장하지 않습니다. 다음 실행에 bounded active-resource 종류와 pending close 이름을 남깁니다.
- 이전 소스의 acknowledgement/오류 전파2건 red → 순수 V8 fake7 PASS. Python wrapper/static11 PASS, CJS3파일 V8 parse-only·Python3.10 AST·정리 외 driver 코드 재구성 대조·source validate_profile/mixed6 일치를 확인했습니다. Node·browser·server·process/thread는 실행하지 않았습니다.
- 세 실제 browser subtest/desktop·phone 단언, 30초 wrapper·native5000ms·critical2/pool7/job600s·phase proof·선택 metadata·S1/S2/제품은 그대로입니다. Public·notes·diff와 최종 hook 뒤 full-range patch 검사를 수행합니다. Guard 세 변수 유지, 로컬 커밋만 준비하며 push/GitHub/CI 조회는 하지 않습니다. CLI 선택 모델 gpt-6.1-sol.

Refs #825

Clean-install 실패 진단의 binary read를 `path.open(mode="rb")`로 명시해 기존 encoding AST 검사와 맞췄다. 검사기·allowlist·진단 상한·원래 시작 실패와 정리 동작은 바꾸지 않았다. Encoding 검사 red→green·기존 bounded fake 포함12건·Python3.10 AST 대조를 통과했다. 실제 Windows 실행·WMI 원인 확인·push·CI 조회는 하지 않았다.

# PR825 — 통합 inventory 누락

- Source `d9165f7b2e3ea491b11588311c365c8ec52bcf64`: 새 clean-install diagnostics test를 `known_tests`에만 추가했습니다. `_run` 대체·소유 임시 JSON·AST 검사 내용을 검토했으며 native cohort를 늘리지 않았습니다.
- 원래 통합 checkout의 `validate_profile()` 누락 실패를 소스 읽기로 재현했고 수정 뒤 통과했습니다. 관련 순수 회귀1건 red→green, mixed 계약6개 digest 일치·미변경을 확인했습니다. Config 의미 변경은 정렬된 목록 한 줄뿐입니다.
- Python3.10 AST/compile·public·notes·diff를 검사하고 최종 hook 뒤 `patch_notes.py check --base origin/main --head HEAD`를 수행합니다. S1/S2·제품·diagnostics/sanitizer·critical v2 proof·changed-test·안전/시간 계약은 그대로입니다.
- Guard 세 변수 유지, 전수 collection·실제 native/process/thread/xdist/browser·설치/서비스·PI/연구/HPC·GitHub/CI 조회·push는 하지 않았습니다. 실제 runtime/CI 성공은 미검증입니다. CLI 선택 모델 gpt-6.1-sol.

# PR #825 게시 sanitizer 비용 보강(로컬 준비)

- 기준은 immutable 71236629ce4658ec8fc7d4988424709baf785a8d. CI/Windows 담당 변경과 scheduler-none-829 branch를 보존했다.
- 구분자만 있는 줄은 전체 길이 C fullmatch로 eligibility를 검사한다. 특수 zone·다른 문자가 있는 줄은 기존 path classifier에 전체 길이로 전달하며 secret/URL/auth·clip 순서는 유지한다.
- 새 work 회귀 4건 red 뒤 지정 pure 검사 43건 PASS. 출력 동등성 6,320개 조합과 늦게 나오는 private/public 경로·credential을 검사했다. 기존 36개 함수(test 32개·helper 4개)와 정확한 입력/출력·CPU 2초 assertion은 AST가 같다.
- 단일 로컬 CPU 관측 0.078125→0.015625초, profile 호출 1,527,855→107회. Linux의 2초 초과는 로컬 미재현이며 새 CI 결과는 미확인이다([근거·한계](../reference/publish-separator-scan-825.md)).
- mock guard를 유지하고 실제 Git/process/Node·native/PI/HPC·네트워크 test는 실행하지 않았다. push/GitHub 쓰기/CI 조회 없이 로컬 커밋만 준비한다.

# PR825 — critical 전수 재수집 제거

- Source `ce4218a1328f2054e415ee90325a74cc6c07bfbc`: critical은 두 native 파일 전체와 필수 browser parameter만 수집합니다. 현재26개·추가 native case를 모두 실행하며 명시 인수/checkout root/기본 discovery/필터 부재를 검증합니다. 모든 setup/call/teardown PASS·no-SKIP 뒤 pool을 엽니다.
- Critical은 좁은 collected/selected만 기록하고 pool의 실제 global full/eligible과 전체 critical parameter 집합·빈 intersection·union을 대조합니다. Execution v2로 v1 혼용을 막으며 profile6/manifest8과 기존 run/attempt/tree/head/workflow/policy/invocation/interpreter·digest·trusted-base 계약은 유지합니다.
- CI step만 critical2분/pool7분으로 조정했습니다. Routine job600초·Linux full·Windows nightly/manual full·native/browser API/assertions/제품 timeout·changed-test coverage·S1/S2 변경은 그대로입니다. Retained f0는 critical26 PASS였지만 job570초 FAIL입니다. 새 head의 실제 절감과 성공≤600초는 미검증입니다.
- Fake phase/workflow135, manifest/재사용95, 별도 offline reader8 PASS. Python3.10 AST/compile·보호 파일 불변·public·notes·diff를 확인하고 최종 hook 뒤 origin/main 기준 patch 검사를 합니다. v7 reader는 별도 로컬 파일이며 root의 바인딩/검토가 필요합니다. 구형 controller/collector는 바꾸지 않았습니다.
- Guard 세 변수 유지, 실제 native/process/thread/xdist/browser·PI/연구/HPC·전수 collection은 실행하지 않았습니다. 로컬 커밋만 준비하며 push/GitHub/CI 조회는 하지 않습니다. CLI 선택 모델 gpt-6.1-sol.

Refs #825

Clean-install 시작 실패의 안전한 진단을 임시 폴더 삭제 전에 남긴다. 두 supervisor의 고정 phase·실패 분류·제한된 시간만 출력하고 원래 오류·실제 up/readiness·정리 계약은 유지한다. 제품 transport·timeout·Job/ACK는 바꾸지 않았다.

기존 실패는 provider_timeout 외 수신/Create 증거가 없어 원인 미확정이다. Fake/static12건·Python3.10 AST·기존 호출/정리 대조를 통과했다. 실제 설치·process·WMI·PI 실행과 CI 조회·push는 하지 않으며 다음 remote clean-install 검증은 총괄이 맡는다.

# PR825 — collection·cleanup checkpoint

- Source `3b814e53419b38c4dff39ae85bc21a4c6c57b99f`: critical 배정만 2→3분으로 바꿨습니다. Job600초·parallel6분·26 critical PASS/no-SKIP barrier와 원래 제품/test timeout, profile6/manifest8을 유지합니다. 기존 Python3.10 fake identity 수정도 보존합니다.
- Phase별 `checkpoints-<worker>.json`은 process-local monotonic hook 관측입니다. Collection(import/selection 포함), 첫 test, stage 시작/완료와 누적 report 수를 구분하며 PASS 증거로 쓰지 않습니다. 미관측 시각은 생략하고 worker 시간을 합산하지 않습니다. Pool snapshot은 hook이 멈추면 갱신되지 않습니다.
- 관련 fake/static 103건 PASS. 최초 red 5건, 중간 mock metadata 누락 3건과 전체 trace를 보존했습니다. Python3.10 AST/compile·workflow 의미 비교·보호 파일 불변·public/notes/patch/diff를 검사합니다. Native/xdist/browser 실행이나 collection 전수 재측정은 하지 않았습니다.
- Retained ZIP의 collection 약85초는 간접 추정입니다. Native 결함·cleanup 원인은 미확정이며 성공한 통합 job≤600초는 root의 원격 CI로 확인해야 합니다. S1/S2 파일·collector는 바꾸지 않았습니다. Guard 세 변수 유지, 로컬 커밋만 준비하며 push/GitHub/CI 조회는 하지 않습니다. CLI 선택 모델 gpt-6.1-sol.

Refs #825

CI artifact 환경이 있을 때만 native fixture의 대기·본문·정리 경계를 case별 JSON으로 기록합니다. 최대64 entries/16 KiB이며 고정 phase·역할·제한된 시간/결과/signature flag만 남깁니다. 진단 기록 시간을 제외한 callable duration도 구분합니다.

Fake/static37건·Python3.10 AST와 원래 assertion/API 인자 대조를 통과했습니다. 기존 native5/web20 계약·대기 시간·retained-handle 정리는 유지합니다. 실제 native 성공이나 제품 결함 해결의 증거는 아니며 통합 CI는 미확인입니다. 로컬 커밋만 준비했습니다.

# PR825 — Linux 3.10 fake Windows identity 회귀

- Source `0d81230ec811b71fbaac79083774259823495af4`: 네 실패는 receipt에 host3.10 버전을 넣어 Windows3.12 검증이 거부한 test binding 오류였습니다. Test runtime proxy와 receipt를 같은 fake Windows3.12/실행 경로로 묶었습니다.
- host3.10/3.12×critical/parallel/barrier/CLI fake 회귀8개 red → 파일 전체59 PASS. 실제 identity의3.10 거부·3.12 허용, 원래 단언 보존·정확한 missing-barrier 실패와 Python3.10 AST를 확인했습니다.
- 수정 범위는 test 파일 하나입니다. Production·workflow·timeout·coverage·S1/S2는 바꾸지 않았으며 Windows critical timeout은 S1 범위로 남깁니다. 실제3.10/Windows·CI 조회·push는 하지 않았습니다. Guard 세 변수는 유지했습니다.
- Public·notes·patch·diff 검사 뒤 로컬 커밋합니다. Root가 최종 통합·게시·새 CI를 맡습니다. CLI 선택 모델 gpt-6.1-sol.

# HPC 비활성 설정의 명령·job 감시 차단 (#829)

- none에서 Scheduler의 제출·큐·상태·취소와 내부 명령 실행을 명시적으로 거부한다. SSH를 구성하거나 subprocess를 호출하기 전 차단한다.
- Runner는 로컬 broker/연결을 유지하고 job watcher 예약·직접 polling을 차단한다. 기존 job·missing count·notified·요청 연결은 보존한다.
- 기존 코드에서 새 회귀 13건 실패, 수정 뒤 관련 mock 37건 통과. SGE/PBS/Slurm/mock 계약을 확인했다. Python 3.10 AST/compile 3파일·public·diff·기록 형식 검사도 통과했다.
- 실제 process/thread·PI·HPC·WSL·연구·CI는 실행하지 않았다. 임의 shell·모델 cache·외부 자동화 격리는 이 수정의 증거가 아니다. 로컬 커밋만 준비하며 push/PR/CI는 root가 맡는다.

# PR825 — critical serial phase 후 나머지 Windows 병렬 검사

- Source `7d9152902bc334538daf83e74e50ee103e4fd578`: native 두 파일25개+resizable browser1개를 먼저 serial로 실행합니다. setup/call/teardown 모두 PASS·no-SKIP을 확인한 뒤 나머지를 4-worker pool에 넘깁니다. Browser TAP도 3 PASS/0 SKIP을 요구합니다.
- 실제 phase별 목록·같은 full/eligible·빈 intersection/정확한 union·worker 결과/시간·run/head/checkout/tree/policy/interpreter를 검증합니다. profile6/manifest8 producer가 업로드 JSON과 job digest를 재검증하며 구형/불완전 결과는 재사용하지 않습니다.
- fake/static708 PASS(collector8 포함) 후 마지막 영향 범위104 PASS, 별도 collector 최종11 PASS입니다. Python3.10 AST·원본 assertions/native/S1 source·기타 job 보존과 public/기록/diff를 확인합니다. 실제 Node/worker/native는 로컬 실행하지 않았습니다.
- Routine10분·원래 test timeout·Linux 전수·Windows nightly/manual 전수를 유지합니다. 예상46+350+50≈446초는 추정입니다. 새 head의 실제 전체 job≤600초·원인·제품/PI 성공은 미확인으로 root 검토/게시/CI가 남습니다. Push·CI 조회·다른 owner/PROBE 통합은 하지 않았습니다.
- [계약과 한계](../reference/ci-windows-cohort-823.md). CLI 선택 모델 gpt-6.1-sol.

# PR825 — worker 진단 수정, Windows 지연 미해결

- Source `2486063`: Linux master/gw0/gw1 진단 파일 계약을 fake로 확인했습니다. 필수 native 누락 실패와 reusable=false를 유지합니다.
- Windows는 wrapper30초/browser60초 불일치까지만 확인했습니다. timeout의 부분 TAP/stderr·원래 예외와 browser 단계 표식을 보존했습니다. 실제 지연 구간·cleanup 완료는 미확인으로 root 검토가 남습니다.
- fake/static 50 PASS, 마지막 worker 3 PASS; JS assertion/body·Python assertion·선택 계약 동일성을 확인했습니다. public/기록/diff 검사, 로컬 커밋만 수행합니다.
- timeout·skip·선택·Linux full·Windows native/nightly full·10분 상한을 유지합니다. 실제435초/2 FAIL은 성공이 아니며 S1 WMI·제품은 수정하지 않았습니다. Push·CI 조회·실제 Node/browser/xdist 실행은 하지 않았습니다.
- [근거](../reference/ci-windows-cohort-823.md). CLI 선택 모델: gpt-6.1-sol.

Refs #828 — 부분 해결이며 이 이슈를 닫지 않습니다.

- tool 원본 receipt의 논문 ID·접근/추출 상태·본문 hash와 방법/QC 줄 범위를 CP1 descriptor에 연결합니다. 승인 뒤 실제 본문·변환 입력과 재사용 fingerprint를 검사합니다. 문자열 full_text·누락/변경·partial/unknown은 자료 요청으로 남깁니다.
- PDF 확보와 추출 완료를 구분합니다. pdftotext 성공도 unknown이며 PDF/HTML의 coverage를 원본에 보존합니다. 지원 JATS 본문 정상경로는 유지하고 coverage v2 이전 기록은 재확인을 요청합니다.
- runtime/tests/manual은 독립 검토본과 8개 Git blob이 같습니다. 해당 검토본의 선택 mock/static92건 PASS를 보존하며 이번 이력 정리에서는 pytest를 재실행하지 않았습니다. 실제 PDF 범위·OCR·기관/PI 요청·문헌 독해·호출·과학적 효용은 UNVERIFIED입니다.
- 잔여: Methods/QC에 필요한 지원 JATS 본문 확보 또는 PDF 범위 검증·OCR·보충자료 artifact 연결이 필요합니다. 후자는 미구현이며 connector 전면 확장은 포함하지 않습니다. CP1 이전 변환과 승인 계획 변경은 추가하지 않았습니다.
- Push·PR 생성·CI 조회·실제 문헌/연구 실행 없이 로컬 공개준비만 했습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR825 · routine Windows 필수 경계와 10분 상한

- source `58129de4`, base `7b6b6ee4`. PR/main Windows는 essential profile v5 한 번만 실행합니다. 정책 변경도 full+preview로 확대하지 않으며 Linux 3.10/3.12·Windows nightly/manual은 전수입니다.
- routine job10분/test7분, 증거·inventory·pin 오류는 빠른 실패입니다. web shutdown·WMI escape/ACK 5개 callable은 collection/선택에서 빠져도 실패합니다. timeout도 실패이며 native assertions·production durability·host lock은 그대로입니다.
- full Linux·설치/validator/MCP·문서 gate와 provenance 함수 AST를 보존했습니다. manifest7은 같은 tree/policy/profile·run/attempt/digest를 요구하며 full Windows/구형/preview를 대신 받지 않습니다.
- guarded pure/fake/static 639 PASS, 강화한 mandatory/workflow node 각1 PASS. cached corpus는 4716→3252/10762(30.2%)·7510 제외, 기존 lock node139 중107이 선택됩니다. lock 규칙을 없앤 것이 아니며 나머지 범위는 full Linux/nightly Windows에 남습니다.
- 과거 cached 추정은 새 native parameter·실제 변경 test 수와 다릅니다. 실제 600초 성공·host wait·완료 job 시간은 미측정이며 기존1124초/2FAIL 실행은 성공 benchmark가 아닙니다. [계약과 한계](../reference/ci-reuse.md).
- local native/process/thread/xdist/PI/연구/bench·전체 suite·API/CI 조회·push/merge 없이 로컬 commit만 준비합니다. root가 S1 수정과 통합한 한 head를 검토·측정합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #825

Owned launcher 실패 시 기존 journal allowlist와 parent exitcode를 최대4096자 JSON으로 출력합니다. 진단 실패도 원래 예외를 바꾸지 않으며 45초·native assertions·제품 동작은 유지합니다.

Fake/AST 17건 PASS. 실제 Windows/WMI 원인·성공은 미확인입니다. Native 실행·push·CI 조회·병합·배포는 하지 않았습니다.

Refs #825 · #824

EOF 대기 대신 길이를 명시하는 stdin 대안을 구현했습니다. 실제 WMI 원인 수정/성공은 미확인입니다.

- 4-byte little-endian header와 정확한 UTF-8 JSON payload를 읽고 EOF 추가 probe를 하지 않습니다. 입력 예산1 MiB를 문자 하한·직렬화 bytes·수신 할당 전에 검사합니다. OS 상한이 아니며 초과는 `request_too_large`, 잘림/encoding/JSON/구조 오류는 WMI 전 고정 실패입니다.
- Command/env carrier는 익명 stdin뿐입니다. 고정 raw/header/payload marker를 추가하고 parser·안전한 fixture allowlist를 7단계에 맞췄습니다. 출력8192 bytes·기존 timing·timeout10/TIMEOUT20·단일 시도·nonce/envhash/identity/Job/ACK/정리 계약은 유지했습니다.
- 수정 전3건 실패 후 wire/model/source20건·관련 fake50건 PASS(70고유 case). 최종 긴 경로 stress와 receiver source 각1건 재확인. 수신 모델은 .NET/PowerShell 실행이 아님을 명시했습니다. 전체 trace는 로컬 산출에 보존합니다.
- Python3.10 AST/compile·변경 외 gate/cleanup/native assertions 동일 대조·public·diff·기록 검사를 수행했습니다. [상한·진단 해석](../reference/wmi-input-phase-825.md)에 한계를 기록했습니다.
- C3·selector/workflow는 변경하지 않았습니다. 실제 process/thread/PowerShell/WMI/WSL/native/CLI/PI/runtime/연구/bench/HPC 미실행. 로컬 커밋만 준비하며 push·PR/API/CI 조회·병합·배포 없이 독립 검토와 통합을 총괄에게 넘깁니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR825 · sanitizer 실제 CPU 초과 후 제품 수정

- source `ea6a8731`, tested base `204cb1e0`. 실제 Linux3.12 run39548511101/job119052128054는 원문 동일성 PASS·process CPU2.329초 FAIL입니다. wall만의 문제라는 설명을 폐기하며 초선형 원인은 입증하지 못했습니다.
- URL 패턴의 필수 문자 부재 시 불가능한 regex 탐색만 생략하고 literal path의 구분자 run을 고정 문자 클래스 search로 처리합니다. 공용 scanner·판정/출력·731072자·limit=None·strict CPU<2와 기존 31개 test/helper AST를 유지합니다.
- red5/동등성2 PASS → 관련 pure24 PASS. 경로1584·URL180조합 동등이며 같은 로컬 장비의 원본 CPU0.156초×2 → 수정0.078초×2입니다. clock 해상도15.625ms이며 Linux 실패나 실제 병렬 부하는 재현하지 않았습니다. Python3.10 AST·public·기록/diff를 검사합니다.
- S1·CI selector/workflow/profile·Windows/full backstop·preview/exact-proof는 그대로입니다. native/process/thread/xdist·PI/연구/bench·fullsuite와 push/API/CI 조회/merge를 하지 않았습니다. 새 head CI와 root 검토는 미완료입니다. [근거](../reference/ci-windows-cohort-823.md).

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #825 · #824

WMI timeout 진단만 분리했습니다. 원인 수정이나 실제 Windows 성공을 주장하지 않습니다.

- `ReadToEnd()` 반환 직후 `input_read`, JSON 변환 뒤 기존 `input_accepted`를 출력합니다. [진단 의미와 한계](../reference/wmi-input-phase-825.md)를 따릅니다.
- 순서 parser·fixture allowlist는 5단계에 맞췄습니다. 결과 포함 frame 상한은 6개이며 기존 8192 bytes·숫자/timing·redaction 상한은 유지합니다. 성공 code/PID·timeout 부분 결과 처리와 보안/정리 경로는 그대로입니다.
- 수정 전 4건 실패 → 명시한 fake/string 계약 34건 PASS(로컬 0.27초). 완료 전 각 timeout·6frame/byte 경계·잘린/중복/불일치·비밀값 부재와 journal 예외 보존을 확인했습니다. 전체 trace는 로컬 산출에 보존합니다.
- Python3.10 AST/compile·진단 외 source/native assertions 동일 대조와 public·diff·기록 검사를 수행했습니다. C3 CPUclock/CIpolicy·테스트는 변경하지 않았습니다. 실제 process/thread/PowerShell/WMI/PI/bench 미실행, native CI 미확인입니다.
- 로컬 커밋만 준비했습니다. Push·API/CI 조회·새 PR·병합·배포 없이 독립 검토와 C3 변경 통합을 총괄에게 넘깁니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR825 · sanitizer CPU 예산 회귀 / 로컬 수정

- source `4128943a`, base `c29e2e3a`. 실제 Linux3.12 job119038816204/run39544266122는 원문 동일성을 통과했고 wall2.092초로 strict<2만 실패했습니다. 4 workers·10724 PASS/43 skip이며 CI CPU 값은 없습니다.
- 해당 separator 입력에서 초선형 재스캔 근거는 찾지 못했습니다. wall-clock-sensitive 비용 assertion으로 분류하되 scheduling/실제 CPU 기여는 미확정입니다. 제품 sanitizer·S1·CI 정책/Windows/backstop/preview/reuse는 변경하지 않았습니다.
- 731072자·limit=None·정확한 출력·strict CPU<2를 유지합니다. module-local fake clock 5개가 wall 대기와 CPU 예산·출력 불변식을 구분합니다. old clock red3, 수정 후 명시한 pure-string/security 16 PASS·guard env 유지.
- Python3.10 AST·public·기록/diff를 검사합니다. 기존 823 evidence는 재사용하며 whole module/suite·native/process/thread/xdist·network·PI/연구/bench를 실행하지 않았습니다. 실제 CI 원인 전체와 새 head CI는 미검증입니다. [근거](../reference/ci-windows-cohort-823.md).
- 로컬 commit만 남기며 push/PR/API/CI 조회/merge는 하지 않습니다. publication은 root review와 S1 WMI 판단까지 보류합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# Windows cohort #823 · main 통합과 빈 association 증거 / 미게시

- source `e09cbb81`. `5989d0a9`에서 main `56914c7b`를 충돌 없이 통합했습니다. PR824 `8baefd07`은 ancestor이며 S1/제품/README/manual을 보존했습니다.
- 실제 PR run 39538379069의 pull_requests=[] 때문에 검색 후보가 사라졌습니다. main 39541901246의 full은 관측했지만 downstream trust 증거 전체의 적합성까지 재실행해 확인한 것은 아닙니다.
- 정확한 빈 목록만 authoritative `/pulls/N`의 merged/main SHA·시각·번호·repo·head/base와 다시 결합합니다. 이후 run/attempt/workflow·trusted-base/source policy·성공 gate·ordered checkout parents/tree·현재 epoch artifact/digest를 모두 검증합니다. 기존 nonempty 불일치나 missing/malformed는 full, v3·preview는 재사용하지 않습니다.
- guarded mock/static 387 PASS, 마지막 fake subset 91 PASS(추가4개 포함). old discovery red1·중간 fixture alias red1도 보존했습니다. main 변경/새 계약 200개만 serial collection했고 실제 fixture를 실행하지 않았습니다. guard 3변수 유지.
- 정적 선택 4716/10762(43.8%)/175파일로 목표 미달, host lock 139개 모두 선택·paper 보호8/preview 유지. local diff 기반 preview 예상5114개는 실제 event 증거가 아닙니다. [정책과 한계](../reference/ci-windows-cohort-823.md).
- 로컬 fetch/merge/commit만 수행합니다. push/PR/CI 조회/병합·native/process/thread/xdist/PI/연구/bench 실행은 하지 않았습니다. 실제 재사용·Windows 성능·artifact 왕복은 새 exact-head CI 전까지 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# Windows cohort #823 · 보호 복구와 preview / 미게시

- source `28b6a1ba`, 이전 head `5728bf0c`, base `c8a33a84`. paper-skill의 Windows sandbox/private/link/hash 8개 누락을 red 8→green 8로 확인하고 복구했습니다. 제품·S1 파일과 host lock/scheduler는 변경하지 않았습니다.
- mixed source pin 아래 Bash 텍스트 35·fake clock/수치 6·JS 문자열 1개만 더 제외했습니다. init 104와 실제 경로/PowerShell/설치/security/Node 경계는 유지합니다.
- 정적 예상 4,592/10,638개(43.2%)/173파일, 명시 보호 범위 3,850개(36.2%)로 목표 미달입니다. 현재 local main diff의 변경 test 포함 preview 예상은 4,990개이며 실제 event 증거가 아닙니다. host lock 139노드/23파일은 모두 선택합니다.
- same-repository 정책 변경 PR만 필수 Windows full 성공 뒤 별도 preview를 실행합니다. exact checkout/ordered parents/workflow·실제 변경 경로를 유지하고 profile/receipt/artifact/summary를 분리합니다. reusable=false이며 manifest producer/consumer는 preview 증거를 거부합니다. 일반 PR/main·nightly/manual에 중복 suite를 붙이지 않습니다.
- guarded mock/static 475 PASS; red/green·235개 CI-only serial collection·inventory 로그를 보존했습니다. 마지막 preview/encoding 77 PASS와 Python3.10 AST·workflow 보존 비교·공개/기록/diff를 확인했습니다. 최종 로그는 OUT에 기록합니다. 3개 guard env는 유지했습니다.
- preview는 warm 후속 실행이며 실제 Windows/xdist/host wait·속도는 미검증입니다. full Linux·Windows nightly/manual·safety/install/validator/MCP와 기존 trust 조건은 유지합니다. push/PR/CI 조회/병합은 하지 않았습니다. 총괄의 #824 이후 main 통합·검토·원격 실행이 남습니다. [근거](../reference/ci-windows-cohort-823.md).

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# Windows cohort #823 · 로컬 후속 / 미게시

- source `9c70d836`·`686b5acf`, 이전 head `6d1eb7eb`, base `c8a33a84`. 제품·S1/S2·README/manual·생성 기록은 변경하지 않았습니다.
- 기존 collection과 바뀐 CI 계약 175개 재수집으로 10,578개 중 4,566개(43.2%)/171파일 선택을 예상합니다. 명시 경계만 3,884개라 이번 검토로는 30%·2–3천 개 목표 미달입니다. 모든 가능한 정책의 최소 범위를 증명한 수치는 아닙니다.
- 혼합 4파일은 327→15개 경계를 남깁니다. 변경 test는 포함하고, 공유 helper/fixture data·policy 변경과 잘못된 range는 full입니다. Linux 전수·Windows nightly/manual 전수는 유지합니다.
- host lock은 22파일/456노드→23파일/139노드입니다. shutdown watcher의 실제 snapshot 경로를 보호하고 durable writer의 포트 0과 무관한 node는 잠그지 않습니다. setup~teardown과 fixture 별칭·autouse/usefixtures를 검사합니다.
- guarded mock/static **415 PASS**. Python3.10 AST 6파일·JSON·공개·기록/diff를 검사합니다. 구형 manifest/profile/artifact 거부와 순수 node/native sibling의 cache 분리도 검증했습니다. guard 환경과 전체 red/intermediate/green 로그는 로컬에 보존했습니다.
- 정책 변경 PR의 첫 실행은 full입니다. 이후 유효 range의 원격 Windows 실행 artifact로 실제 선택·worker 수·시간·경합을 확인해야 합니다. static 수치로 성능 향상을 주장하지 않습니다.
- **push/PR/CI 조회/병합 없음.** 총괄의 #824 이후 main 통합·exact-head 검토·게시·native CI가 남습니다. 실제 process/thread/xdist/PI/연구/bench 실행은 하지 않았습니다. [근거와 한계](../reference/ci-windows-cohort-823.md).

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #799 · PR #824

새 Windows 실패는 이전 supervisor 조기 종료와 달리 WMI spawn broker에서 발생했습니다. 원인 수정이 아니라 진단 보강입니다.

- Broker는 provider timeout·protocol·native 오류와 실패 단계를 구분하고, provider가 반환한 경우에만 returncode를 기록합니다. 명령·PowerShell 원문·환경값·원문 stdout/stderr·예외 메시지는 새 진단에 넣지 않습니다.
- 시작 fixture는 detached-launch journal 끝 16 KiB에서 최대 4개 행의 allowlist 사실만 출력합니다. 누락·읽기 실패·잘못된 JSON과 잘린 tail을 표시하며 원래 예외를 유지합니다.
- 새/수정 회귀 11건 red→green, 관련 fake 계약 20건 PASS(총 31개 고유 case); 최종 journal 5건을 다시 확인했습니다. 기존 UTF-8/재채점 검증은 변경 없이 보존하며 반복하지 않았습니다. 전체 오류 trace는 로컬 산출에 남깁니다.
- 성공 경로의 nonce/envhash·identity·Job·ACK 순서, deadline·단일 시도, retained-handle 및 승인 후 정리는 그대로입니다. 기존 native 함수/assertions·시작/health/PID 순서는 AST로 대조했고 Python 3.10 문법/compile 4파일과 public·diff·기록 검사를 수행했습니다.
- Windows API·process/thread·WMI/WSL·PI·연구·bench·HPC는 실행하지 않았습니다. 실제 원인과 새 head의 native CI 결과는 미확인입니다. C3 CI/policy는 변경하지 않았으며 push·PR 변경·CI/리뷰 조회·병합·배포 없이 로컬 커밋만 준비합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #799 · PR #824

Windows CI 회귀 중 테스트의 UTF-8 읽기를 고치고 시작 실패 진단을 보완했습니다. 제품 동작은 변경하지 않았습니다.

- `test_bench_metrics.py`의 JSON 읽기 3곳에 UTF-8을 명시했습니다. receipt/sidecar 바이트 보존·미수집과 0 구분·재계측 금지 assertions는 유지합니다. 전역 locale/UTF-8 설정을 바꾸지 않고 fake cp1252 기본 reader로 두 재채점 실패를 재현했습니다.
- `_start_gateway` 시작 실패 때 임시 supervisor/gateway 로그를 stderr에 남기고 원래 예외·traceback을 다시 전달합니다. 로그 누락·읽기 오류가 원래 실패를 가리지 않도록 fake 회귀 6건을 추가했습니다. 기존 모든 native 함수·assertion과 start/health/record 순서는 AST 대조로 보존을 확인했습니다.
- 수정 전 8건 실패 → 관련 fake 24건 PASS(로컬 1.62초). Python 3.10 AST/compile 2파일·public·diff·기록 검사를 수행했습니다. 전체 traceback은 로컬 산출에 보존합니다.
- Windows 실제 gateway 실패는 shutdown 전 supervisor 종료이며 로그 본문이 없어 원인 미확인입니다. 제품 CLI·Job/ACK·timeout·retry는 바꾸지 않았고 native fixture·실제 process/thread·PI·연구·bench·HPC는 실행하지 않았습니다. 최종 Windows 검증과 원인 확인은 CI에 남습니다.
- C3 소유 CI/policy와 다른 담당자 변경은 유지했습니다. 로컬 커밋만 준비하며 push·PR 변경·CI/리뷰 조회·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# Windows CI cohort #823 · 미게시

Refs #823 · source `15b12a819848c2f437ccc0451337c6b08b8f2270` + `3e827e4cf8d4644db3eedbf35de93b0fe080c7ce` · base `c8a33a84`.

- 전역 runtime hash 만료를 제거하고 명시한 OS/안전 cohort·변경 test·38개 검토 source/helper pin으로 선택합니다. checkout/ordered parents·push range 불명, 공유 fixture/policy 변경은 전수입니다. v4 manifest는 기존 신뢰 조건과 main before=검증 PR base도 요구합니다.
- 고정·미확정 listener·전역 scan과 참조된 관련 helper는 setup~teardown을 잠급니다. 전용 subprocess·임의 포트·미사용 import와 단순 설정 객체 생성은 전역 lock 근거에서 뺐습니다. 실제 Runner broker 포트가 입증되지 않으면 보수적으로 잠급니다. proc_tree/adapter의 검증한 전체 process-table wrapper 진입점도 보호합니다.
- guarded mock/static **565 PASS**, 직렬 collection **10,554개**. 기존 artifact와 제품 node 집합이 같습니다. CI 계약 35개 node를 v2 계약 128개로 교체·추가했습니다. 실제 process/thread/xdist/서비스·연구는 실행하지 않았습니다.
- 정적 예상 **9,270개(87.8%)/272파일** 선택, **1,284개/38파일** 제외. lock 후보는 **77파일/2,625노드→22파일/456노드**입니다. 20–30% 목표는 미달이며 미분류 5,837개를 임의로 제외하지 않았습니다. 현재 파일 단위 보호 cohort만 3,433개라 추가 node/cohort 검토가 필요합니다.
- 보존 main run 39510024326 artifact의 대기 합은 **2,949.22 worker-seconds/77파일**입니다. 전달받은 2,450초와의 차이는 미확정입니다. 새 Windows/병렬 실행·속도·재사용 실증은 미검증입니다.

[정책·상세 한계](../reference/ci-windows-cohort-823.md). Linux 전수, Windows nightly/manual 전수와 제품 안전·내구성·worker 격리는 유지했습니다. 다른 owner 파일과 기존 #793 branch는 보존했습니다. **push·PR 생성·CI 조회 없이** 총괄의 최신 main(#799/docs) 통합·정적 검토·native CI를 기다립니다.

Refs #799, #739

최신 main c8a33a8을 정상 merge해 #793 CI 정책과 #821/#822 변경을 보존했습니다. Recorder 소스는 기존 검토본 그대로이며 실제 bench는 실행하지 않았습니다.

- HANDOFF의 닫힌 PR 큐·Paper2Skill 병합 전 표기를 정리했습니다. 매뉴얼은 Linux 전수·Windows 기본 경계/선택 범위 검증 실패 시 전수·nightly/수동 전수를 구분합니다. README 한영판·C3 정책/CI 소스·생성 목차는 main과 같습니다.
- 집중 fake/coroutine/static 29건 PASS(로컬 1.98초). 성공/실패 spawn·누락과 0·arm 격리·재채점 receipt/sidecar 보존을 확인했습니다. Python 3.10 AST/compile 6파일과 변경 runtime 2파일 정답 누출 정적 검사 PASS.
- LF 문자 수: HANDOFF 3349→3333(-16), manual 175613→175667(+54); README 11686, 영문 17627로 변동 없습니다. main에 반영된 Paper2Skill 설명은 유지했습니다.
- 기존 git subprocess fixture 차단은 재실행하지 않았습니다. 실제 process/thread·PI·연구·bench·HPC·원자료·network fixture 미실행이며 속도·효용과 이번 head의 Linux/Windows CI는 미검증입니다. #799는 OPEN입니다.
- Public·diff·patch-notes·notes 결과는 로컬 검증 산출에 기록합니다. Push·PR 생성·CI 조회·리뷰 호출·병합·배포 없이 총괄에게 로컬 head를 넘깁니다.

- 총괄 독립 정적 검토에서 코드 P1=0을 확인했습니다. 참고 계약의 기록기 미구현 표기를 opt-in 구현/실측 미실행으로 바로잡았습니다.
- 동일 main Git 본문/LF 기준 README 한영·manual 합계 204926→204981자입니다. 실제 bench·효용은 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR #793 exit logging 전용 fixture (local only)

- 기준 fc70f4a6. 새 trace는 powershell_started만 관측했고 입력 read/EOF·JSON·스케줄링·관측을 구분하지 못한다. Create 결과·provider timeout 원인은 UNVERIFIED다.
- logging test 한 건에만 일반 Popen과 정확한 fake gateway 명령용 launcher를 사용한다. 기존 assertions 8개·teardown과 다른 기존 함수/fixture 20개는 그대로다.
- 실제 breakaway·nested Job 생존·거부·ACK 전 차단 gates와 제품/CI policy는 변경하지 않았다. 이것은 WMI timeout 수정이나 native 성공 증거가 아니다.
- 새 회귀 5 failed → 관련 fake/static 74 passed. Python 3.10 AST·public·기록/diff 검사를 수행한다. real process/WMI/PI/WSL/연구/bench는 실행하지 않았다.
- root exact-head 검토 전 push 금지: commit만 한다. CI 조회·재시도·PR/thread 쓰기·병합·배포 없음. [근거·유지한 gate](../reference/exit-logging-fixture-793.md).

# PR #793 자동 조회 deadline 검사 분리

Refs #788 · source `6be0f39` · base `d3cc0ba4` · 미게시.

- [Linux3.12 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39491594456/job/118866006138)는 limiter의 Event.wait가 outer 1초 watchdog에 취소돼 TimeoutError가 된 경로입니다. 특정 지연 원인과 제품 결함은 미확정이며 #800과 같은 원인이라고 판단하지 않았습니다. 변경 전 세 case는 로컬 3 passed로 CI red를 재현하지 못했습니다.
- test-only module-local timer에서 operation awaitable을 명시 등록하고 phase별 start/tick/heartbeat event를 새로 만듭니다. real coordinator는 그대로이며 완료 phase마다 가상 20ms를 소비합니다. 기존 80ms logical budget의 remaining은 80→60→40ms, 같은 deadline은 100.08입니다. hung operation의 시작·suspend를 확인한 뒤 가상 deadline에 취소하고 await로 정리합니다. 60초 production cadence는 유지해 80ms 안에 heartbeat를 만들지 않습니다.
- outer watchdog은 logical deadline과 분리한 10초로 한정했습니다. coordinator 전체에는 동기 durable 기록도 포함되므로 1초 wall 시간을 logical timeout 증명으로 사용하지 않습니다. bound 증가만으로 통과시키지 않고 pending/cancel·모든 operation/progress task 완료·ToolError+NOT_EVIDENCE·무승인·phase 순서·progress/저장 status 일치를 검사합니다. 완료된 작업의 거짓 timeout 거부와 시작 통지 누락 중 caller 취소/정리도 별도 검사합니다.
- `tests/test_external_wait.py` guarded mocked coroutine **23 passed**. 기존 shared deadline/retry/PDF human-wait 제외/취소/progress 검사를 보존했고 실제 network/process/thread는 실행하지 않았습니다. Python3.10 AST·public·기록·patch-note·diff를 검사하고 native red·baseline·intermediate/final 전체 로그와 guard 환경을 보존했습니다.

제품 external_wait/annot_mcp와 classifier·CI trust/workflow/isolation·Windows·CPU guard·README/manual/다른 owner 파일은 변경하지 않았습니다. 10초 watchdog은 cooperative coroutine 교착 감시이며 OS/동기 I/O 시간의 강제 중단 보장은 아닙니다. 새 native CI는 미검증이며 **push·GitHub 답글/resolve·CI 조회 없이** root의 exact-head 검토와 게시를 기다립니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR #793 한국어 설명문 허용 범위 축소

Refs #788 · source `8b8e883` · base `205c51af` · 미게시/P1 검토 대기.

- `조립하고 결과를 확인합니다.→분해하고 결과를 확인합니다.`는 기존 실제 parser에서 양방향 document-only였습니다. 임의 Hangul prefix와 행동/보고 꼬리를 없애고 고정된 문서 주어+정도 표현+형용사 하나만 후보로 남겼습니다. 붙여 쓴 지시문 제목도 전체 문단 보호 대상으로 보냅니다. 도구/행동 denylist를 늘리지 않았습니다.
- 실제 MarkdownIt의 targeted red는 **37 failed / 13 passed**, 수정 뒤 관련 pure/fake/static **563 passed**입니다. 생소한 action chain·단순 행동·띄어쓰기·목록/제목·앞뒤 설명문을 섞은 경우를 양방향 검사했습니다. 형용사 prose와 같은 Markdown reflow는 유지하고 새로운 형용사 형태나 붙여 쓴 fragment의 기존 full fallback도 넓히지 않았습니다.
- 실제 README의 기존 동작 서술 수정은 이제 full을 요구합니다. 원래 입력은 그대로 negative로 강화하고 같은 README의 별도 형용사 변경을 positive로 추가해 기존 code/link/HTML 예제 주변의 유효한 문서 절감을 유지했습니다. README 원본과 기존 normative·markup·provenance/consumer·Windows·CPU 보호는 변경하지 않았습니다.
- Python3.10 AST·public·기록·patch-note·diff를 검사했습니다. 실제 parser/positive control과 guard 환경을 확인하고 전체 red/green·중간 contract 실패 로그를 보존했습니다. 기존 함수/class 중 `descriptive_prose`만 바뀌었고 나머지 24개의 AST는 같습니다.

한계: 무해한 한국어 행동/보고 서술도 보수적으로 full입니다. 자연어 전체를 판별한다거나 P1 전체가 해결됐다고 주장하지 않습니다. 새 native CI는 실행/조회하지 않았으며 **push·답글·resolve·PR 수정 없이** root의 combined exact-head 검토를 기다립니다. 로컬 native/process/thread/실제 git fixture/xdist·PI/WSL/연구/bench/HPC·전체 suite는 실행하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR #793 command-shaped prose 보호

Refs #788 · source `2620947` · base `678abc7c` · 미게시.

- P1 4321519440의 `assemble→publish`는 기존에도 full이었지만 `assemble→clean/bundle`은 실제 MarkdownIt에서 document-only였습니다. 새 도구·동사를 열거하지 않고, 수정 가능한 문단을 작은 설명문 grammar로 제한했습니다. 분류하지 못한 문단은 inline signature와 container 전체를 묶어 양방향 수정·추가·삭제를 full로 보냅니다.
- 영어 copular 설명·짧은 문서 명사구와 검토한 한국어 설명/기록 서술형은 유지합니다. 그 밖의 무해한 prose·혼합 언어도 full일 수 있습니다. 자연어/명령을 완전히 해석하는 규칙은 아닙니다. 별도 설명 문단과 동일 Markdown soft reflow는 유지하며 기존 normative·code/link/HTML·dependency 오류 보호는 바꾸지 않았습니다.
- frozen `678abc7c`와 실제 parser의 최종 회귀는 **63 failed / 97 passed**, 수정 뒤 관련 pure-string/fake/static **512 passed**입니다. 원래 Gradle·생소한 도구/동사·전치사/다중 문장/제목/목록·한국어 지시문과 일반 prose를 양방향 검사했습니다. #labhq heading의 기존 pure/fake-git 회귀도 그대로 통과했습니다.
- bare lane Python의 `ModuleNotFoundError: markdown_it`는 별도 실패 로그로 보존하고 PASS에 포함하지 않았습니다. 기존 task dependency의 실제 parser를 확인한 뒤 green을 기록했으며 mock guard 환경과 전체 red/green trace를 유지했습니다. Python3.10 AST·public·기록·patch-note·diff 검사를 수행했습니다.

CI provenance/tree/profile/run/attempt/digest/consumer, workflow·Windows/native safety·CPU guard와 다른 owner의 코드/문서 및 `678abc7c` merge graph는 보존했습니다. 새 HEAD CI는 실행/조회하지 않았습니다. **push·PR 본문·thread 답글/resolve는 하지 않았으며 root가 exact-head 검토와 게시·최종 CI·병합을 맡습니다.** 로컬 native/process/thread/xdist·실제 git fixture·PI/연구/bench/HPC·전체 suite는 실행하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #788, #793

C3 CPU scan 개선, 로컬 검토가 끝난 Windows fixture 전달 수정, README 한영·매뉴얼의 Paper2Skill 설명을 기존 PR에 함께 통합했다. 원본 source commit ID를 보존해 패치노트 근거를 유지했고 실제 검토된 head에서 새 CI를 받는다.

Windows fixture 수정은 실제 함수 AST/fake 검증과 독립 정적 P1=0이며 native 실행은 하지 않았다. scanner 동등성·CPU 측정은 C3의 로컬 근거다. 실제 Windows·manifest/재사용·WMI timeout 해결·연구 효용은 미검증이다. 사용자 매뉴얼의 순간 개발 문구는 상시 진단 안내로 바꾸고 상세 근거는 reference에 둔다. 자동 STATUS/패치노트 목차는 직접 고치지 않았다.

# PR #793 scanner CPU 비용 수정

Refs #788 · source `a6a52b1` · tested base `2642fa16`.

- [Linux3.12 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39473571463/job/118803767425)는 `touches`의 None 검사를 통과한 뒤 CPU 2.019318592초로 `< 2`를 넘겼습니다. 경합 원인이나 superlinear 경로가 확인된 것은 아닙니다.
- `_token_candidates`의 chunk 끝·colon run 끝 탐색 두 곳만 bounded 단일 character-class regex로 바꿨습니다. drive colon·URI·trim·escape·정규화와 731,072자 입력·CPU clock·결과 assertion·엄격한 2초 한도는 그대로입니다. 다른 함수·class 94개의 AST도 같습니다.
- 기존 구현과 AST가 같은 frozen oracle을 기준으로 생성 문자열 3,107개의 후보 순서·scan metadata·lexical zone 판정이 일치했습니다. URI/drive/UNC/ADS/traversal·인접 separator·따옴표/escape·허용/제한 경계와 기존 clock 회귀를 포함해 guarded mock/pure-string/static **59 passed**입니다. 변경 전 비교 회귀 13개도 통과했으며 로컬에서 기존 CI 실패를 재현했다고 주장하지 않습니다.
- Windows/Python3.12.10에서 같은 긴 입력을 구현별 7표본·표본당 8회, 순서를 번갈아 측정했습니다. `touches`의 호출당 CPU 중앙값은 **0.109375→0.0703125초(35.7% 감소)**, token 추출은 **0.04296875→0.005859375초**입니다. 로컬 측정이며 CI 소요 시간·복잡도 증명이 아닙니다.
- Python3.10 AST·integrations·public·기록·patch-note·diff 검사를 수행했습니다. guard 환경과 native 실패/로컬 전체 로그를 보존했습니다. 새 fixture의 unquoted-space 기대값 오류는 기존 token 경계와 대조해 바로잡았으며 기존 test/assertion은 수정하지 않았습니다.

CI workflow·manifest의 tree/ordered parent/run/attempt/digest/trusted-base 조건·worker 격리·Windows/WMI 보호는 변경하지 않았습니다. runtime fingerprint는 갱신하지 않아 Windows는 보수적인 full fallback 대상입니다. Linux 전수·Windows nightly/dispatch 전수·설치/validator/MCP gate도 유지합니다.

한계: 새 head native CI, Windows 결과와 manifest/no-reuse 실증은 미검증입니다. snapshot 갱신 시점과 CI CPU 초과의 환경 원인도 UNKNOWN입니다. native process/thread/xdist·PI·연구·bench/HPC·전체 suite·CI polling·review 요청·merge 없이 종료하며 root가 exact-head CI와 독립 보안 검토를 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #788, #793

실제 Windows job118803767466의 두 실패는 diagnostics를 받지 않는 기존 fixture 래퍼가 Create 전에 거부한 문제다. 전달 인자만 맞추고 모든 기존 assertion·1회 Create·no-ACK/미소유 PID 보호를 유지했다.

원래 함수 AST의 fake-only 검증: 수정 전 TypeError, 수정 뒤 전달 identity·1회 fake Create·receipt/identity assertion PASS. Python3.10 AST·diff PASS. 실제 native fixture 재실행·push·CI·병합·배포는 하지 않았다. C3 policy 작업과 독립된 로컬 커밋이며 root가 C3 완료 뒤 결합한다. 원래 WMI timeout 원인은 미확인이다.

# PR #793 Windows WMI timeout 진단

- 원본 bd333485와 실제 검사 merge 7c9ccbb9를 구분하고, main 2ce87786을 포함한 검사 merge로 fast-forward 통합했다. C3의 기존 변경은 보존했다.
- PowerShell script 진입·입력 수신·Create 진입·반환의 고정 phase와 child Stopwatch/부모 monotonic 경과 시간을 기록한다. raw argv·env·stderr는 기록하지 않는다.
- timeout·미확인 Create는 계속 실패한다. 단일 Create·소유 handle/nonce/identity/ANY-Job·ACK/cleanup 경계를 유지한다. workflow·CI policy는 수정하지 않았다.
- 새 회귀 수정 전 16 failed, 관련 mock/static 최종 72 passed. Python 3.10 AST·공개/기록/diff 검사를 수행한다. 실제 Windows/WMI는 로컬 미실행이다.
- 원래 timeout 원인·Create 결과와 새 no-reuse 실증은 UNVERIFIED다. CI 조회·재실행·리뷰 요청·병합·배포하지 않는다. [해석·한계](../reference/windows-provider-793.md).

PR #793 · Refs #788 · source `2bc8476`.

기존 CI 정책은 Linux 3.10/3.12 전수 병렬화, Windows 경계 및 nightly/dispatch 전수, 검증 tree·workflow/profile·run/attempt·전체 gate receipt에 묶인 재사용입니다. 이번 수정은 재사용할 수 없는 snapshot drift를 별도 결과로 구분합니다.

- b842e56의 run39461236144는 선행 9개 job이 성공했고 validation-manifest만 실패했습니다. 보존된 event 진단·run association·현재 PR base는 `d0c834a`, immutable checkout `f480761`의 ordered parents는 `[2ce8778, b842e56]`입니다. PR merge SHA도 같은 checkout이고 main ref·forward compare는 두 커밋 전진을 확인합니다. 두 parent가 모두 있어 fetch-depth 문제가 아니며 GitHub의 snapshot 갱신 시점은 미확정입니다.
- producer는 기존 receipt/tree/profile·checkout/source/ref/blob 검사를 유지합니다. parent mismatch는 여전히 manifest를 만들지 않습니다. 같은 repo/workflow/run/attempt·9개 성공 job·immutable ordered parents/tree·현재 PR merge SHA/head·main ref·완전한 forward compare를 독립 확인한 경우에만 `reusable=false`로 종료하고 upload도 하지 않습니다. API 장애·부분/위조 증거·다른 manifest 오류는 계속 실패합니다.
- 안정된 binding은 기존 v3 manifest를 그대로 생성합니다. consumer의 expected tree/parents·workflow·attempt·coverage·artifact digest·사전 trusted-base policy 검사는 AST가 동일합니다. no-reuse에는 artifact가 없으므로 main은 full로 복귀합니다. observed parent로 event base를 대체하거나 자기 인증하지 않습니다([정책](https://github.com/ehojune/bioinfo-team-3d/blob/codex/ci-reuse-docs/docs/reference/ci-reuse.md)).
- 새 회귀는 수정 전 **3 failed/35 passed**, 수정 뒤 관련 guarded mock/static **607 passed**입니다. malformed/forged/부분 job·API 장애·임의 오류·stray file upload 방지와 consumer full fallback을 검사했습니다. Python3.10 AST/YAML·기존 gate job 동일성·integrations·public·기록·patch-note·diff PASS. 전체 실패 로그와 guard 환경을 보존했습니다. lookup-only reference는 6,618 → 7,177자이며 read-often docs는 늘리지 않았습니다.

한계: 새 head의 native CI와 실제 no-reuse/artifact 왕복은 미검증입니다. 제품·worker 격리·다른 owner 파일은 변경하지 않았고 main 통합/profile refresh도 하지 않았습니다. helper/runtime fingerprint가 달라 Windows 전수 fallback을 예상하며 과거 selected 수를 새 CI 범위로 주장하지 않습니다. 로컬 native/process/thread/xdist·PI·WSL·연구·bench/HPC·전체 suite·dispatch·CI polling·review 요청·merge 없이 종료하며 root가 exact-head CI·독립 검토·후속 P1·land를 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #799, #739

bench별 기록기 연결을 로컬 구현했습니다(76437b0). 실제 bench·연구·속도/효용 검증은 하지 않았습니다.

- `bench_metrics.py`가 기존 #815 계획 모양 셈법을 공유하고, hash 일치 승인 snapshot·task ledger·결과/승인 event를 arm별로 집계합니다. 최초/재계획/continuation 기대 dispatch, hosted task·attempt retry·consult·skill·분석·review·보고·승인 대기, known 비용·unknown 개수를 구분합니다.
- bench가 만든 runner에만 opt-in callback을 연결합니다. 성공한 OS spawn 뒤 시작 수와 argv UTF-8 표현·stdin byte만 셉니다. 단독 엔진도 직접 계측합니다. task/prepared prompt·context 크기는 대리값이며 원문·명령·경로·secret·다른 요청은 새 receipt에 저장하지 않습니다.
- 누락은 `not_collected`, 단독 arm의 labhq 단계와 mock의 실제 CLI는 `not_applicable`입니다. 실패 spawn과 미관측 attempt를 구분하고 재전송을 중복 집계하지 않습니다. `run.json`·arm별 `bench_metrics.json/.md`·comparison에 전달하며 재채점은 관측과 sidecar를 보존합니다.
- 집중 fake/static 28건과 실제 `result_editor` kind 연결 1건 PASS. Python 3.10 AST/compile 6파일·변경된 비-bench runtime 2파일 정답 누출 정적 검사·public·diff·patch-notes·notes 검사를 기록했습니다. Reference LF 문자 수 4833→4780(-53).
- 기존 `test_bench_answer_identifiers_do_not_leak_into_non_bench_runtime_files`는 내부 git subprocess fixture가 mock guard에 차단됐습니다. 로그/실패명을 보존하고 재실행하지 않았으며 동일 검사 함수의 위 두 파일 정적 대조로 대체했습니다. 원 pytest node와 Linux/Windows 실제 process 통합은 CI 경계입니다.
- 남은 범위: canonical 승인 snapshot이 없는 일반 lane 계획·재계획 rerun 증거, 실제 파일/환경·SDK 내부 전달량·baseline 내부 turn은 미수집입니다. launch 성공은 내부 CLI 인증·tool 성공의 증거가 아니며 단계 시간은 중첩되는 task elapsed 합입니다. #799는 계측 공백과 승인된 실제 비교가 남아 OPEN으로 둡니다.
- CSO 정책·승인/hash·DB schema·CI/conftest/pyproject·의존성·PI 상태는 변경하지 않았습니다. 실제 process/thread/포트·설치·PI·연구·bench·HPC·원자료·network fixture 미실행. #793 정책 준비 전까지 로컬 커밋만 보존하고 push/PR·CI polling·병합·배포를 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Refs #788 · source `56d65ee`.

기존 CI 정책은 Linux 3.10/3.12 전수 병렬화, Windows 경계 및 nightly/dispatch 전수, 검증 tree·workflow/profile·run/attempt·전체 gate receipt에 묶인 재사용입니다. 이번 수정은 아래 CPU clock 회귀뿐이며 [정책](https://github.com/ehojune/bioinfo-team-3d/blob/codex/ci-reuse-docs/docs/reference/ci-reuse.md)의 보호 조건은 그대로입니다.

- 완료된 run39458103661의 Linux3.12 두 실패는 반환값 검사를 통과한 뒤 wall-clock 2초를 2.032/2.049초로 넘겼습니다. 같은 run의 3.10은 10,193 passed/43 skipped·4 workers·422.13초, 3.12는 2 failed/10,191 passed/43 skipped·4 workers·479.57초입니다. 실패 구간의 CPU 소모 시간은 없어 경합 원인은 미확정입니다.
- 동기 string scanner의 `_assert_linear`만 `perf_counter`에서 `process_time`으로 바꿨습니다. 긴 입력·`is None`·엄격한 `< 2`·기존 test/assertion은 유지합니다. [현재 process의 CPU clock](https://docs.python.org/3.10/library/time.html#time.process_time)으로 비용을 검사하며 한 크기 입력의 시간 예산을 선형 복잡도의 증명으로 보지 않습니다.
- 새 fake 회귀는 적은 CPU/긴 wall 시간의 허용, 정확히 2초와 초과 CPU의 거부, 잘못된 반환값의 거부를 확인합니다. 수정 전 3 failed/2 passed, 수정 뒤 관련 pure-string·encoding **51 passed**. 기존 mock guard 환경과 전체 실패 로그를 보존했습니다. Python3.10 AST·public·기록·patch-note·diff PASS.
- 제품·workflow·manifest·profile·worker 격리와 S1/S2/DOCS798 파일은 변경하지 않았습니다. main 통합/profile refresh도 하지 않았습니다. origin/main 2ce8778(#822 d2289f2)의 runtime 변경은 현재 broad fingerprint를 만료시키므로 synthetic merge에서는 Windows 전수 fallback을 예상하며 과거 selected 수를 새 CI 범위로 제시하지 않습니다.

한계: 새 병렬 CI는 미조회이며 parent mismatch 원인은 UNKNOWN입니다. 로컬 xdist/native process/thread·Windows/WSL·PI·연구·bench/HPC·전체 suite·dispatch·CI polling·review 요청·merge 없이 종료합니다. exact-head CI·검토·후속 P1·land는 root 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Refs #788 · source `7b5ac01` + `7b99cbd` · main `d0c834a` 통합.

- job.env의 runner.temp가 workflow 시작을 막았습니다. 경로는 첫 step env에서 받아 UTF-8 GITHUB_ENV에 씁니다. job.env context 허용 목록과 POSIX/Windows 공백·따옴표 경로를 fake I/O로 검사했습니다. 추가 startup 진단은 jobs=0/workflow-file failure뿐이며 native 검증은 다음 CI에 맡깁니다([공식 context 규칙](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)).
- Windows PR/main은 검토된 pure 10파일만 digest·이유에 따라 제외합니다. 새 test·바뀐 source·hard OS/path/Job/shell/SQLite/lock/내구성 경계는 포함합니다. runtime·schema/catalog·scripts·pyproject·관련 fixture helper가 바뀌면 Windows 전수로 복귀하는 보수적 만료를 유지합니다. Linux 두 버전 전수·Windows nightly/dispatch 전수·독립 안전/설치/validator/MCP gate는 그대로입니다.
- Hub/Runner/ASGI mock 이름·import·문자열 대신 alias를 해석한 port/process capability 호출과 관련 helper를 직렬화합니다. 현재 같은 tree에서 lock 대상은 **220파일/7,781 node → 77파일/2,591 node**입니다. HOME/temp/state/DB/output 격리와 #787 진단·25분 상한을 유지했습니다.
- guarded mock/static **559 passed**. serial collection **10,201 → 9,718 nodes**, 306 → 296 files(10파일·483 node 제외). 이전 published head는 10,114 → 10,074 node·lock 217파일/7,694 node였습니다. main 통합과 새 회귀로 full 수가 달라졌습니다. Python3.10 AST·profile hash·integrations·public·기록·patch-note·diff PASS. 최초 12 failed/7 passed와 중간 1 failed/53 passed 전체 trace도 보존했습니다.
- main의 #821·제품·S1/S2 소유 test는 main과 동일하며 직접 수정하지 않았습니다. manifest helper와 conftest는 이전 head와 동일합니다. strict ordered parent·workflow/run/attempt/profile/tree/전체 receipt 보호를 완화하지 않았습니다. lookup-only reference는 6,229 → 6,618자이며 read-often docs는 늘리지 않았습니다.

한계: checkout-parents의 실제 mismatch 원인은 여전히 UNKNOWN입니다. startup 실패는 그 경로의 새 증거가 아닙니다. 실제 병렬 안전·CPU/worker 수·Windows 시간/5분 목표·성공 manifest/reuse는 미검증입니다. 제외율은 4.7%로 대부분의 Windows 검사가 남습니다. 로컬 native fixture/worker/process/thread·PI·연구·HPC·bench·전체 suite·dispatch·CI polling·review 요청·ready·merge 없이 종료하며 root가 exact-head CI·독립 검토·후속 P1·land를 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Refs #798, #821

논문 스킬 문서를 병합된 동작에 맞췄습니다(8aba20b).

- CP1 metadata 확인 → 승인된 단계에서 생성 → 검증 문서의 읽기 전용 주입 → 성공 결과의 정확한 ID·폴더/문서 hash 참조를 설명합니다. 승인·취소 경계와 참조 receipt/실제 tool 호출·과학 효용의 구분을 유지합니다.
- README 한영판은 CI 통과 대상을 병합된 기능으로 한정합니다. 실제 연구 동작·효용은 미검증입니다. 운영 큐·예산·작업자 정책은 독자 문서에 추가하지 않았습니다.
- LF 문자 수: README 11,684→11,686, README.en 17,598→17,627, manual 175,212→175,153. 독자 문서 합계 204,494→204,466(-28); reference 2,188→2,218(+30).
- 병합 코드 대조·로컬 링크/heading·diff·public·patch-notes·notes 검사. 제품·테스트·AGENTS·HANDOFF·CI 파일과 생성 목차는 변경하지 않았으며 새 테스트·pytest·실제 연구/bench/native fixture를 실행하지 않았습니다.
- 독립 검토 P1=0 뒤 읽기 권한 설명을 검증된 주입 문서로 한정했습니다.
- 로컬 커밋만 준비했습니다. #793 CI 정책 적용 전 불필요한 전체 Windows 실행을 막기 위해 push·PR 생성은 총괄이 나중에 진행합니다. 이번 작업에서 CI 조회·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Refs #788 · source `d60e4e4` + `3fa6e27` · CI 정책을 한 head로 통합합니다.

- Ubuntu 3.10/3.12 전수는 CI outer 명령에만 `-n auto --dist loadfile`을 씁니다. global addopts 없이 nested pytest는 직렬입니다. Windows PR/main은 `windows-boundary-v1`, nightly/dispatch는 `windows-full-v1`이며 clean-install·공식 validator·긴 MCP gate를 유지합니다.
- Windows 제외는 검토된 pure 3파일의 source digest·이유와 runtime Python/pyproject fingerprint에 묶었습니다. 변경·신규·미분류·OS/safety 파일은 포함하고, implementation review가 낡으면 전수로 복귀합니다. 현재 대부분의 Windows test를 포함하므로 full과 동등하거나 5분이라고 주장하지 않습니다.
- controller/worker 원래 HOME identity와 worker별 temp/state/cache/DB/log 격리를 유지합니다. port/process test 및 local helper는 job 공유 lock으로 setup~teardown을 직렬화합니다. production fsync·SQLite FULL·atomic replace·Job 코드와 S1/S2 소유 파일은 바꾸지 않았습니다.
- v3 manifest는 coverage profile·workflow/selector/profile/dependency/conftest/guard blob까지 결합하고 자기 인증·fork·다른 run/attempt/tree/profile 재사용을 거부합니다. worker별 full/selected node 목록, CPU/실제 worker 수, setup/pytest/job queue, failures/skips·최장 파일·lock 대기를 CI artifact에 기록합니다. #787 controller class와 25분 상한은 그대로이며 worker에도 제한된 stack 진단을 추가했습니다.
- 검증: guarded mock/static **531 passed**. 로컬 serial collection **10,114 → 10,074 nodes**, 303 → 300 files(검토된 3파일·40 node 제외). 이는 native CI/병렬 실행 증거가 아닙니다. Python 3.10 AST·workflow/기존 진단 동일성·integrations·public·기록·diff PASS, guard와 전체 로그 보존. lookup-only CI reference는 4,127 → 6,229자이며 read-often 문서는 한 줄만 추가했습니다.

manifest 실제 원인은 **UNKNOWN**입니다. [실패 job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39421039549/job/118639548051)의 checkout API는 `[8151f236…, 853edf98…]`와 gate와 같은 tree를 확인했지만 event snapshot은 없고 run association base는 `233ac70…`입니다. ordered parent 비교를 그대로 거부하며 안전한 actual/expected SHA 진단만 보강했습니다. 실제 worker HOME/port 격리·Windows 안전/소요 시간·성공 manifest/reuse는 미검증입니다. 로컬 worker/process/thread·PI·연구·HPC·bench·전체 suite 실행과 CI polling은 하지 않았습니다. root가 새 exact-head native CI·독립 검토·후속 P1·병합을 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR #822 Linux fake fixture 계약 수정

- 제공된 Linux 3.10·3.12 로그의 동일 3건을 로컬 mock으로 재현했다. 진단 wrapper가 추가한 AssertionError 대신 기존 LaunchError를 기대한 test 계약 오류였다.
- AssertionError와 원래 LaunchError의 cause 동일성을 검사한다. spawn 1회·stream close·raw retry 금지·미확인 PID 종료 금지·PID/ACK 기록 부재를 유지한다.
- 수정 전 3 failed, 수정 후 관련 mock 14 passed. Python 3.10 AST·public·diff·기록 검사를 수행한다.
- 제품·Windows integration·CI/conftest·#774 수정은 변경하지 않았다. 원래 WMI timeout 원인·Create 결과는 UNVERIFIED이며 새 CI는 조회하지 않는다.

CP1에서 논문 스킬 생성이 끝날 때까지 기다리던 흐름을 바꿉니다. CP1에서는 메타데이터만 확인하고 승인 뒤 스킬을 준비합니다. 담당 스텝은 hash와 소유권을 확인한 문서만 읽으며, 결과의 참조 receipt로 사용 여부를 기록합니다.

- 승인 전 준비 금지, 승인된 계획·hash, 취소 전파와 읽기 전용 입력 경계를 유지합니다. 파일 배치만으로 `called=true`를 기록하지 않습니다.
- 기존 CI 실패 4건을 재현한 뒤 같은 4건과 관련 17건의 fake 검사가 통과했습니다. prompt snapshot은 의도한 변경을 역치환으로 확인하며 단언을 유지했습니다.
- 앞선 main 문서 통합에서는 제품 코드·테스트를 바꾸지 않았고 자동 목차는 main 것을 보존했습니다. 공개 검사 2회와 기록 검사를 통과했습니다.

최종 head의 CI·재리뷰는 controller가 담당합니다. 실제 논문/PDF·연구 요청·성능 검증은 하지 않았습니다.

Fixes #798

## 10-21 Linux 3.12 CI 보정 (1079d5b)

- Job 118674733476의 terminal error는 staff-path 테스트의 전역 Path trap이 pytest traceback·cache·정리도 차단한 문제였습니다. trap은 staff/cache 경로와 해당 호출 context로, git fake는 해당 모듈로 한정했습니다. 제품 코드·안전 경계는 그대로입니다.
- 기존 사전 검사 6조합은 단독 PASS였으며 무관한 진단 I/O를 끼운 새 6건은 같은 메시지로 red 재현했습니다. 수정 뒤 해당 6+6건과 예상 밖 예외/조기 staff·cache I/O/git 차단·복원 4건 등 16건 PASS(로컬 0.36초), 관련 HOME guard 3건 PASS(0.10초)입니다. 실패 trace를 보존했습니다.
- Python 3.10 AST/compile 1파일·public·diff·patch-notes·notes 검사를 통과했습니다. inherited mock guard를 유지했습니다.
- 한계: 선행 E/F의 원 traceback은 CI 로그에 없어 전체 원인은 미확정입니다. 전체 CI green을 주장하지 않습니다. 실제 설치/git·process/thread·CLI·PI·PDF·연구·bench·HPC·원자료는 미실행이며 CI/리뷰 조회·대기·봇·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# PR #822 Windows CI 진단 보완

- Windows 실패는 exit-code assertion 전 PowerShell WMI 호출의 10초 timeout이다. Create 성공 여부·근본 원인은 미확정이며 #774 AST 수정과의 연결 근거는 없다.
- supervisor fixture가 시작 실패 때 기존 진단 끝 8192자를 첨부하도록 보완했다. 제품 timeout·재시도·소유권·Job 안전 경계와 실제 CI assertions는 바꾸지 않았다.
- 수정 전 새 회귀 2 failed, 수정 후 관련 mock 6 passed. Python 3.10 AST 통과. 실제 WMI·프로세스는 실행하지 않았고 Windows 실패는 미해결이다.
- public·diff·patch-notes·notes 검사 결과는 PR 본문에 기록한다. CI 조회·재시도·병합·배포는 하지 않는다.
- [원인과 한계](../reference/ci822-launch-evidence.md). 원래 #774 수정과 다른 작업자의 파일은 보존했다.

# PR #821 문서 충돌 통합

- main의 감사·queued-halt 안내와 PR의 논문 스킬 계약을 함께 보존했다.
- 제품 코드·테스트는 직전 검토 head와 동일하며 자동 목차는 main 버전을 유지했다.
- 최종 CI·실제 요청 동작은 새 head에서 확인해야 한다.

Fixes #798
PR #821 · CI 보정 a9d0e56

논문 스킬은 CP1에서 metadata·선정 hash만 확인하고 승인된 담당 단계에서 생성합니다. 검증된 문서만 읽기 전용 grant에 연결하며, 성공한 결과의 정확한 ID·내용 hash 참조가 있어야 called=true입니다. 승인·재승인·조상 입력 보호·지연 스킬 재사용 제외 계약을 유지합니다.

- Linux 3.12 job 118620897833의 네 실패는 이전 prompt snapshot 두 건과 추출이 없어진 준비 단계에서 중단을 기대하던 두 건이었습니다.
- Prompt는 승인 후 생성 문장만 반영했고 그 문장을 되돌리면 이전 두 digest와 일치함을 검사합니다. schema·output_types off/on assertion은 유지합니다.
- 준비 callback 미호출 뒤 승인 hash가 있는 CSO step에서 runner shutdown·asyncio cancellation을 주입합니다. 두 예외의 전파, 연구 단계 미dispatch, 계획·승인 hash 보존을 검사합니다. 제품 예외 처리는 이미 맞아 수정하지 않았습니다.
- 검증: 원 head에서 네 실패 재현 → 같은 네 node PASS(로컬 1.55초), 관련 mock/static 17건 PASS(4.06초). Python 3.10 AST/compile 3파일·public·diff·patch-notes·notes 검사도 통과했습니다. 전체 실패 trace는 레인 로컬 로그에 보존했습니다.
- 한계: 실제 process/thread·CLI·PDF·네트워크·PI·연구·bench·HPC는 미실행입니다. Windows/Python 3.10 실행·최종 head CI·독립 재리뷰는 controller가 맡습니다. CI 조회·대기·봇 호출·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
#774 — 환경변수 입력 판별 수정 (draft)

## 한 일
- 최신 main을 통합한 뒤 Python 단순 대입·삭제를 외부 입력에서 제외했다.
- Load·get/getenv·AugAssign 읽기와 LABHQ_INPUT_* 선언, 기존 격리·redaction을 유지했다.

## 테스트 결과
- 수정 전 새 회귀: 9 failed / 15 passed. 수정 후 지정 AST·FakeInterpreter 검사: 28 passed.
- Python 3.10 AST·diff 검사 통과. 실제 interpreter·서비스·성능은 미검증.
- public·patch-notes·notes·diff 검사 통과. CI·리뷰는 조회하지 않는다.

## 바꾼 파일
reproduction_receipt.py, 해당 focused test, manual 한 문장, reference·status·patch entry.

## 막힌 점
없음. 실제 실행 검증과 CI 판정은 총괄에게 맡긴다.

Fixes #774

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Refs #788 · test proxy 수정 `d566299`.

- [Python 3.10 실패 job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39417966579/job/118611596303)의 다섯 traceback은 모두 `lookup_runtime` proxy에 `get_running_loop`가 없어 새 `_step_slot()`에서 발생한 AttributeError입니다. CI 결과는 5 failed / 10009 passed / 43 skipped, 13분24초입니다. timing 문제로 취급하지 않았습니다.
- fixture가 module-local monkeypatch 전에 원래 `asyncio.get_running_loop`와 `asyncio.Lock`을 캡처해 proxy에 전달합니다. fake timer·notification/cancellation handshake는 그대로입니다. 새 회귀는 원래 API·같은 step의 실제 coroutine lock·queued decision의 직렬화와 declined 반환을 확인합니다.
- 수정 전 focused file은 **6 failed / 15 passed**(기존 CI 5건과 새 회귀 1건). 수정 후 해당 file 21건과 기존 overlap/직렬화 6건을 합쳐 **27 passed**입니다. Python 3.10 AST로 기존 test/helper·fixture의 나머지 본문과 assertion이 동일함을 확인했습니다.
- 제품·workflow·manifest helper·기존 overlap test는 `80b6ff5e`와 byte-identical입니다. deadline/timeout을 늘리거나 assertion·lock·gate를 약화하지 않았습니다. guard 환경과 전체 CI/red/green 로그를 보존했고 public·patch-note·기록·diff 검사가 통과했습니다.

한계: 실제 manifest 원인은 여전히 UNKNOWN입니다. 성공 manifest/reuse·새 native CI는 미확인입니다. 실제 process/thread·PI·연구·HPC·bench·전체 suite 실행이나 CI polling 없이 종료하며 root가 새 head 검토·CI·병합을 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
PR #820 최신 main 통합

## 한 일
- main `8151f23`을 명시적으로 병합했습니다. 충돌은 HANDOFF 한 곳이며 기존 문서 정리와 main의 queued-halt 검증 근거를 모두 보존했습니다.
- 병합된 #818·#819를 큐에서 빼고 열린 #793·#821을 남겼습니다. 매뉴얼의 #784 미출시 표식도 제거했습니다. 구현·검사와 실제 운영·과학 효용의 구분은 유지합니다.
- main의 코드·테스트와 생성 목차는 그대로 보존했습니다. 새 기록은 원본에 남기며 목차는 병합 뒤 main workflow가 갱신합니다.
- 세 문서의 LF 정규화 길이는 기존 head 179,685→통합 후 180,420자입니다. main의 새 설명을 포함한 수치이며 최신 main 180,815자보다 395자 짧습니다.

## 검사
- 충돌 잔여·큐·병합 ancestry·상대 링크·공개 경로/비밀값 변경분·소유 범위·`git diff --check` 통과.
- patch-notes 규칙과 notes-index 원본 검증 통과. 생성 목차의 PR 변경 금지 규칙을 지킵니다.

## 한계
문서와 기록만 수정했습니다. 전체 공개 스크립트·pytest·서비스·PI 인스턴스·배포·실운전·bench·HPC는 실행하지 않았고 CI·리뷰는 기다리지 않았습니다. 새 head 검토·CI·병합은 총괄이 관제합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #798
PR #821 · 기능 c878148 · 읽기 권한 P1 6b37d70 · main 통합 df2a51c

논문 스킬 변환을 승인된 담당 단계의 dispatch로 옮기고 장착과 결과의 출처 표기를 구분합니다. root core_structure가 찾은 정상 DAG의 논문 입력 Read 거부를 수정했으며 최신 main 8151f23도 명시적으로 merge했습니다.

- CP1은 metadata·담당 단계·선정 hash만 고정합니다. 기존 예산·retry·취소 경계로 지연 build하며 승인 계획·재승인 검사·#807의 deferred 재사용 제외를 유지합니다.
- policy 구성 때 receipt와 owned 문서의 길이·내용 hash·무링크 handle을 재검사해 정확한 파일만 기존 read grant에 연결합니다. inputs 보호·조상 권한·native 쓰기 제한은 유지하며 namespace 충돌 시 장착을 생략합니다. 광역 allowlist·policy 우회 분기는 추가하지 않았습니다.
- 성공한 결과의 정확한 스킬 ID/폴더·문서 hash 참조와 유지된 사본이 있어야 called=true입니다. 장착만으로는 참이 되지 않으며 미참조·실패·증거 부족은 false와 이유를 남깁니다. 이는 출처 표기이며 tool 실행·과학적 효용의 증거는 아닙니다.

충돌 선택: HANDOFF 한 곳에서 main의 #818 queued-halt 검증 설명과 #821 논문 입력 계약을 함께 보존했습니다. manual은 양쪽 내용이 자동 병합됐습니다. #818·#819 코드/검사와 main 생성 목차를 손대지 않고 가져왔습니다.
검증: 통합 후 focused mock/static 63건 PASS, guard 이벤트 0건. main 관련 14파일과 #821 제품/계약 9파일 동일성을 확인했고 Python 3.10 AST/compile 7파일·public·diff·patch-notes·notes 검사도 통과했습니다. 이전 Read P1은 4건 red 뒤 관련 80건/최종 권한·profile 14건으로 검증했습니다.
한계: grant는 policy 구성 시 검증하며 파일 불변을 보장하지 않습니다. 실제 CLI·native sandbox·process/thread·PDF·네트워크·연구·bench·HPC는 실행하지 않았습니다. 최종 head의 Windows/Python 3.10/safety CI·독립 재리뷰는 root/native owner가 담당합니다. CI 조회·대기·봇 호출·PR 병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #798
PR #821 · 기능 c878148 · 읽기 권한 P1 수정 6b37d70

논문 스킬 변환을 승인된 담당 단계의 dispatch로 옮기고 장착과 결과의 출처 표기를 구분합니다. root core_structure 정적 검토가 찾은 정상 DAG의 논문 입력 Read 거부도 수정했습니다.

- CP1은 metadata·담당 단계·선정 hash만 고정합니다. 기존 예산·retry·취소 경계로 지연 build하며 승인 계획·재승인 검사·#807의 deferred 재사용 제외를 유지합니다.
- policy 구성 때 runner receipt와 owned 문서의 길이·내용 hash·무링크 handle을 재검사해 정확한 파일 하나만 기존 read grant에 연결합니다. inputs 보호·조상 권한·native 쓰기 제한은 유지하고 namespace 충돌 시 스킬 장착을 생략합니다. 광역 allowlist·policy 우회 분기는 추가하지 않았습니다.
- 성공한 결과의 정확한 스킬 ID/폴더·문서 hash 참조와 유지된 사본이 있어야 called=true입니다. 장착만으로는 참이 되지 않으며 미참조·실패·증거 부족은 false와 이유를 남깁니다. 이는 출처 표기이며 tool 실행·과학적 효용의 증거는 아닙니다.

검증: daemon/정책 조합 Read 거부 4건 red 뒤 관련 mock/static 80건 통과. 최종 권한·native profile 14건도 통과했고 마지막 guard 추가 이벤트는 0건입니다. 정확한 문서 읽기, Write/Edit 거부, 다른 입력/private/링크 우회 거부, 같은 크기 내용 변경·누락·링크 교체의 grant 제외, 조상 namespace 보존을 확인했습니다. Python 3.10 AST/compile·public·diff·patch-notes·notes 검사도 통과했습니다.

변경: paper_skills·CSO·runner workspace/daemon·continuation proof 한 조건, 관련 tests와 manual/reference/HANDOFF·기록. 이번 P1 제품 변경은 workspace/daemon에 한정합니다.
한계: grant는 policy 구성 시 검증하며 파일 불변을 보장하지 않습니다. 실제 CLI·native sandbox·process/thread·PDF·네트워크·연구·bench·HPC는 실행하지 않았습니다. Windows/Python 3.10 런타임·CI·독립 재리뷰는 controller가 담당합니다. CI/봇 대기·호출·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Refs #788 · main `233ac70` 명시 통합 `0c404e4`.

- 시작 worktree는 clean·MERGE_HEAD 없음이었습니다. latest main을 fetch하고 실제 merge에서 `tests/test_consult_restart.py`의 optional Git provenance fixture 한 곳을 해결했습니다. main의 단일 `None` mock을 택해 실제 Git process 접근을 막았습니다.
- PR의 event/adoption handshake·provider 중복 금지·60초 outer watchdog과 main의 facilities 대기 즉시 실패·CSO routing 계약을 함께 보존했습니다. 양쪽 parent의 모든 assertion과 각 함수의 출처를 AST로 대조했습니다. timeout/제품 코드를 추가 수정하지 않았습니다.
- 제품 코드는 incoming main과 동일합니다. workflow·`scripts/ci_skip.py`는 `1c4917f0`와 byte-identical로 full gate receipts·exact tree/head/base/checkout/workflow binding·제한된 identity 진단을 유지합니다. halt/supervisor test를 별도로 고치거나 옛 버전으로 복원하지 않았습니다.
- consult mock 3건과 관련 CI policy/manifest/encoding을 합쳐 **316 passed**. guard 환경·전체 로그 보존. Python 3.10 AST·양쪽 assertion 보존·integrations·public·patch-note·기록·diff 검사 PASS. main의 문서·패치노트 변경은 통합 결과로 보존했습니다.

한계: 실제 manifest 원인은 여전히 UNKNOWN이며 성공 manifest/reuse를 확인하지 않았습니다. 실제 process/thread·PI·연구·HPC·bench·전체 suite·새 CI polling은 하지 않았습니다. root가 새 exact-head native CI 진단·독립 리뷰·최종 병합을 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #798
PR #821 · 기능 커밋 c878148

논문 스킬의 무거운 변환이 계획 승인(CP1)을 막지 않도록 승인된 담당 단계의 dispatch로 옮겼습니다. 최종 기록은 장착과 결과의 실제 출처 표기를 구분합니다.

- CP1은 제공된 DOI/PMID·full_text 접근 metadata·담당 단계와 선정 hash만 고정합니다. build는 기존 예산·retry·취소 경계를 사용하며 승인 계획을 바꾸지 않습니다.
- 지정 정규직의 owned `inputs/paper_skills/<skill_id>.md`와 engine 사본을 hash 검증 후 주입하고 실행 뒤 제거합니다. 성공한 결과의 정확한 ID/폴더·문서 hash 참조와 유지된 사본이 있어야 `called=true`입니다. 참조 없음·변경·실패·증거 부족은 false와 이유로 남깁니다. 이는 출처 표기이며 tool 실행·과학적 효용을 증명하지 않습니다.
- build·예산 대기 중 승인/선정·재사용 proof drift는 중단·재승인을 요구합니다. 교정/slot repair도 같은 경계를 적용하고 기존 provenance에 receipt를 보존합니다. 내용 proof 없는 지연 빌드 노드와 종속 노드는 CP1 재사용 후보에서 제외하며 #807의 독립 노드·기존 고정 hash 검증은 유지합니다.

검증: CP1 선행 build/불명확 called 계약과 교정 승인·slot repair 예외의 red를 보존했습니다. 관련 3개 파일의 mock/static 66건, Python 3.10 AST/compile, public·diff·patch-notes·notes 검사를 통과했습니다. 실제 PDF·네트워크·연구·process/thread 실행은 하지 않았습니다.

변경: paper_skills·CSO·runner workspace/daemon·continuation proof 한 조건, 관련 tests와 manual/reference/HANDOFF·기록.
한계: 실제 사용은 결과의 출처 표기로만 판정합니다. 실제 전환 지연·과학적 효용·Windows/Python 3.10 런타임은 미검증이며 CI·새 head 리뷰는 controller가 맡습니다. CI/리뷰 대기·봇 호출·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Refs #788 · 진단 수정 `fcf645d` · manifest 원인은 아직 미확정입니다.

- [실패 job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39408450974/job/118593337549)은 full gate receipts를 통과한 뒤 복합 identity 검사에서 실패했습니다. Windows·Python 3.10/3.12·설치·slow·validator 등 나머지 9개 job은 success입니다.
- checkout `b7bf2ad7`의 API tree는 gate receipts와 같은 `b3b10c22`이며 parent 순서는 base `5877bb2f`, head `ad93b423`입니다. REST run의 PR association base는 `b9abcec7`로 달라 event payload의 base 증거로 대체하지 않았습니다.
- [해당 run의 workflow 화면](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39408450974/workflow)은 `ad93b423`를 가리킵니다. head/checkout workflow blob은 같지만 로그에 실제 `GITHUB_WORKFLOW_SHA/REF`가 없어 환경변수 불일치는 단정하지 않습니다. [공식 정의](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)는 workflow source SHA와 event checkout SHA를 구분합니다.
- 원인을 추측해 허용 분기를 추가하지 않았습니다. 기존 metadata·ordered parents·workflow blob 검사를 그대로 유지하고 실패 조건명과 고정 source/ref 역할명만 출력합니다. 환경·event 원문이나 임의 값을 출력하지 않습니다. manifest/reuse 성공 주장은 없습니다.
- 회귀는 수정 전 **13 failed / 8 passed**, 수정 후 관련 pure mock/static **313 passed**입니다. 실제 git/API/프로세스 fixture 없이 정상 manifest 동일성·각 identity 거부·모든 gate 거부·diagnostic 비노출을 확인했습니다. Python 3.10 AST·나머지 함수 동일성·encoding·integrations·public·patch-note·기록·diff 검사 PASS, guard와 전체 실패 로그 보존.

미해결: 실행 환경변수와 event payload를 확인할 다음 exact-head 진단이 필요합니다. fetch한 main `3eaa6f9`와 `tests/test_consult_restart.py` 충돌도 확인했으나 현재 소유 밖이라 worktree에 merge/수정하지 않았습니다. controller가 통합·새 CI·리뷰·원인 수정을 이어야 합니다. 기존 run 재실행·새 CI polling·실제 process/thread·PI·연구·bench·HPC·전체 suite는 하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
20시 보고 전 문서 현행화

## 한 일
- HANDOFF에서 병합된 PR과 오래된 통합 대기를 빼고 열린 #793·#818·#819만 남겼습니다. #766 병합 확인도 반영했습니다.
- 매뉴얼의 #791·#807 병합 전 표식을 제거하고 감사 기록의 #802·#807·#815 상태를 갱신했습니다. 구현·CI와 배포 준비·실운전·성능 측정은 구분합니다.
- 세 문서의 LF 정규화 문자 수: HANDOFF 3,614→3,044, 매뉴얼 174,343→174,281, 감사 기록 2,044→2,360. 합계 180,001→179,685(-316)입니다.

## 검사
- 문서 상태·병합 commit의 ancestry·변경한 상대 링크·추가 문장의 비밀값·개인 경로 검사와 `git diff --check` 통과.
- patch-notes·notes-index 원본 검증 통과.
- `scripts/check_public.sh`는 35초 제한 실행에서 출력 없이 비정상 종료(exit 1)해 전체 검사는 미확인입니다.

## 한계
문서만 바꿨습니다. pytest·서비스·PI 인스턴스·배포·실운전·bench·HPC는 실행하지 않았습니다. CI·리뷰·병합은 총괄이 관제합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #811
Refs #804
PR #818

Shadow 비교의 기준 실행을 공유하고 제거 검사를 계약 node로 좁혔습니다. queued-halt CI 검사는 executor 대기와 분리했습니다.

- off는 불변 JSON 한 번만 공유하고 on 네 경우는 독립 실행합니다(lab 호출 구조 8→5). fresh-process off/import·fail-open·privacy/read-scope·제거 compile/잔여 참조/상태·핵심 E2E는 유지하며 제거 뒤 전체 테스트 수집을 추가했습니다. Shadow 전용 검증 5개는 assertion을 유지해 observer suite로 이동했습니다.
- 완료 Windows job 118566758410은 8659 passed·48 skipped 뒤 한 건 실패했습니다(2031.72초). queued halt·세션·미dispatch assertion은 통과했고, 다음 t3의 빈 input scan executor 대기에 2초 전체 task 제한이 걸렸습니다. 제품 halt 결함을 뒷받침하는 trace는 없습니다.
- 해당 control 테스트만 worker job 본문을 직접 실행하도록 opt-in fixture를 적용했습니다. 기존 assertion 6개·deadline 5개를 모두 보존하고 첫 task가 슬롯을 보유한 채 halt 완료·나중에도 t2 미dispatch를 추가 검증합니다. fsync/SQLite write는 실제 본문 그대로이며 전역 no-op·정책·production control 코드 변경은 없습니다.
- 모의 executor stall로 원 head에서 같은 TimeoutError red 재현, 수정 뒤 관련 mock/static **11 passed**. #800 done-event 방어와 #811 계약도 확인했습니다. 앞선 #811 mock/static 79 passed·1 skipped 근거는 reference/status에 남겼습니다. Python 3.10 AST·public·diff·patch-notes·notes 검사를 기록했습니다.
- 로컬 실제 subprocess/thread fixture·제거/E2E·PI/연구/bench/HPC/원자료는 미실행입니다. Windows/native scheduling·durability 통합과 CI 시간 절감은 새 CI 검증 경계입니다. CI/리뷰를 조회·대기하거나 봇 호출·병합·배포하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 승인 대기와 외부 조회 예산 (#784, 개발 초안)

동일 MCP event loop·작업 폴더의 자동 조회와 PDF 승인 대기를 하나씩 처리해 겹친 시간을 두 번 제외하지 않도록 했습니다. 대기열에 있던 조회는 승인 종료 뒤 기존 잔여 예산으로 시작하며 별도 단계 예산은 독립적입니다. 기존 timeout/error/cancellation과 #800 progress 종료 flag는 유지합니다.

fake clock·notification·coroutine gate 회귀는 수정 전 5 failed/1 passed, 수정 후 관련 27 passed입니다. 겹친 승인 뒤 후속 조회·잔여 예산 소진, 승인/조회 양쪽 순서, queued/active 취소와 다른 단계를 검사했습니다. 로컬은 mock/static만 실행했습니다.

실제 서비스·PDF·MCP server·네트워크·process·PI·연구·HPC·성능은 미검증입니다. 독립 process/event loop 사이의 동시 실행은 이 slot의 직렬화 범위 밖입니다. 근거 docs/reference/approval-wait-784.md. draft PR을 열며 CI/리뷰 대기·병합·배포는 총괄 소관입니다.

PR #793 · Fixes #788 · P1 수정 `d2ddfc3`.

- 4321974288: 주제 명사 목록과 별개로 영어·한국어의 요구·허용·금지 표현을 이전/새 본문 양쪽에서 보호합니다. 규범 inline 문단의 문구·문맥·강조·container 변경은 full CI이며, 내용·Markdown 구조가 같은 soft wrapping만 허용합니다.
- Authentication mandatory→optional, Widget prohibited→permitted, 한국어 필수 검토→생략 허용과 추가/삭제·역방향 회귀를 확인했습니다. 별도 일반 prose와 같은 의미의 reflow는 유지하며 규범 문단 안의 부수 prose 수정도 보수적으로 full로 남깁니다.
- 4321519440: 정확한 Gradle assemble→publish 사례는 기존 `5e55e9d`의 publish 규칙으로 full입니다. 4320728990: 기존 `5d7bd7a` 이후 그대로인 README 제목에 prose만 추가하면 document-only입니다. 시작 head `fafd36b`의 실제 parser·fake git 회귀 **2 passed**로 검증했습니다.
- 새 회귀는 원래 head에서 **70 failed, 20 passed**, 수정 후 관련 mock/static **448 passed**입니다. Markdown 의존성을 직접 import·parse하므로 dependency 오류의 fallback False를 증거로 삼지 않았습니다. 설치·실제 git fixture 실행 없이 기존 의존성을 재사용했습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff PASS. workflow·외부 요청/redirect 보호·REST/artifact/exact-tree/workflow/gate·manifest 함수와 기존 assertions는 그대로이며 guard 환경·전체 실패 로그를 보존했습니다.

한계: 실제 process/thread·PI·연구·bench·HPC·전체 suite는 실행하지 않았습니다. 새 CI/리뷰는 조회·대기하지 않으며 controller가 후속 검증·병합을 맡습니다. 실제 artifact 재사용 효율은 미측정입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #766 · Refs #681 · P1 수정 `45f35f0`.

- 4323699194: 이름 선택 괄호식·선택 변수 receiver의 Kill()을 거부합니다. 괄호식의 앞선 출력도 추적하며 명시 PID·인용된 변수 문자열·호출 없는 member 조회는 구분합니다.
- 4323699213: ionice의 class/classdata·ignore option 뒤 실행 command를 검사합니다. PID/PGID/UID 대상 지정·option operand·help/version은 실행형과 구분합니다.
- 4323699225: Bash locale quote의 비번역 literal을 double quote처럼 정규화합니다. 인접 fragment와 escaped/single-quoted 데이터도 구분합니다. 4318107934는 기존 stale 근거 답글로 제외합니다([근거·회귀](https://github.com/ehojune/bioinfo-team-3d/blob/codex/broad-process-stop-681/docs/reference/process-stop-681-followup.md)).
- 검증: 원래 head에서 **31 failed, 30 passed**, 수정 뒤 guard 10개 파일·text encoding **710 passed**. policy·flat/nested fake broker의 deny·card/event/submit 미호출·pending 없음을 확인했습니다. 기존 test·assertion과 guard 환경은 유지했습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff 검사 PASS. policy·runner·workflow·공통 fixture와 #800 반영을 보존했습니다. main과 충돌은 없었습니다.

한계: 실제 process/proc_tree·shell·번역 catalog·PI·연구·HPC는 실행하지 않았습니다. OS 격리·PID tracker #675·P2는 별도 범위입니다. 새 CI·새 head 리뷰/P1=0·병합은 controller가 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #797
Refs #672, #680
PR #807: P1 4323683886 — paper-skill 방법이 바뀌면 이전 결과를 재사용하지 않도록 수정했습니다.

- 해당 단계의 `paper_skills` selection과 step/agent에 연결된 `paper_skill_results` receipt 전체·순서를 context hash에 넣습니다. CP1 후보 identity와 실행 context가 같은 hash를 쓰며 prompt에는 receipt 경로/값을 추가하지 않습니다.
- 방법 변경은 그 단계와 의존 단계의 재사용을 거절하고 다른 단계의 별도 skill 변경은 독립 후보를 유지합니다. 구 실행 signature는 새 계약의 증거로 쓰지 않습니다. 승인 뒤 현재/보관 plan의 해당 입력이 바뀌면 중단·새 CP1을 요구하며 조용히 재실행하지 않습니다.
- 기존 head 새 회귀 15건 red 뒤 새 18건을 포함한 관련 mock/static **129 passed**. 선언·실제 attachment metadata 대조, CP1 후보/snapshot·승인 후 변경·기존 요청 입력 차단·재개/예산 rollback을 확인했습니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 근거를 기록했습니다.
- Paper2Skill 변환/설치·실제 source bytes 읽기·process/thread/Hub 운영·PI·연구/bench/HPC/원자료는 미실행입니다. 기존 hash/path·승인·보안·bounded loop를 유지하고 실행 확장·P2·다른 owner 범위는 건드리지 않았습니다. 최종 head CI·새 리뷰/P1=0·병합/배포는 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #811
Refs #804
Draft PR #818

Shadow 비교 검사에서 같은 off 실행의 반복을 줄였습니다. on 비교와 안전·제거 검증은 유지합니다.

- off 기준은 독립 상태에서 한 번 계산한 JSON 문자열만 공유합니다(비교 네 경우: lab 호출 8→5). on 네 실행은 각각 새 상태이며 fresh-process off/import·fail-open·privacy/read-scope 검사는 유지합니다.
- 제거 뒤 compile·잔여 참조·상태 복구·resume 승인·필수 E2E를 유지하고 child 실행은 계약 node로 좁혔습니다. 전체 생존 테스트 수집을 추가했습니다. #804의 script 의존 boundary harness는 script와 함께 제거하고 mainline CP1 검사는 남깁니다.
- 수집을 깨던 mixed-suite의 Shadow 전용 테스트 5개를 observer 테스트로 이동했습니다. AST 대조로 이동한 본문·assertion과 기존 함수 68개의 보존을 확인했습니다. CI workflow/classifier·Shadow 동작·fsync/SQLite 정책은 변경하지 않았습니다.
- 기존 head에서 새 계약 4건 red, 수정 뒤 관련 mock/static 79 passed·1 skipped(로컬 비공개 단어 목록 미설정)·2 deselected(실제 제거/E2E). 로컬 subset은 7.13초이며 CI 시간 절감은 미측정입니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 결과는 기록에 남깁니다.
- 실제 subprocess/thread·제거·Hub/PI 운영·연구/bench/HPC/원자료는 실행하지 않았습니다. 변경된 child 선택의 실제 수집/상태/E2E·Windows/3.10 통합과 전후 CI duration은 controller 검증 경계입니다. Draft로 제출하며 CI·리뷰를 기다리거나 봇 호출·병합·배포하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
패치노트 가독성 개선

## 한 일
- 기존 1,440개 항목에 쉬운 첫 문장을 추가하고 아래에 기존 기술 설명을 그대로 표시합니다. 시간·SHA·PR 링크와 기술 기록은 보존 검사에서 변경 0건입니다.
- 새 항목은 `summary`에 쉬운 설명, `text`에 구체적인 변경을 쓰도록 작성 기준을 추가했습니다.
- 예: **이전 분석 결과를 다시 쓸 때 잘못된 파일이 연결되지 않도록 고쳤습니다.** 다음 줄에 기존 식별값·계획 승인 검사 설명이 이어집니다.

## 테스트 결과
- 관련 검사 15개 통과. 신규 계약은 수정 전 9개 실패·기존 6개 통과를 확인했습니다.
- Python 3.10 문법·기록 생성 검증·공개 검사 통과. 생성기 독립 검토 P1=0, 설명 1,440쌍 상호 대조 후 조건 누락·과장 10건을 보완했습니다.
- 기술 원문 보존으로 생성 본문은 290,968→364,971자입니다. 쉬운 문장의 중앙 길이는 44자입니다.

## 바꾼 파일
`scripts/notes_index.py`, 관련 검사, `patch_notes/entries/*.yaml`, `AGENTS.md`, `patch_notes/STYLE.md`

## 막힌 점
`patch_notes/README.md`·`STATUS.md`는 직접 수정하지 않았습니다. 병합 후 기존 자동 생성 workflow가 갱신합니다. 전체 CI·새 head 리뷰는 대기합니다.

## PI·Claude에게 물을 것
없음.

## 확인
- [x] 공개 정보 검사 통과
- [x] `docs/status/`에 같은 보고 추가
- [x] PI가 요청한 문서 변경 범위

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #797
Refs #672, #680
PR #807: P1 4323035753 — 요청 단위 외부 입력이 재사용 증거에서 빠지던 문제를 수정했습니다(`04fbc087`).

- `references`·`project_dirs`는 모든 step에 전달되므로 내용 증거가 없으면 CP1 전에 재사용 후보에서 제외합니다. 외부 입력을 열거나 hash를 추정하지 않습니다.
- dispatch에서 입력 부재만 기록해 retry/repair·archive·재승인에 보존합니다. 기록 없는 과거 결과도 제외합니다. metadata 삭제로 미검증 입력 이력을 지우지 못하며, 승인 후 입력 추가는 중단·새 CP1을 요구합니다. 새 기록·snapshot에 입력 경로/값을 넣지 않습니다.
- 기존 head에서 새 mock 회귀 20건 red, 수정 뒤 새 23건을 포함한 관련 mock/static **112 passed**. CP1/input-fit·재개·예산 rollback을 확인했습니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. 전체 red/green 로그는 로컬 D: 산출에 보존했습니다.
- 실제 process/thread/Hub 운영·PI·연구/bench·원자료/HPC는 미실행입니다. 기존 승인·보안 경계를 유지하며 #798/#811·P2·다른 owner 범위는 건드리지 않았습니다. 최종 head CI·리뷰/P1=0·병합/배포는 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #766 · Refs #681 · P1 수정 `2b6b8b4`.

- 4322864519: EncodedCommand·e/ec·약어의 literal UTF-16LE Base64를 실행 없이 decode해 검사합니다. host 옵션·script 인수를 구분하고 decode 크기·재귀 상한과 불명확한 encoded 입력 거부를 유지합니다.
- 4322864528: 이름 선택 객체의 ForEach-Object/foreach/% MemberName·positional Kill·wildcard 호출을 거부합니다. common parameter operand와 명시 PID·조회·데이터는 구분합니다.
- 4322864535: stdbuf의 short/long option operand 뒤 literal command를 기존 검사에 연결합니다. 4318107934는 기존 공식 계약의 stale 근거 답글로 제외합니다([근거·회귀](https://github.com/ehojune/bioinfo-team-3d/blob/codex/broad-process-stop-681/docs/reference/process-stop-681-followup.md)).
- 검증: 원래 head에서 새 회귀 **37 failed, 25 passed**, 수정 뒤 guard 9개 파일·text encoding **649 passed**. policy·flat/nested fake broker의 deny·card/event/submit 미호출·pending 없음을 확인했습니다. 기존 test·assertion은 유지했습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff 검사 PASS. guard 환경을 유지하고 전체 실패 로그를 보존했습니다. 초기 oversized test ID의 Windows 환경 변수 길이 오류는 짧은 ID로 고쳤으며 assertion은 그대로입니다.

한계: 실제 process/proc_tree·shell·PI·연구·HPC는 실행하지 않았습니다. OS 격리·PID tracker #675·P2는 별도 범위입니다. 새 CI·새 head 리뷰/P1=0·병합은 controller가 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #665
PR #791: 조상 입력 보호·성공 retry 증거·부분 정리·입력 hash 계약을 유지하고 CI fixture만 보정했습니다.

- 현재 head CI의 Linux 3.10/3.12는 같은 두 테스트에서 실패했습니다: `test_deleted_output_without_a_recorded_hash_is_missing`, `test_codex_profiles_keep_same_scoped_read_grants[False-workspace-write]`. 실행 중 run의 `--log-failed`는 제공되지 않아 완료된 job 로그를 API로 보존했습니다.
- `RunContext` stand-in에 실제 기본값 `read_only_inputs=[]`를 명시하고, 작은 hash fixture의 output/input cap을 함께 고정했습니다. missing/unrecorded·exit code·scoped read/deny assertion은 그대로입니다. production에 누락 필드를 허용하는 분기는 추가하지 않았습니다.
- 같은 두 실패를 mock으로 재현한 뒤 관련 **31 passed**, 최신 main 통합 뒤 같은 **31 passed**. 새 hash 허용·상한 거절·partial cleanup·native profile 구성·retry identity 회귀를 함께 확인했습니다. 전체 traceback은 작업별 D: artifact에 보존했고 기존 guard 환경을 유지했습니다.
- main `b9abcec7`의 #801 문서·#790 종료 기록 변경을 보존해 합쳤습니다. HANDOFF 충돌은 main 우선순위 표와 #791 검증 기록을 함께 남겨 해결했습니다(LF 4,748→3,439자). manual에는 #791 병합 전 구현임을 표시합니다.
- public·diff·Python 3.10 AST·patch-notes·notes 검사 통과. 실제 process/thread/signal/proc_tree·직원 CLI·PI·원자료/HPC·연구/bench·설치·게시·배포는 실행하지 않았으며 #790의 process/signal 테스트도 로컬에서 돌리지 않았습니다. native/전체 Windows 검증·최종 head CI·새 리뷰/P1=0·병합은 controller 담당입니다. 기존 SyntaxWarning·P2는 범위 밖입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Fixes #788 · main `b9abcec` 통합 `aaa0469`.

- HANDOFF 충돌에서 main의 독자별 안내와 PR의 fail-closed CI 정책 링크를 함께 보존했습니다. 정규화한 문자 수는 이전 PR 4,755 → 병합본 3,457(main 3,313)입니다.
- workflow·CI helper·consult/deadline test는 이전 PR head와 동일합니다. main의 #790 signal 코드·test도 그대로입니다. 전체 Windows/Python 3.10·안전 gate와 #787 진단은 유지합니다.
- 관련 mock/static **358 passed**, 사고 guard·환경 유지. 최초 검사는 Markdown 의존성 import에서 358 setup error로 중단됐고, 기존 임시 의존성을 재사용해 통과했습니다. 전체 실패 로그를 보존했고 설치·공통 fixture 수정은 하지 않았습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff 검사 PASS. 기존 consult mock 8건 통과 기록은 그대로이며 재실행하지 않았습니다.

한계: 기존 `3e5532c` CI는 확인 시 8건 통과·Windows 실행 중이었고 실패 job은 없었습니다. 통합 head의 새 CI·리뷰는 controller가 확인합니다. 실제 process/thread·PI·연구·bench·전체 suite는 실행하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #797
Refs #672, #680
PR #807: CP1 전 후보 검증·계획/승인 seal, dispatch 도구 증거와 부분 완료 결과 보존을 유지합니다.

- 최신 main `3e924495`를 merge해 HANDOFF 충돌을 해결했습니다. main의 독자별 안내·현행 큐와 #807의 context/도구/현재 차수 결과 보존 근거를 함께 남겼습니다. HANDOFF는 LF 기준 4,690→3,314자로 줄었으며 이전 검증 기록 링크를 유지합니다. manual은 #807 구현이 병합 전임을 표시합니다.
- 이번에는 제품 코드·테스트·고정 hash/계약 항목을 바꾸지 않았습니다. CP1/input-fit·승인 후 drift·부분 완료 보존·Shadow 분리 관련 mock/static **38 passed**. 실제 process/thread/proc_tree·Hub·연구/bench·PI·원자료/HPC는 실행하지 않았습니다.
- 시작 시 head `2787ef08`에서 Linux 3.10/3.12 등 완료 CI는 SUCCESS, Windows는 IN_PROGRESS였고 실패 job은 없었습니다. 요청한 `gh run view --log-failed`도 실행 중이라 로그를 제공하지 않았습니다. 실패를 추정해 코드를 고치거나 CI를 기다리지 않았습니다.
- public·diff·patch-notes·notes 검사 통과. 전체 실패/검증 artifact는 작업별 D: 임시 폴더에 보존했습니다. 최종 head CI·새 리뷰/P1=0·병합/배포는 controller 담당이며 실제 연구 효용·native/Windows 전체 실행은 별도 검증 경계입니다. #798/#811·P2·다른 owner 범위 미착수입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## PR #802 P1 후속

4321008909는 기존 45240fb9의 started_here=False와 실제 함수/autospec 회귀로 stale입니다. 나머지 세 건을 수정했습니다: post-spawn 로그 실패도 소유 handle cleanup을 실행하고, PID 기록 없는 과거 이력은 현재 인증·runner 연결이 확인된 경우만 해소하며, Windows role·supervisor는 현재 PID 생성 시각·생존·ANY-Job 비소속을 조회합니다. unknown·stale·조회 오류는 실패하고 WMI 신원 미확정 PID에는 종료 권한을 부여하지 않습니다.

수정 전 새 회귀 20 failed/6 passed, 수정 후 관련 mock/static 191 passed. guard 유지·blocked 0, Python 3.10 AST 5파일·encoding 검사 PASS. 실제 Native/WMI/process/thread/signal·PI·연구·HPC는 로컬 미실행입니다. 기존 positive/nested CI gate는 그대로입니다.

manual 172,387→172,624자, reference 4,357→5,000자(LF Unicode). 근거 docs/reference/windows-runtime-795.md. push 뒤 CI를 한 번 조회하고 총괄에게 인계하며 봇 요청·대기·병합·배포는 하지 않습니다.

Fixes #665
PR #791 · 리뷰 수정 `7ec4a59`.

- Codex 입력 snapshot은 POSIX/Windows·resume에 같은 native `:read-only` 기반 profile을 적용하며 `outputs/`·`.tmp/`만 씁니다. 추가 write root·사용자 config·extra CLI args로 넓히지 않고, 작업 폴더를 재사용하는 후속 turn도 입력을 보호합니다. 실제 sandbox 실행 검증은 별도입니다.
- 조상 metadata에 결과 task id를 전달해 첫 실패 run 대신 해당 성공 retry run을 검사합니다. manifest의 요청·step·성공 상태·파일 hash와 최신 per-run identity 검사를 유지합니다.
- 거부된 조상의 부분 복사·이전 snapshot은 inventory 소유 파일만 지웁니다. 원본·알 수 없는 파일은 보존하고 정리 실패는 직원 실행 전에 중단합니다. 선언 단계의 수집 hash 상한은 input의 파일/총량 허용 상한까지 보장합니다.
- 원래 head에서 보강 회귀 **14 failed** → 관련 mock/static **67 passed, 1 skipped**. 성공 retry·누락/다른 run·부분 실패/정리 실패·큰 입력 허용과 상한 거절·profile 구성·DAG task binding을 검사했습니다. 기존 hash cap/growth assertion은 유지하고 fixture의 output/input cap을 함께 고정했습니다.
- 지적 4320685868은 기존 head의 patch-notes 검사 통과와 현재 entry를 확인해 stale 근거로 답글을 남깁니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. 최신 main #810 변경을 merge로 보존했습니다. 실패 traceback은 작업별 local artifact에 보존했고 mock guard blocked는 0건입니다.
- 실제 process/thread/proc_tree·직원 CLI·PI·원자료/HPC·연구/bench·설치·게시·배포 미실행. 권한 fixture skip·전체 suite·native sandbox·Windows/3.10 실제 실행은 CI/별도 승인 경계입니다. 기존 SyntaxWarning은 범위 밖이며 P2·다른 owner 변경은 건드리지 않았습니다. 최종 head CI·새 리뷰·P1=0·병합은 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

# 재현 receipt 유니코드 source 경로 (#773, PR #816)

## 결론

PR #816(draft), 커밋 `fb5e47c`. 재현 receipt가 공개 source의 안전한 유니코드·구두점 경로(`code/analysis/outputs/scripts/분석.py`, `run (v2).py` 등)를 `blocked_path`로 막지 않고 문법 검사·실행합니다. Refs #773.

## 재현 (현재 main)

- 정리본의 `_code_target`은 공개 파일의 basename을 그대로 둡니다.
- 그런데 `labhq/reproduction_receipt.py::_path`는 `[A-Za-z0-9_./ -]`만 받았습니다. 그래서 정상 Python source가 `redacted-…py`·`blocked_path`로 기록되고 문법 검사를 받지 못했습니다.
- 새 positive test 5개가 수정 전에 실패했습니다(점으로 시작하는 ASCII 이름은 원래 통과).

## 바꾼 것

- **받는 것:** NFC 유니코드와 일반 구두점 이름(괄호·쉼표·`+`·`;`·`@`·`#`·`№`·공백이 든 폴더 등).
- **계속 거부:** 절대·drive(`C:`)·UNC·backslash 경로, `..`·`.labhq`.
- **새로 거부:**
  - 제어(Cc)·서식(Cf, 예: RLO·zero-width)·surrogate·사설·미할당 문자, 줄·문단 구분 문자, 일반 공백이 아닌 공백(NBSP 등)
  - Windows 예약 문자(`:<>"|?*`)와 장치 이름(`CON`·`con.tar.py`·`LPT1` 등)
  - 점·공백으로 끝나거나 공백으로 시작하는 부분(Windows가 이름을 바꿈)
  - NFC가 아닌 표기(같은 이름의 두 철자), UTF-8 255 byte를 넘는 부분
- **ASCII 강화:** 장치 이름과 끝 점·공백은 ASCII에서도 전에는 받았는데, OS 경로 모호성이라 함께 막았습니다.
- **막힌 이름의 처리:** 지금처럼 redacted이고, staging·실행하지 않으며, 결과는 `blocked_path`/`blocked_syntax`로 남습니다(성공으로 쓰지 않음).
- **바꾸지 않은 것:** `_path`는 파일을 열지 않는 논리 경로 검사입니다. #781의 hard link·링크 폴더 보호, #771의 script별 산출 판정, #800의 `-E -s -B` 실행·`-I -S -B` 문법 검사, 입력·caller 환경 격리는 그대로입니다.

## 확인 (로컬 mock/static만)

- **수정 전:** 새 test에서 16 failed. 유니코드 positive 5개, ASCII 장치 이름·끝 점/공백 negative 9개, 끝단 test 2개입니다.
- **수정 뒤:** 가짜 `subprocess.run`으로 receipt mock test 61 passed, 2 skipped(symlink 권한, 실제 interpreter CI test).
  - 끝단 test는 유니코드 source를 published 이름 그대로 임시 폴더에 두고 문법 검사·실행 명령·예상 산출 경로를 확인합니다.
  - 막힌 이름 4개는 redacted, 미실행, 파일 미생성을 확인합니다.
- Python 3.10 문법, `check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것·한계

- **실제 interpreter·R·bash에 유니코드 이름 전달:** 로컬에서 실행하지 않았습니다. 실제 interpreter test는 CI에서만 돕니다.
- **NFD 이름:** macOS 등에서 NFD로 만들어진 이름은 `blocked_path`로 남습니다(보수적).
- #774는 별도입니다.

## 단계
PR #766 광역 종료 guard 후속, Refs #681.

## 수정
- P1 4321811293·4321811304: 이름 선택 expression의 Id와 process substitution의 PID를 종료 consumer에 연결합니다.
- P1 4322020342·4322020348: Invoke-Expression·watch의 literal 실행 payload를 검사합니다.
- P1 4322020355·4322020358: kill의 nonpositive PID와 이름 선택 객체의 Kill()을 거부합니다. 조회·signal operand·명시 PID·문자열 데이터의 음성 회귀를 유지합니다.
- 4318107934는 기존 [공식 계약의 stale 근거 답글](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4320835135)로 제외합니다. [세부 근거](../reference/process-stop-681-followup.md). manual은 기존 정책 문단만 갱신했습니다.

## 검증
- 새 문자열/fake 회귀: 수정 전 **43 failed, 41 passed**. 수정 후 guard 8개 파일·text encoding: **587 passed**. 원래 test·assertion은 수정하지 않았습니다.
- policy와 flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. 사고 guard 로드·환경 변수 3개 유지, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff 검사 PASS. #800 merge의 ancestry를 확인했습니다. workflow·runner·공통 fixture는 변경하지 않았습니다.

## 한계
실제 process/proc_tree·shell·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2는 별도 범위입니다. 새 head CI는 조회·대기하지 않으며 controller가 CI·리뷰·병합·배포를 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6-luna) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

PR #793 · Fixes #788 · Windows consult fixture 후속 · 수정 `0e1a3f8`.

- `07e43ede` 실패는 late reconnect 뒤 fixture의 `settle()` 3초 watchdog에서 발생했습니다. 제품 결함은 확인되지 않아 이번 수정은 test에 한정했습니다.
- runner 대기와 adoption future 설치를 event로 확인하고, adoption 알림을 제어된 gate로 지연시켜 새 provider가 겹치지 않는지 검사합니다. 대상 두 parameter만 60초 outer watchdog을 쓰며 다른 test의 `settle()` 제한과 기존 assertion 8개는 그대로입니다.
- 최초 로컬 검증은 Git HEAD 조회의 Popen을 guard가 2건 차단했습니다. optional Git provenance만 module-local fake로 바꾼 뒤 **8 passed**입니다. 원래 process fixture는 재실행하지 않았고 guard 환경·전체 실패 로그를 보존했습니다.
- main `4436b23`(#812 `8cc845b` 포함)을 병합했습니다. workflow와 CI helper는 병합 전과 동일하며 공통 fixture·제품 recovery 코드는 수정하지 않았습니다.

검증: same-generation·late reconnect·세대 변경 격리·다른 ask/agent 배제·비용 회수 mock 8건, Python 3.10 AST·기존 assertion/helper 동일성·workflow/helper 동일성·integrations·public·patch-note·notes·diff 검사 PASS.

한계: CI Windows의 원래 3초 지연 원인은 미확정입니다. 실제 process/thread·provider·PI·연구·bench·전체 suite는 실행하지 않았습니다. 새 head CI·리뷰·병합은 lead가 맡습니다.

# 응집형 분석 스텝 정책과 bench 계약 최종 범위 (#799)

## 결론

PR #815(draft), 정책 커밋 `7330e77`. #799의 최종 범위입니다. 공통 계획 정책에 응집형 분석 스텝 문장을 더했고, 4-arm bench에서 무엇을 셀지 정한 계약과 결정적 계획 예제 test를 두었습니다. Refs #799.

**bench·실운전·모델 실행은 하지 않았고, 속도·비용·품질 효과는 측정하지 않았습니다.**

- **근거:** [#799](https://github.com/ehojune/bioinfo-team-3d/issues/799)의 PI 10-20 외부 감사 요구(데이터 탐색·전처리·핵심 통계처럼 강하게 결합된 작업은 같은 분석 스텝에서 잇고, 이종 벤더 qa-gate/critic·CP2는 유지)와 [#760 반영 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6281989709).
- **승인:** 총괄 확인 [#760 재개·배정](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6285136614). 이 승인은 정책·준비 작업까지이며, 연구·bench 실행 승인이 아닙니다.

## 바꾼 것

- **`labhq/bioinfo_policy.py`** (`BIOINFO_ASSIGNMENT_RULE`)
  - 같은 데이터·질문의 탐색·전처리·핵심 통계는 한 분석 단계에 두고, 데이터셋·질문·근거 갈래가 다른 진짜 독립 작업만 나눈다는 문장을 더했습니다.
  - 생물학적 해석, 독립 technical·scientific QC 단계, 다른 회사 과학 리뷰, CP1·CP2 승인·근거 검사는 따로 둡니다.
  - 같은 상수라 일반·연구·연구 CP2·재계획 prompt와 continuation이 모두 받습니다. `cso.py`는 고치지 않았습니다.
  - 직원 공통 규칙에도 들어가므로 "In a plan,"으로 시작합니다.
  - nf-core 배정과 건별 기여 승인 문장은 그대로입니다.
- **`tests/test_coarse_plan_799.py`** (결정적, 9개)
  - 새 문장과 경계 문구가 모든 계획 template에 있고, nf-core·기여 승인 문구가 남는지 확인합니다.
  - 최초·재계획·continuation 계획 예제로 split 후보와 유지 경계를 셉니다.
  - continuation의 rerun 수는 **계획상 기대 dispatch**이지 실제 CLI 시작 수가 아님을 명시했습니다(리뷰 P2).
- **`docs/reference/bench-coarse-contract.md`**
  - 계획 기대값, task 기록 대리값, 실제 관측값(runner의 CLI 시작·byte)을 나눕니다.
  - 관측하지 않은 재시도·consult·CLI 시작·byte는 0이 아니라 `미수집`으로 둡니다(리뷰 P2).
  - arm별 값의 수집 위치(각 arm 폴더의 `run.json` 옆)와 기록기 미구현을 적었습니다.
  - 기존 수치(144/19·21:26 벤치 A, 22:28 벤치 C 재채점, C 원 보고 85.9분)는 출처와 함께 두고 인과 결론은 내리지 않았습니다.

## 확인 (로컬 mock/static만)

- 새 정책 test는 수정 전 문장에서 실패했고(1 failed, 8 passed), 수정 뒤 통과합니다.
- `test_coarse_plan_799`·`test_role_footer`·`test_ask_policy`·`test_step_prompt_rules`: 45 passed.
- `test_nfcore_default_732`: 14 passed. 모든 계획 경로의 nf-core 배정 test를 포함합니다. 실제 하위 프로세스를 쓰는 restart test 5개는 mock guard가 막았고 다시 돌리지 않았습니다.
- Python 3.10 문법, `check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과. 계약 문서 링크 깨짐 0건.
- 독립 리뷰 P1=0은 `41475fd4` 기준이며, 이 head는 그 뒤 변경입니다.

## 남은 것·한계

- **기록기 미구현:** task 기록 대리값과 runner의 실제 CLI 시작·byte를 arm 폴더에 쓰는 코드는 없습니다. bench dry-run 전 별도 작업이며, 그때까지 해당 칸은 `미수집`입니다.
- **효과 미측정:** 새 문장으로 실제 CSO 계획이 응집되는지, 속도·비용·품질이 달라지는지는 측정하지 않았습니다. 실제 arm 측정은 PI의 별도 bench 실행 승인이 필요합니다.
- **목차 링크:** `docs/reference/README.md` 목차와 manual 반영은 #801 등 다른 PR이 그 파일을 고치는 동안 미뤘습니다.

Fixes #797
Refs #672, #680

PR #807: CP1 재사용 계약 `232690b`, 복구/예산 경계 `d175432`, 리뷰 수정 `ea0f289`.

- 실행 prompt와 identity가 같은 노드별 과학 context를 사용합니다. scoped prompt 이전의 도구 signature는 증거로 인정하지 않습니다.
- 도구·engine 설정은 CP1 대기 전 대신 실제 dispatch에서 기록합니다. retry/wake/결과 교정의 혼합 설정, 설정 변경 중 실행·증거 없는 복구는 재사용을 거절합니다.
- 부분 실행 뒤 drift는 진행 중인 차수와 완료 결과를 보존합니다. 새 CP1은 현재 차수의 완료 결과도 입력·도구·산출 증거를 검사해 snapshot에 포함하며, 증거가 깨져 있으면 기록을 남긴 채 중단합니다. 조용한 재실행이나 승인 우회는 없습니다.
- 기존 head에서 실제 실패 패턴 **8 failed**를 재현한 뒤 관련 mock/static **113 passed**. 현재 차수 완료 결과의 재승인·중복 dispatch 방지, CP1 카드/seal, 새 계획 전 restart, 예산 rollback, #796 input-fit/지표·Shadow 분리를 확인했습니다. 지적 4321664411은 기존 `d175432`와 복구 회귀 테스트로 해결을 확인했습니다.
- Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. 최신 main의 #810 변경을 merge로 보존했습니다. 전체 traceback은 작업별 local artifact에 보존했고 mock guard의 blocked는 0건입니다.
- 실제 process/thread/Hub·연구/bench·PI·원자료/HPC 실행 없음. Windows/3.10 실행·전체 suite·최종 head CI/리뷰·P1=0 판단은 controller 담당입니다. 파일 변경을 잠그지 않으며 실제 도구 설치 판본·과학 효용은 미검증입니다. 기존 P2와 #798/#811은 범위 밖입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## PR #790 P1 4320895582

첫 await 전에 dispatch 완료 future를 등록하고 acceptance·중단 결과 기록까지 종료 drain이 기다리도록 고쳤습니다. 기존 task 정리 중 들어온 handler도 재검사하며 ACK·5초 제한·미확인 outbox와 게시/승인 gate를 유지합니다. timeout은 handler의 completion future를 취소하지 않습니다.

검증: 기존 head에서 회귀 5 failed/1 passed, 추가 drain interleaving 1 failed를 확인했습니다. 수정 후 메모리 store·coroutine barrier 및 기존 fake signal callback을 포함한 mock/static 19 passed. Python 3.10 AST·encoding 검사가 통과했고 guard를 유지했습니다. 실제 writer thread·신호·process·운영·PI·HPC는 미실행입니다. 이전 guard 차단 startup 테스트는 재실행하지 않았습니다.

CI는 push 뒤 한 번만 조회하며 완료를 기다리지 않습니다. fresh-head 리뷰·병합·배포는 총괄이 맡습니다. 근거: docs/reference/runner-signals-664.md.

# 4-arm bench 준비: 응집형 분석 스텝·handoff 기록 계약 (#799)

## 결론

PR #815(draft), 커밋 `38145a4`. #799의 결정적 준비만 했습니다. 4-arm bench에서 응집형 분석 스텝과 handoff 비용을 무엇으로 셀지 정한 계약 문서와, 기존 정책(#732)을 검증하는 계획 예제 test입니다. Refs #799.

**bench·실운전·모델 실행은 하지 않았고, 어떤 성능 수치도 측정하지 않았습니다.**

## 바꾼 것

- `docs/reference/bench-coarse-contract.md`
  - 기존 `BIOINFO_ASSIGNMENT_RULE`의 주입 경로를 정리했습니다.
  - 계획 모양 셈법: 단계, handoff, 유지 경계, 응집·pipeline split 후보, 넘기는 산출 수.
  - arm별 기록 항목: 단계 수, CLI 시작 수, 전달 문맥량, 계획/skill/승인 대기/분석/review/보고 시간·비용. 항목마다 지금 얻는 곳과 기록기 유무를 적었습니다.
  - 고정 조건(같은 입력·판본·예산·PI 답·blind rubric·arm 격리)과 합치지 않는 경계(QC, 생물학적 해석, 다른 회사 리뷰, CP1·CP2)를 적었습니다.
  - 기존 수치(144/19·21:26 벤치 A, 22:28 벤치 C 재채점, C 원 보고 85.9분)는 출처와 함께 두고, 인과 결론은 내리지 않았습니다.
- `tests/test_coarse_plan_799.py` (결정적, 8개)
  - 규칙이 일반·연구·연구 CP2·재계획 prompt 맨 앞에 붙고, continuation은 연구 계획 prompt에 덧붙는 구조임을 확인합니다.
  - 최초 계획: 과분할 대 응집 예제, pipeline 준비·technical QC 분할 예제, 병렬 독립 분석 예제.
  - 재계획: 유지+새 단계를 함께 셉니다.
  - continuation: 기존 재사용 규칙(`split_reuse`)으로, 분석을 나누면 CLI 시작이 하나 늘어남을 보입니다.
  - 예제는 모두 `validate_steps`를 통과하는 실제 형식의 계획입니다.

## 결정 필요

pipeline이 아닌 분석의 탐색·전처리·핵심 통계를 한 단계로 이으라는 문장은 정책에 없습니다. 넣으면 모든 요청의 계획 prompt가 바뀌므로 정책 문장은 바꾸지 않았습니다. bench 전에 총괄·PI가 정해 주세요.

## 확인 (로컬 mock/static만)

- `tests/test_coarse_plan_799.py` 8 passed. 모델·CLI·하위 프로세스 없음, mock guard 유지.
- Python 3.10 문법(`ast.parse(feature_version=(3, 10))`), `scripts/check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.
- 계약 문서의 상대 링크 깨짐 0건.

## 남은 것·한계

- **기록기 미구현:** §3의 기록기(task 기록에서 단계별 시간·비용·CLI 시작·prompt 크기를 뽑는 코드)는 구현하지 않았습니다. `labhq/bench.py`와 `orchestrator/cso.py`는 범위 밖이라 건드리지 않았습니다.
- **예제는 손으로 만든 것:** 실제 CSO가 이런 계획을 내는지는 bench dry-run에서 확인해야 합니다.
- **목차 링크:** `docs/reference/README.md` 목차 링크는 #801이 같은 파일을 고치고 있어 그 병합 뒤에 더해야 합니다.
- **README·manual:** #801 진행 중이라 건드리지 않았습니다.

## ancestor fixture import 경로

확정 원인: detached direct child의 script 경로는 임시 폴더이며 pytest/WMI caller의 sys.path는 상속되지 않습니다. parent-fixture.log의 tests import 실패를 재현했습니다. launcher·parent가 이미 전달받는 checkout root를 helper import 전에 추가합니다. 제품 코드·Job gate·소유 handle·parent-pin 단정은 유지합니다.

로컬 mock/static 13 passed. 새 회귀 2개는 import cache를 우회한 경로 검색으로 수정 전 ModuleNotFoundError, 수정 뒤 통과를 확인합니다. 기존 fixture contract·Python 3.10 AST·encoding 검사도 통과했습니다. guard 유지·blocked 0, 실제 process/WMI는 로컬 미실행입니다.

03828116의 제공된 Windows 로그는 positive·nested Job-close 생존 PASS, 1 failed/8510 passed/48 skipped입니다. 새 head CI는 미확인이고 조회·대기·봇·병합·배포는 하지 않습니다. 수정 범위는 해당 test와 필수 기록뿐입니다.

## 단계
읽는 사람별 문서 정비(PI 요청) — 문서만 변경, PR #801

## 한 일
- main(#800·#803·#805·#806)을 합치고 HANDOFF 충돌을 현재 우선순위로 풀었습니다. #805의 manual 독자 분리·다이어그램·수치와 #806 hook 안내는 그대로입니다.
- README 한영판: PR 번호·감사 공지·PI 창구 행을 빼고 "v0.5 뒤 기능은 실제 연구 요청으로 재확인 전"이라는 안정적 한계를 적었습니다. 영문판은 한국어판 구성에 맞췄습니다.
- HANDOFF: 접수 중지, 병합 전 열린 PR 7개, 보류 항목, 접수 재개 뒤 순서만 남겼습니다. token 수치는 빼고 운영 기준·#760으로 보냈습니다.
- AGENTS·CLAUDE는 개발 절차만, 점검 주기·동시 투입·한도·접수 중지는 `docs/reference/lead-operations.md`로 옮겼습니다.
- manual: #796 문단을 PR #803 병합 사실로 고치고, 어휘 수를 코드 기준 125개(topic 45, EDAM 연결 79)로 맞췄습니다.
- 총괄 최종 대조: manual의 CP1 입력 검증/사후 그림자 측정 설명을 구분하고, PI Q&A 현재 운영의 옛 한도·깨진 HANDOFF anchor를 고쳤습니다. 날짜가 붙은 과거 답은 보존했습니다.
- 감사 우선순위 reference에 10-20 진행을 날짜 붙여 더했습니다(기존 표는 보존).

## 확인
- 코드 대조: `agents/core/*.yaml`(Claude Code 8·Codex 5, 리뷰어 Codex gpt-6-astra), `labhq/vocab/output_types.yaml`(125 key)·`edam_subset.yaml`(79 연결)·`topic_checklists.yaml`(45 분야·135 항목), `labhq/init_defaults.py`(`semantics.mode: ab`).
- `integrations.py --check`·`vocab_tables.py --check`·`notes_index.py --validate`·`patch_notes.py check`·`check_public.sh`·`git diff --check` 통과. 수정 문서 8개의 상대 링크·manual anchor 0건 깨짐.
- `tests/test_integrations.py`·`tests/test_project_link_spellings.py` 39 passed. CLI를 하위 프로세스로 띄우는 2개는 mock guard가 막았고 같은 검사(`integrations.py --check`)를 직접 통과했습니다.
- 글자 수(main → 후): 개발 문서(AGENTS·CLAUDE·HANDOFF) 8,368 → 6,845, README 한영판 포함 39,255 → 36,127.

## 남은 것
- 열린 PR #766·#790·#791·#793·#802·#807·#812가 병합되면 manual 해당 절과 HANDOFF 표를 그때 갱신해야 합니다.
- README 한영판은 badge·이중 언어 때문에 목표 15k보다 큽니다. badge 블록은 생성물이라 줄이지 않았습니다.

PI가15:44 신규 일반 작업 보류를 해제했다. HANDOFF·운영 기준·PI Q&A만 최신 결정으로 맞췄다. 실제 연구·bench는 별도 승인, README는 이번 운영 정정으로 늘리지 않았다.

16시 대조: #812 병합, #815 draft 준비와 #798 순서를 개발 문서에 갱신했다. 실제 요청 성능·배포 성공은 미확인이다.

PR #793 · Fixes #788 · Refs #786 · 수정 `5e55e9d`.

- workflow+PR 번호별 concurrency를 두고 pull_request만 취소합니다. main·dispatch는 run/attempt별 group입니다. checkout/workflow/helper·artifact/run/attempt·전체 gate 증명은 유지합니다.
- 첫 PR의 전체 base diff로 prose를 판별합니다. manual·AGENTS/HANDOFF·정책 문서는 내용·구조·예제가 같은 문장 경계 줄바꿈만 후보입니다. 정책 표현 변경은 일반 문서에서도 full이며 [좁은 fallback](../reference/ci-reuse.md)을 명시했습니다.
- 실제 AGENTS·HANDOFF·CI reference의 줄바꿈 positive를 확인했습니다. 현재 manual은 기존 inline markup의 모호성 때문에 full입니다. #805 내용을 고치거나 parser 지원 범위를 넓히지 않았습니다.
- 가벼운 gate에 vocab·topic checklist·private-path 기본값·encoding 검사를 보강했습니다. README/integrations·notes·public·PR patch-note 검사는 유지합니다.
- `.005` polling 세 곳을 module-local fake clock·heartbeat·취소 handshake로 바꿨습니다. 실제 deadline 인자·취소 완료·PDF 승인 대기 제외·누적 예산을 검증하며 main의 limiter 회귀는 동일합니다. 제품 코드는 수정하지 않았습니다.
- main `b4947c6`을 병합했습니다. #805 문서 구조와 #787의 3.10 25분 제한·faulthandler·stall snapshot을 보존했습니다.

검증: 새 CI 계약 수정 전 8건, 경로와 무관한 정책 문장 회귀 18건 실패를 재현했습니다. 수정 후 CI 계약·기존 판별/재사용·encoding·external_wait·notes·vocab·private-path·topic mock/static **388 passed**, integrations **31 passed / subprocess CLI 2건 제외**입니다. Python 3.10 AST·#787 step 동일성·integrations/vocab CLI·public·patch-note·notes·diff 검사 PASS. guard 환경은 그대로이며 실패 전문은 로컬에 보존했습니다.

남은 경계: PR800은 OPEN입니다. `1bbb0615`의 stopped-flag 제품 수정은 가져오지 않았으며 병합 후 새 head 전체 CI가 필요합니다. Windows 실패는 stdout reader의 EOF 대기 timeout까지 확인했습니다. current main에도 CDP 응답/close의 무기한 대기가 있으나 실제 원인은 **UNVERIFIED**입니다. browser/process/thread·실제 Python 3.10/Windows·전체 suite·artifact 왕복은 로컬에서 실행하지 않았습니다. 후속 CI·리뷰·병합은 lead가 맡습니다.

## supervisor fixture 후속

PR #802의 fake-role 시작과 parent-pin chain을 raw Popen에서 기존 verified spawn으로 연결했습니다. direct/WMI의 보유 handle과 file output을 함께 다루며 실패 시 기존 Job journal·fixture traceback을 제공합니다. 제품 gate와 lifecycle/restart/parent-pin 단정은 유지합니다.

확정한 결함은 fixture의 launch 경계 우회입니다. redirector Job이 네 실패의 직접 원인인지는 기존 로그로 확정하지 않습니다. d5954101 Windows 로그의 positive·nested WMI 생존은 PASS지만 수정 fixture의 실제 CI는 미확인입니다.

로컬 mock/static 155 passed, guard 유지·blocked 0. Python 3.10 AST·public·diff·기록 검사 결과는 [근거](../reference/windows-runtime-795.md)에 연결합니다. reference 3,702→4,357자(LF Unicode), manual 166,480자 유지. 실제 process/WMI는 로컬 미실행이며 CI 조회·봇·병합·배포는 총괄 소관입니다. manual·#805 문서는 수정하지 않았습니다.

## 2026-10-20 14:40 — `pi-language-linear`

## 단계
P1 — 긴 보고서의 경로 판별 지연 제거. Fixes #810.

## 한 일
- 경로 존재 판별을 고정 길이 패턴으로 바꿔 긴 접두문에서 반복 탐색을 없앴습니다. 입력을 자르지 않아 끝의 경로도 찾습니다.
- Unicode·상대/절대 경로·코드 감지와 25,000·100,000자 회귀 검사를 추가했습니다. 기존 통역 test의 Git provenance만 fixture에서 가짜 값으로 바꿨습니다.

## 테스트 결과
- mock-only guard 유지: 관련 146개 통과(85.58초). 제한 자료·예산·timeout·종료 안전 검사를 유지했습니다.
- 지정 followup test: 수정 전 23.54초 → 수정 후 7.10초. **1초 목표는 미달**입니다.
- 단일 test profile: 6.99초 중 SQLite transaction 종료 5.63초, 경로 판별 25회 합계 0.058초. 저장 정책은 변경하지 않았습니다.
- 순수 판별 25,000자: 약 0.99–1.06초 → 0.00065–0.00082초. 경로 패턴은 짧은 문자열 55,987개에서 기존 판정과 일치했습니다.
- 첫 검증의 Git subprocess 시도는 guard가 차단했습니다. 자식은 시작되지 않았고 fixture 수정 뒤 관련 검사가 통과했습니다.

## 확인
- 공개 검사·patch-notes·notes-index 검사 통과. Python 3.10·Linux 실행은 CI 확인 전입니다.
- 연구·PI 실행·HPC·bench·배포는 하지 않았습니다. 최종 CI·리뷰·병합은 총괄이 이어갑니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
PR #766 이름 기반 광역 종료 guard — `d16dd8e`, Refs #681.

## 수정
- P1 4321588842: skill의 이름/광역 선택 종료를 거부합니다. short/long option·붙인 operand·약어를 읽고 no-action·help/version·signal listing과 명시 PID를 구분합니다. operand로 쓰인 조회 표식은 예외가 아닙니다.
- P1 4321588847: pidof를 이름 선택 PID producer로 추적해 command substitution·pipeline·xargs와 연결합니다. skill no-action의 이름 선택 PID를 종료에 넘기는 경로도 거부합니다.
- 4318107934는 기존 공식 parameter 계약의 stale 근거 답글이 있어 제외했습니다. [근거·회귀·경계](../reference/process-stop-681-followup.md). manual은 해당 정책 문단만 갱신했습니다.

## 검증
- 새 회귀: 수정 전 **22 failed, 30 passed**. 수정 후 guard 7개 파일과 `tests/test_approval_closed_grammar.py`: **544 passed**.
- policy·flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·notes·diff 검사 PASS.

## 한계
실제 skill/pidof·process/proc_tree·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2는 별도 범위입니다. CI·새 head 리뷰·병합·배포는 controller가 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

Fixes #797
Refs #672, #680
PR #807. 재사용 계약 `232690b`, CI 회귀 수정 `d175432`.

CP1 전에 검증한 노드별 재사용 snapshot을 계획/승인 hash와 묶습니다. 승인 뒤·재개·dispatch 직전 증거가 달라지면 재실행 대신 중단·새 CP1을 요구합니다. 과학 계약·도구/engine 설정·의존 산출 hash를 비교하고, 증거 없는 입력·manifest 없는 디렉터리는 제외합니다.

- CI 회귀: 무조건적인 request 조회를 연구 step 경계 안의 안전한 조회로 바꿔 consult/wake/nonresearch 동작을 보존했습니다. 새 계획 채택 전 보관 차수의 이미 결정된 checkpoint 복구만 구분하며, 채택된 계획의 새 CP1·snapshot 검증은 유지합니다.
- 해시 증명과 실행 진행을 구분했습니다. 예산 거절 전에는 실행 준비 완료로 표시하지 않아 이전 계획·승인·집행 상한을 rollback합니다. 실제 dispatch 뒤의 drift는 중단하되 그 차수를 되돌리지 않습니다.
- 실제 CI 패턴에서 누락 request mock 16건과 연구 복구/예산 4건, 합계 **20 failed**를 재현한 뒤 관련 mock/static **100 passed**. 새/동일 hash 계획 CP1, CP1/CP2 revise·resume, 예산 상한 rollback, 승인/dispatch 뒤 drift, #796 input-fit/지표·Shadow 분리를 검사했습니다. 기존 연구 실패 테스트의 assertion은 유지했습니다.
- Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. git probe는 명시 mock, native thread 시작 금지·to_thread 인라인 mock, 기존 guard 환경 유지·blocked 0건입니다. 전체 실패/성공 trace는 local artifact에 보존했습니다.
- 실제 Hub를 구성하는 ask 37건·process/thread/signal·전체 suite·Windows/3.10 실행은 CI 경계입니다. PI·원자료/HPC·연구/bench·봇 요청/대기·병합·배포는 하지 않았습니다. #798/#811·다른 owner 범위 미착수. 실제 과학 효용·도구 설치 판본은 미검증이며 파일 변경을 잠그지 않습니다. 최종 CI·리뷰·P1=0·병합은 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>

## 단계
PR #766 이름 기반 광역 종료 guard — `73bb72b`, Refs #681.

## 수정
- P1 4321387908: find의 unquoted `{}` 뒤 후속 action을 이어 검사합니다. exec/execdir/ok/okdir·batch·wrapper 회귀를 추가했고 기존 prefix 거부와 shell/block lexer는 유지합니다.
- P1 4321387934: 이름 선택 pipeline의 `$_`/`PSItem`에서 나온 ID를 직접·괄호식·복사 변수로 전달해도 거부합니다. 명시 PID 조회와 이름 선택에서 유래하지 않은 PID는 기존 경계를 유지합니다.
- 4318107934는 공식 parameter 계약의 stale 근거 답글이 있어 제외했습니다. [지적별 근거·경계](../reference/process-stop-681-followup.md).

## 검증
- 새 회귀: 수정 전 **17 failed, 12 passed**. 수정 후 guard 6개 파일과 `tests/test_approval_closed_grammar.py`: **492 passed**.
- policy·flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·notes·diff 검사 PASS. 기존 정책의 누락 경로를 고쳤으며 상세 근거만 갱신했습니다.

## 한계
실제 shell/process/proc_tree·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2는 별도 범위입니다. CI·새 head 리뷰·병합·배포는 controller가 맡습니다.

## 2026-10-20 13:36 — `gemini-manual-architecture`

**[Gemini]**

## 단계
문서 및 아키텍처 매뉴얼 정리 — `docs/manual.md` · `README.md` · `README.en.md` · `docs/reference/readme-evidence.md` · `AGENTS.md`

## 한 일
- **매뉴얼(`docs/manual.md`) 내 개발 에이전트 전용 규칙 분리**:
  - `## 설정 포인트`에 섞여 있던 개발용 `pytest` 홈/캐시 격리 규칙 및 Ubuntu Python 3.10 CI 진단·대기 상한 안내, `signatures.yaml`·`topic_checklists.yaml`의 테스트/fixture 작성 지시를 `AGENTS.md`로 이관하고 매뉴얼은 `labhq`를 깊이 공부·운영하려는 사용자 관점 설명으로 정리했습니다.
  - 목차에서 누락되어 있던 `[PI에게 보이는 글](#pi에게-보이는-글)`을 목차에 추가하고, 프롬프트 지시문 어조 대신 `labhq/pi_language.py`와 `labhq/korean_qc.py`의 평문 변환·문체 점검 동작 설명으로 다듬었습니다.
- **전체 계층 구조와 모듈 아키텍처 및 다크 다이어그램 추가**:
  - `docs/manual.md`의 `## 구조와 이벤트` 아래에 `### 전체 계층 구조와 모듈 아키텍처` 절을 신설하고, 검정 바탕·흰색 글씨(`theme: dark`, `#0b0f19`/`#111827`)의 4계층(`PI 접점` ↔ `Gateway·SQLite WAL·CSO·Policy` ↔ `Research & Meaning Devices 6종` ↔ `Runner·CLI Adapters·HPC`) Mermaid 다이어그램과 계층별 핵심 모듈·책임 표를 반영했습니다.
  - Codex 리뷰 지적(P2 4건)을 반영해 다이어그램 및 표의 CLI 명령(`resume`), SQLite 저장소 경로(`labhq/store.py`·`labhq/request_bundle.py`), 클라이언트 WebSocket 엔드포인트(`/ws/client`)를 실제 코드와 일치시켰습니다.
- **오래된 수치 및 파일 구조 최신화**:
  - `labhq/vocab/output_types.yaml` 및 `topic_checklists.yaml`(`docs/vocabulary.md`)의 현행 기준인 **45개 분야·135항목**, **어휘 125개 key(79개 EDAM 연결)**를 `README.md`, `README.en.md`, `docs/manual.md`, `docs/reference/readme-evidence.md`에 일치시켰습니다.
  - `docs/manual.md`의 `## 파일 구조` 트리에 `supervisor.py`, `store.py`, `durable.py`, `verify.py`, `request_bundle.py`, `reproduction_receipt.py`, `portfolio.py`, `result_storage.py`, `korean_qc.py`, `labhq/research/`, `labhq/evidence/`, `labhq/recruit/paper_skills`, `labhq/skills/` 등 v0.5 모듈 구성을 반영했습니다.

## 테스트 결과
- [x] `pytest -q tests/test_integrations.py tests/test_direct_outputs.py tests/test_nfcore_default_732.py tests/test_pdf_figure_policy.py tests/test_staff_writing_skills.py tests/test_vocab_tables.py`: `115 passed, 2 skipped`
- [x] `python scripts/integrations.py --check` · `python scripts/vocab_tables.py --check` · `python scripts/patch_notes.py check` · `python scripts/notes_index.py --validate`: 통과

## 바꾼 파일
- `docs/manual.md`
- `README.md`
- `README.en.md`
- `docs/reference/readme-evidence.md`
- `AGENTS.md`
- `patch_notes/entries/gemini-manual-architecture.yaml`
- `docs/status/2026-10-20-1336-gemini-manual-architecture.md`

## 막힌 점
- 없음

## PI·Claude에게 물을 것
- 없음

## 확인
- [x] `scripts/check_public.sh` 통과 (비밀값, 실제 설정, 데이터 파일 없음)
- [x] `docs/status/2026-10-20-1336-gemini-manual-architecture.md`에 같은 보고 추가
- [x] ⛔ 체크포인트에 해당하면 PI 확인을 받았음

<!-- author:gemini -->

## 단계
CI 문서 판별·동일 tree 재사용 — Fixes #788, PR #793. 이번 수정: `b357555`.

## 수정
- P1 4321302443: CLI 이름을 추가하는 대신 실행 지시어와 파일·경로·옵션·따옴표 형태의 변경 문장을 full로 판정합니다. 미등록 CLI의 변경·추가·삭제와 fake git의 전체 diff 판정을 검증했습니다.
- 일반 prose·그대로인 미등록 CLI/inline/fenced 예제는 축소 가능 사례를 유지합니다. 지시어·argv 형태가 있는 일반 prose도 full이 되는 보수적 경계는 [정책](../reference/ci-reuse.md)에 명시했습니다.
- 4320728990은 기존 `5d7bd7a` 수정과 stale 근거 답글이 있어 제외했습니다. README 제목 fake git 회귀도 계속 통과합니다.
- workflow는 수정하지 않았습니다. #787의 25분 제한·faulthandler·stall snapshot은 origin/main과 동일하며 전체 gate·checkout/workflow/manifest 증명은 유지합니다.

## 검증
- 새 회귀: 수정 전 **42 failed, 3 passed**. 수정 후 `tests/test_ci_unlisted_commands.py tests/test_ci_pr_policy_p1.py tests/test_ci_skip_policy.py tests/test_text_encoding.py`: **253 passed**.
- Python 3.10 AST·workflow 동일성·integrations·public·patch-note·notes·diff 검사 PASS. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback을 로컬에 보존했습니다.

## 한계
실제 artifact 왕복·native git fixture·전체 suite·process/thread/signal 통합은 실행하지 않았습니다. CI·새 head 리뷰·병합은 controller가 맡습니다.

## encoding·공용 fixture 후속

기존 encoding checker를 바꾸지 않고 `journal.open(mode="rb")`와 native `open_process`로 의미를 명시했습니다. 64 KiB tail bound·ownership/ACK/cleanup·started_here=False는 유지합니다. PR #793의 HTTP binary lint 변경은 가져오거나 덮어쓰지 않았습니다.

승인된 test 원문만 재사용했습니다: `6954ab6aa3ce7314744d754dea16d26e45c0731d`의 recruit/receipt-clock 2파일과 `98f70d96a937e5c6ee8fb650a31d5bf63c0eecbe`의 **second parent 대비 external_wait 1파일 delta**. 세 결과 blob은 원본과 일치합니다. 공용 product 변경·merge commit은 가져오지 않았고 clock/cancellation/progress timeout 단정은 보존했습니다.

후속 검증: 실제 encoding classifier·잘못된 text I/O 반례·bounded-tail fake, receipt-clock fake reconnect/timeout/cancel, external_wait의 mock 3phase와 WMI/runtime mock을 포함해 **110 passed**, guard 3개 유지·blocked 0. 수정 전 실패와 수정 후 전체 출력을 보존했습니다. Python 3.10 AST(embedded CI scripts 포함)·public·diff·patch-notes·notes PASS. Hub/process/WMI 실제 fixture는 로컬 미실행입니다.

reference 3,010→3,702자(LF Unicode), manual 동작 설명은 유지합니다. 실제 Windows survival·Hub 재개·fresh-head CI 증거는 root가 확인해야 합니다. CI 조회·watcher·봇·병합·배포는 하지 않았습니다. root는 이 head 확인 뒤 후속 watcher 하나를 맡습니다.

# PR #800 Python 3.10 CI 멈춤 수정 (external_wait)

## 결론

`pytest (3.10)`이 `tests/test_external_wait.py::test_mcp_timeout_is_an_error_with_mock_board_events[False]`에서 25분 상한까지 멈췄습니다. #794·proc_tree 변경과 무관한 `labhq/tools/external_wait.py` 결함이었고, `1bbb061`에서 고쳤습니다.

## 원인

- CI stall snapshot에 남은 pending task는 `run_lookup.<locals>.progress()` 하나였고, gather callback이 붙어 있었습니다. `finally: watch.cancel(); await gather(watch)`까지 갔는데 progress가 끝나지 않은 것입니다.
- progress는 `record` → `asyncio.wait_for(notify(...))`로 5ms마다 게시합니다. Python 3.10의 `wait_for`는 바깥 취소가 안쪽 완료와 같은 순간에 오면 결과를 돌려주고 취소를 버립니다(3.12는 구현이 바뀌어 없음). 그래서 progress가 계속 돌았습니다.

## 수정

- progress 반복을 cancel뿐 아니라 `stopped` 표시로도 끝냅니다.
- 조회 상한, 시간 초과·취소 기록, 표시 실패 무시는 그대로입니다.

## 검증

- **로컬 (asyncio mock만):** 새 `tests/test_external_wait_cancel.py`는 취소를 한 번 삼키는 가짜 notify로 3.10과 같은 장면을 만듭니다. 수정 전에는 2초 bound에서 TimeoutError(멈춤)였고, 수정 뒤 통과했습니다(5회 반복 모두 통과).
- 기존 `tests/test_external_wait.py`와 함께 21 passed. guard 차단 없음.
- `ast.parse(feature_version=(3, 10))`, `check_public.sh`, `git diff --check` 통과.
- **CI:** 실제 Python 3.10에서의 확인은 CI `pytest (3.10)`에 맡깁니다.

## 남은 것

- 경합이라 수정 전에도 매번 멈추지는 않습니다. 원인은 stall snapshot과 3.10 `wait_for` 동작으로 판단했습니다.
- PR #766이 `tests/test_external_wait.py`를 고치고 있어 그 파일은 건드리지 않고 새 test 파일을 만들었습니다.

## 단계
PR #766 이름 기반 광역 종료 guard — `d83da2b`, Refs #681.

## 수정
- P1 4320847541: `else` 뒤의 literal 실행 명령과 wrapper를 검사합니다.
- P1 4320847550: 공백·줄바꿈을 구분해 literal 변수의 이름 선택·복사·pipeline·InputObject/Id 소비를 연결합니다. 덮어쓰기만으로 선택 상태를 지우지 않습니다.
- P1 4321129633: 중첩·double quote를 구분해 `$()` payload를 검사하고 이름 선택 PID를 외부 종료 명령에 연결합니다. 기존 backquote 거부와 PID·단순 조회·데이터 음성 사례를 유지합니다.
- 4318107934는 공식 parameter 계약의 stale 근거 답글이 이미 있어 제외했습니다. [지적별 근거·경계](../reference/process-stop-681-followup.md).

## 검증
- 원래 head `fea7042` 코드로 최종 새 회귀 52건을 재현: **26 failed, 26 passed**. 수정 뒤 guard 5개 파일과 `tests/test_approval_closed_grammar.py`: **463 passed**.
- policy·flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·notes·diff 검사 PASS. manual은 해당 정책 문단만 갱신했습니다.

## 한계
실제 shell/process/proc_tree·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2와 이전 Hub 통합 검증은 별도 범위입니다. CI·새 head 리뷰·병합·배포는 controller가 맡습니다.

Windows detached launch와 설정/runtime 준비를 분리합니다. `started_here=False`의 실제 health 함수·autospec 회귀, auth/identity·ANY-Job gate, foreground/POSIX를 유지합니다. Fixes #795.

후속: direct를 먼저 시도하고 확인된 WinError 5 또는 Job 잔류에만 소유 handle로 자식 종료·회수를 마친 뒤 transient local WMI를 한 번 시도합니다. base interpreter와 venv carrier로 redirector의 별도 PID/Job을 피합니다. nonce receipt·보유 PID handle의 생성 시각/실행 파일/current-user·모든 Job 비소속 확인 전에는 ACK하지 않습니다. bootstrap은 ACK 뒤 같은 PID로 supervisor에 진입하며 caller 소멸/만료에는 self-abort합니다. unknown·정리 실패·provider 거부에 unsafe retry는 없습니다.

환경 값은 PowerShell stdin/Startup.EnvironmentVariables 메모리로 전달하며 파일·argv·진단에는 쓰지 않습니다. 임시 control은 instance state의 current-user-only DACL과 만료를 갖습니다. site/PYTHONPATH 없이 bootstrap하고 기존 path/prefix를 복구하나 초기 isolated/no-site flags는 유지됩니다. 서비스·Task Scheduler·설치·승격·host 정책 변경은 없습니다.

검증: 로컬 mock/static **146 passed**, guard 3개 유지·blocked 0. direct cleanup/one attempt, WMI codes, nonce/creation/image/owner·timeout·unknown, native identity reader와 DACL, 같은 PID 전환을 fake로 확인했습니다. Python 3.10 AST(embedded CI scripts 포함)·public·diff·patch-notes·notes 검사 PASS. 로컬 실제 process/thread/signal/WMI·PI 상태 접근은 없습니다.

CI-only 증거 pending: nested outer non-breakaway/inner silent-breakaway에서 WMI launch 성공 후 부모 Job들을 닫고 fake supervisor와 child role의 생존·소유 handle cleanup을 요구합니다. 별도 실제 bootstrap unknown/timeout은 ACK/role 진입을 막습니다. 실제 direct 거부+mock provider 거부는 별도이며 survival PASS로 세지 않습니다. [0bf4c97d clean-install 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39363311454/job/118434304160)의 child_in_job=True는 실제 잔류이며 ancestor flags는 unknown입니다. **이번 head의 실제 탈출·clean-install 해결은 아직 미검증**이고 WMI provider도 차단할 수 있습니다. CI·리뷰·병합은 root 담당입니다.

manual 166,295→166,466자, reference 3,029→3,010자(LF Unicode). [근거·제약](docs/reference/windows-runtime-795.md). 배포는 하지 않았습니다. root는 doctor --runtime rc0/runtime_readiness.ready, 실제 WSL, 명시적 idle0·global deploy lock을 별도로 확인해야 합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 단계
CI 문서 판별·동일 tree 재사용 — Fixes #788, PR #793. 이번 수정: `8869961`.

## 한 일
- P1 4320958323: 같은 저장소 이름·ID를 확인한 PR만 manifest를 생성합니다. fork PR의 Ubuntu 두 버전·Windows·설치·validator·MCP gate와 문서 검사는 유지합니다.
- P1 4320958329: 설명 뒤 명령도 변경·추가·삭제하면 full입니다. 기존 제목·명령·inline/fenced 예제를 유지한 prose 변경은 계속 허용합니다.
- P1 4320728990: 기존 `5d7bd7a`가 전체 본문 검사 대신 변경 prose만 검사하도록 고쳤습니다. 기존 README 제목에 문장만 추가하는 원래 사례를 fake git으로 재현해 시작 head에서도 PASS를 확인했습니다.
- #787의 Ubuntu 3.10 25분 제한·faulthandler·stall snapshot은 origin/main과 동일합니다. checkout·workflow·전체 gate 증명과 redirect/auth 보호도 유지합니다.

## 검증
- 새 회귀 검사: 수정 전 **16 failed, 5 passed**. 수정 후 `tests/test_ci_pr_policy_p1.py tests/test_ci_skip_policy.py tests/test_text_encoding.py`: **208 passed**.
- Python 3.10 AST·workflow 동일성·integrations·공개·기록·diff 검사 PASS. mock guard 환경과 전체 실패 로그를 보존했습니다.

## 한계
실제 artifact 왕복·native git fixture·전체 suite·별도 external_wait 문제는 이번 검증 범위 밖입니다. 이전 encoding 수정과 external_wait 근거는 [이전 기록](2026-10-20-1159-ci-reuse-docs.md)에 있습니다. CI·새 head 리뷰·병합은 controller가 맡습니다.

# PR #800 Windows CI 실패 수정 (proc_tree)

## 결론

`pytest-windows`의 `test_the_watcher_stops_a_gateway_that_never_finishes_its_shutdown` 실패는 #794 변경과 무관한 `labhq/proc_tree.py` 판정 결함이었습니다. `6cf6c8a`에서 고쳤습니다.

## 원인

- 테스트 sleeper가 재사용된 PID 752를 받았습니다. 부팅 때 그 PID의 옛 주인이 띄운 system 프로세스 760·856이 아직 752를 부모로 적고 있었습니다.
- `kill_tree`는 terminate가 거부된 후보를, 조회 handle로 나이를 읽을 수 있어도 생성 시각을 보지 않고 `failed`로 기록했습니다.
- 그래서 `spare_ancestors_on_failure`가 root를 일부러 남겼고, watcher는 2를 돌려줬습니다. 엉뚱한 프로세스를 죽이지는 않았지만, 멈춘 gateway를 끝내지 못했습니다.

## 수정

- 거부된 후보 가운데 조회 handle이 있는 것은, 열린 후보와 같은 규칙으로 판정합니다(부모보다 먼저 생긴 프로세스는 PID 재사용 고아). 고아는 건너뛰고 건드리지 않습니다.
- 부모와 같은 시각이나 그 뒤에 생긴 거부 후보, 조회도 안 되는 후보는 지금처럼 실패로 남겨 조상을 살려 둡니다.
- AccessDenied를 무시하거나 신원·자손 검사를 줄이지 않았습니다.

## 검증

- **로컬 (mock만):** 가짜 process table로 새 test 5개를 만들었습니다. CI와 같은 장면(`kill_tree`, 가짜 시계를 쓴 `watch_gateway_exit`)에서 수정 전 3 failed였습니다. 나머지 2개는 정상 차단을 지키는 회귀 test입니다. 수정 뒤 `tests/test_proc_tree.py`·`tests/test_no_tree_kill.py` mock test 64 passed입니다.
  - 기존 실제 프로세스 test `test_the_helper_ends_exactly_a_real_parent_and_child_and_not_a_sibling`은 mock guard가 막아 다시 돌리지 않았습니다.
  - `ast.parse(feature_version=(3, 10))`, `check_public.sh`, `git diff --check` 통과.
- **CI:** 실제 Windows에서 실패한 test는 GitHub CI `pytest-windows`에서 다시 확인해야 합니다. 이 기록을 쓸 때는 결과가 없습니다.

## 남은 것

- CI의 760·856이 정말 부팅 때 생긴 프로세스였는지는 로그에 생성 시각이 없어 확인하지 못했습니다. 실패 장면과 코드 경로가 맞는다는 근거로 판단했습니다.
- Windows + Python 3.10 조합 job은 CI에 없습니다.

Windows detached launch에 BREAKAWAY를 요청하고 자식의 모든 Job 비소속 확인 뒤 PID를 기록합니다. 거부·잔류·unknown은 성공 기록 없이 실패하며 foreground/POSIX·identity guard를 유지합니다. 설정 준비와 runtime 준비를 구분하고 stale/missing/unknown managed daemon을 실패로 판정합니다. Fixes #795.

후속 수정 `45240fb9`: 읽기 전용 health 검증에 필수 `started_here=False`를 전달합니다. 실제 check_health와 autospec으로 정상·타 identity·identity 없음, remote/foreground·WSL을 검증합니다. Job 조회는 pointer/BOOL 규격을 확인하고 native/binding 오류와 잔류를 출력합니다. Windows CI positive는 launch 성공→부모 Job close→child 생존→소유 handle 정리를 요구하며 거부/unknown을 PASS로 받아들이지 않습니다. denied는 별도 test입니다.

검증: 로컬 mock/static **107 passed**, guard 3개 유지·blocked 0. 수정 전 health 5건 TypeError 재현 로그와 수정 뒤 전체 출력을 보존했습니다. Python 3.10 AST(embedded CI script 포함)·public·diff·기록 검사 PASS. 실제 process/thread/signal/Job fixture는 로컬에서 실행하지 않았습니다.

한계: [완료된 clean-install CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39361662285/job/118429060176)는 launch exit 2로 실패했습니다. 이전 출력에는 child membership/native 오류가 없어 원인은 미확정이며 **이 회귀가 해결됐다는 근거는 아직 없습니다**. 실제 생존·새 head CI는 root 확인 대상입니다. 배포 gate는 `doctor --runtime` exit 0 및 `runtime_readiness.ready=true`입니다. PI 상태 변경·배포·봇 호출·병합은 하지 않았습니다.

manual 166,205→166,295자, reference 1,811→3,029자(LF Unicode). [근거·남은 CI 확인](docs/reference/windows-runtime-795.md). workflow·host Job 정책·broker/service 변경은 없습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 단계
PR #766 CI fixture 보정 — `6954ab6`, Refs #681.

## 한 일
- 지정 head의 artifact는 Windows `test_resume_waits_for_active_recruit_receipt[active-True-plan_only]` 실패였습니다. 안내된 limiter fixture는 이미 `98f70d9`에서 수정됐고 이번에도 세 phase가 통과했습니다.
- 원본 resume coroutine의 code를 그대로 실행하되 그 coroutine의 polling 시계만 가상으로 고정했습니다. 중간 상태 확인은 barrier로 동기화합니다. product·기존 assertion·16개 matrix·0.2초 기한·2초 실제 watchdog은 유지합니다.

## 테스트 결과
- `tests/test_resume_receipt_clock.py`와 `tests/test_external_wait.py::test_key_fulltext_automatic_phases_use_task_deadline`: **6 passed**. FakeHub의 재개·기한 만료·취소를 검증했습니다.
- Python 3.10 AST·기존 assertion/matrix/기한 보존 비교·public·patch-note·notes·diff 검사 PASS. guard 환경과 전체 로컬 실패 로그를 보존했습니다.

## 한계
- 원본 Hub fixture의 로컬 실행은 `subprocess.Popen` guard에서 1회 차단됐으며 재실행하지 않았습니다. 전체 Hub/Windows 통합 결과는 미검증입니다.
- 실제 process/proc_tree·PI·연구·HPC와 late-storage/PDF 테스트는 실행하지 않았습니다. 전체 CI·fresh-head 리뷰·병합·배포는 controller가 맡습니다.

Fixes #796
PR #803. CP1 `cab83e7`, 분리 `2070c23`, 공유 fixture `bab83c2`.

- CP1 전 기존 2회 검증에 실제 입력 mismatch 진단·교정·전후 경고를 연결합니다. unknown·예산·승인·중단 경계를 보존합니다.
- mainline input_fit·관계표와 optional Shadow adapter는 같은 aggregate API·의미 hash를 씁니다. off·전체 제거 후에도 CP1 검증을 유지하고 actions-only 제거는 base observer를 보존합니다.
- 기존 semantic_report·bench 기록은 checklist·선언/fit·고정 pack·재사용 근거를 구분합니다. 독립 충족·실제 사용·과학 성능 효과는 증거 없으면 미측정입니다.
- 추가 변경은 테스트 3개 파일뿐입니다. `6954ab6aa3ce7314744d754dea16d26e45c0731d`의 두 파일과 `98f70d96a937e5c6ee8fb650a31d5bf63c0eecbe^2`→해당 merge의 `tests/test_external_wait.py` diff만 그대로 적용했습니다. 각 원본/applied patch SHA-256 일치; merge·제품 변경은 가져오지 않았습니다.
- 관련 mock/static **36 passed**: private clock의 reconnect/timeout/cancel, mock 자동 조회 deadline·취소·timeout progress, CP1의 optional import 차단·메모리 삭제 변환. Python 3.10 AST·public·diff·patch-notes·notes 검사 통과, guard 환경 유지.
- `8d0084cd` 실제 3.10/3.12 로그에는 종전 분리/제거 실패가 없고 각 1건의 timing fixture 실패만 남았습니다. 새 head 통합 CI·리뷰·P1=0 확인은 controller 담당입니다.
- 실제 Hub/process·제거/import 통합·전체 suite·Windows/3.10 실행·PI/연구/bench/원자료/HPC·봇 호출·병합·배포는 로컬 미실행입니다. #797/#798·#804 범위를 추가하지 않았습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 단계
CI 문서 판별·동일 tree 재사용 — Fixes #788, PR #793 DRAFT. 이번 수정: `435af38`.

## 한 일
- PR branch head·synthetic checkout·workflow identity·전체 gate 증명과 README prose 보호를 유지했습니다. #787의 3.10 25분 제한·faulthandler·stall snapshot 단계도 그대로입니다.
- binary 응답을 여는 정확한 stdlib `urllib.request.OpenerDirector.open`만 구분합니다. 일반 `.open`·`read_text`·`write_text`의 encoding 강제는 유지하고 JSON은 UTF-8로 명시해서 decode합니다. redirect/auth·응답 크기 제한은 유지합니다.

## 테스트 결과
- 초기 encoding 검사: 1 failed, 1 passed. 최종 guarded `tests/test_text_encoding.py tests/test_ci_skip_policy.py`: **187 passed**. raw binary·UTF-8 JSON·잘못된 encoding·크기 초과·filesystem 음성 사례 포함.
- Python 3.10 AST·workflow/#787 동일성·공개·기록·diff 검사 PASS. guard 환경과 전체 실패 로그를 보존했습니다.

## 미해결·한계
- 별도 `external_wait[fulltext]` 수정은 이 브랜치에 없습니다. 현재 [fixture](https://github.com/ehojune/bioinfo-team-3d/blob/119106cfc2781589a4f1ab000b438765644c411e/tests/test_external_wait.py#L264-L272)는 실제 80ms/5ms timer와 바깥 1초 기한을 씁니다. CI에서는 내부 timeout 뒤 [progress task 정리](https://github.com/ehojune/bioinfo-team-3d/blob/119106cfc2781589a4f1ab000b438765644c411e/labhq/tools/external_wait.py#L82-L86) 중 바깥 기한이 취소하여 예상 `ToolError` 대신 `TimeoutError`가 났습니다. 정리가 늦어진 세부 원인은 UNVERIFIED이며 별도 담당 확인이 필요합니다.
- 해당 coroutine fixture·실제 process/thread/signal fixture·전체 suite는 실행하지 않았습니다. 전체 CI 수정 완료 주장은 하지 않으며 DRAFT 유지·CI/review/ready/병합 결정은 root가 맡습니다.

PR #802

## 변경

Windows detached 실행은 CREATE_BREAKAWAY_FROM_JOB을 요청하고 소유한 자식 handle의 전체 Job 비소속을 확인한 뒤 PID를 기록합니다. 거부·잔류·unknown은 성공 PID/readiness 없이 실패하며 안전하지 않은 flag 재시도는 없습니다. 호출자 Job membership·immediate limits와 launch 단계를 기록합니다. lifecycle exit 기록 부재만으로 과거 Job 강제 종료 원인을 확정하지 않습니다.

설정 준비와 runtime 준비를 분리하고 `doctor --runtime`을 배포 점검 계약으로 추가합니다. 과거 managed daemon의 stale/missing/unknown 신원은 실패, pristine·정상 down은 별도 상태입니다. remote/foreground는 로컬 PID 없이 인증된 gateway·runner로 확인하며 켜진 WSL runner 연결도 확인합니다. foreground/POSIX·PID identity guard는 유지합니다.

Fixes #795

## 검증·한계

- 로컬: guard 환경을 유지한 mock/static 100 passed, blocked 0. Python 3.10 AST(실제 CI subprocess script 문자열 포함)·public·diff 검사 통과. 실제 process/thread/signal/Job fixture는 실행하지 않았습니다.
- CI 전용 Windows 테스트: 임시 fake daemon·state·소유 kill-on-close Job의 breakaway 허용/거부, Job close 뒤 생존 또는 명시적 실패, PID/readiness 부재와 생성 시각을 확인한 handle의 정리. 상위 CI Job이 허용 경로를 막으면 명시적 실패를 출력하며 이 경우 생존은 미검증입니다. 실제 CI 결과는 아직 확인 전입니다.
- manual 165,668→166,205자. 상세 근거·Microsoft 문서: docs/reference/windows-runtime-795.md. workflow·CI 정책은 변경하지 않았습니다.
- 실제 PI state/daemon/Job·기관·연구·HPC·raw data는 건드리지 않았습니다. CI·fresh-head 리뷰·배포·병합은 root 담당이며 breakaway 거부 시 독립 콘솔이 필요합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 단계
CI 문서 판별·동일 tree 재사용 — Fixes #788, PR #793. 기능 수정: `5d7bd7a`.

## 한 일
- REST run/job의 branch head와 실제 synthetic checkout을 분리했습니다. manifest v2는 PR head/base·run/attempt·checkout의 두 parent/tree·workflow SHA/ref/blob·전체 gate를 묶습니다. 누락·불일치·fork·부분 검증은 full입니다.
- fenced/inline/indented 예제가 그대로면 README prose를 줄입니다. 예제·보호 markup의 추가·삭제·수정과 불명확한 문법은 full이며 문서 validator는 유지합니다.
- 최신 main을 병합했고 #787의 Ubuntu 3.10 25분 제한·faulthandler·stall snapshot 단계가 그대로임을 정적으로 확인했습니다.

## 테스트 결과
- 초기 회귀 재현: 6 failed, 97 passed. 최종 guarded mock/static: **202 passed, 2 deselected**. 실제 REST 형태의 sparse repository·단일 parent branch와 잘못된 identity/gate 사례를 포함합니다.
- Workflow YAML·Python 3.10 AST·#787 동일성, integrations, 공개·기록·diff 검사 PASS. guard 환경 유지, 전체 실패 로그 로컬 보존.

## 한계
- real-git test 파일·subprocess integration 2개·전체 suite·실제 GitHub artifact 왕복은 로컬 미검증입니다. CI·review·병합은 controller가 맡습니다.
- 중첩 block 예제·HTML 내부 수정·새 inline 식별자/link가 있는 생성 Markdown은 full입니다. 실제 절감량은 미측정입니다. [판별·증거 정책](https://github.com/ehojune/bioinfo-team-3d/blob/5d7bd7adbfc63e284426348cffe6252e2eac1d83/docs/reference/ci-reuse.md).

## 단계
개발 감사 우선순위 반영 — 문서만 변경

## 한 일
#794–#799를 #739 체크리스트에 연결하고 HANDOFF의 낡은 큐·7M 한도를 현재 15M 승인으로 맞췄습니다. Shadow는 실측 동안 유지/확장 동결, 효용 판정 뒤 관측 코드 정리입니다. README 한영·AGENTS·관련 manual도 같은 범위로 갱신했습니다.

## 검증
직원 설정 일치·공개 검사·기록 형식·diff 검사 통과. 제품 코드 변경이 없어 로컬 pytest는 실행하지 않았습니다. 최종 head CI·리뷰·P1=0·병합은 별도 gate입니다.

## 한계
P1 수정·현재 PI 가동·새 실운전·장치 효용·bench PASS를 선언하지 않습니다. Job Object 원인은 가설이며 벤치 A/C 수치를 분리했습니다. 연구·HPC·실프로세스 fixture는 실행하지 않았습니다.

문서 글자 수: HANDOFF 4,415→3,888; README 한영·AGENTS·HANDOFF 합계 37,130→37,053. 상세 근거는 docs/reference/audit-priorities-2026-10-20.md에 둡니다.

# 재현 receipt 실행 site-packages (#794, PR #800)

## 결론

PR #800, 기능 커밋 `64f1527`. 재현 receipt 실행(`rerun=True`)의 Python 명령에서 `-S`를 뺐습니다(`-E -s -S -B` → `-E -s -B`). 이제 실행 interpreter(venv·conda)의 site-packages를 불러옵니다. 문법 검사는 `-I -S -B` 그대로입니다. Refs #794.

## 재현 (현재 main `eb09e52`)

PR #789가 `-I`를 `-E -s`로 풀었지만 `-S`를 남겼습니다. `-S`는 `site` 초기화를 건너뛰므로 venv의 site-packages가 `sys.path`에 들어가지 않습니다. 그래서 numpy·pandas처럼 외부 패키지를 쓰는 실제 분석 rerun은 모두 `ModuleNotFoundError`로 `fail`이었습니다. PR #789의 fake importer는 site-packages를 다루지 않아 이 결함을 놓쳤습니다.

## 바꾼 것

- `labhq/reproduction_receipt.py`: 실행 명령을 `-E -s -B <script>`로 바꿨습니다. 그대로 둔 것은 다음과 같습니다.
  - staged sibling import(script 폴더가 `sys.path` 앞에 옴)
  - script별 산출 판정과 hard link·링크 폴더 보호(#771·#781)
  - `PYTHONPATH`·사용자 site·호출한 폴더 격리
  - 문법 검사의 `-I -S -B`
- receipt `command` 값만 바뀌고 schema·상태·요약은 그대로입니다.
- 문서: `docs/manual.md`의 #772 문장 하나를 사실에 맞게 고쳤습니다. 상세는 `docs/reference/reproduction-receipt.md`에 한 줄로 더했습니다.

## 검증: 로컬 (mock/static만)

- fake importer가 `-S`일 때 site-packages를 빼도록 고치고, mock test를 하나 더했습니다. 수정 전 코드에서는 site-packages module import가 `fail`이었고, 기존 #772 test의 명령 기대값도 실패했습니다(5 failed). 수정 뒤에는 #771·#781·#772 test를 포함해 18 passed, 2 skipped입니다.
  - skip 하나는 symlink 권한이 필요한 test입니다.
  - 다른 하나는 아래의 실제 interpreter test입니다. mock guard가 켜져 있으면 프로세스를 띄우기 전에 skip하도록 했고, guard 로그에도 이 test의 기록이 없습니다.
- Python 3.10 문법(`ast.parse(feature_version=(3, 10))`), `scripts/check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 검증: CI (실제 interpreter, PI가 이 test로 한정해 승인)

- 실제 interpreter test는 `test_a_real_isolated_rerun_imports_site_packages_and_siblings_but_not_the_callers_paths` 하나입니다.
- tmp fixture만 쓰고, 현재 interpreter를 receipt의 flag와 최소 환경으로 띄웁니다.
- 확인하는 것:
  - staged `helper.py`를 import하는지
  - site-packages의 `yaml`을 import하는지(그 파일이 `site.getsitepackages()` 아래에 있는지 먼저 확인)
  - 호출한 폴더, `PYTHONPATH`, 사용자 site(`PYTHONUSERBASE`), 다른 step 폴더의 module이 import되지 않고 `sys.path`에도 없는지
  - `PYTHONPATH`와 `PYTHONUSERBASE`를 receipt 입력으로 넘겨 자식 환경에 실제로 들어가게 했는지. 그래서 `-E -s`가 막는 것을 직접 확인합니다.
- CI에서 이 test는 `pytest (3.10)`·`pytest (3.12)`(Ubuntu)와 `pytest-windows`(3.12)에서 돕니다. 이 PR을 열 때 CI 결과는 아직 없습니다.

## 남은 것·한계

- **Windows + Python 3.10 조합은 CI에 없습니다.** 지금 CI의 Windows job은 3.12 하나뿐입니다. workflow는 이 PR의 담당 범위 밖이라 바꾸지 않았습니다.
- **실제 분석 rerun PASS가 아닙니다.** 실제 interpreter 검증은 tmp fixture 하나뿐입니다. numpy 같은 과학 패키지로 실제 분석을 다시 돌린 것이 아닙니다.
- **interpreter 환경의 `.pth` 파일도 처리됩니다.** `-S`를 빼면 site-packages의 `.pth`도 처리되는데, 이는 일반 `python script.py`와 같습니다.
- #773과 다른 감사 항목은 다루지 않았습니다.

PR #790 · Fixes #664

- 표준 runner의 기본·무시 TERM/HUP을 종료 경로에 연결하고 사용자 handler·Ctrl+C·게시·승인 gate를 유지합니다. 결과 ACK를 최대 5초 기다리며 미확인 결과는 durable outbox에 남깁니다.
- P1 4320639315 수정: diagnostics wrapper의 원래 disposition을 구분해 기본 TERM을 graceful 종료로 보내고 진단 기록·사용자 handler·복원을 보존합니다.
- P1 4320639321 수정: durable acceptance 뒤 stopping을 재검사하여 늦은 dispatch를 실행하지 않고 종료 증거가 있는 결과를 기록합니다. 중복 dispatch도 기존 결과를 보존합니다.
- P1 4320639324·4320639329는 stale입니다. 83d5bf89에 기능 행·status가 있고 그 head의 patch-notes 검사가 통과했습니다.
- 검증: 수정 전 후속 회귀 3건 실패·2건 통과, 수정 후 관련 mock/static 24 passed·1 deselected. Python 3.10 문법·public·diff 검사 통과. manual 165,710→165,749자. 근거 docs/reference/runner-signals-664.md.
- 한계: 기존 tests/test_shutdown_recovery.py::test_startup_uses_common_interruption_and_keeps_card는 이전 검증에서 guard가 subprocess.Popen.__init__을 차단해 재실행하지 않았습니다. 실패 전체 로그를 보존했습니다. 실제 신호·POSIX/WSL startup·process/proc_tree·운영·HPC 검증은 없습니다.
- CI·fresh-head 리뷰·병합·배포는 supervisor 담당입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

PR #766, 고침: `c484256`. Refs #681.

- 지정 P1 10건 중 9건을 수정했습니다. coproc·eval·backquote·timeout/nice/setsid·find action·dash·trap·pgrep→xargs kill·ANSI-C literal quote를 검사합니다. 재귀 상한 초과도 거부합니다.
- 4318107934는 원문의 positional Name 전제가 공식 5.1·7.5 계약과 7.5.3 소스에 맞지 않아 stale 근거를 답글로 남깁니다. 명시한 Name이 switch 뒤에 오는 경우는 현재도 deny합니다. [지적별 근거](../reference/process-stop-681-followup.md).
- 현재 head `d4317ec`의 mock baseline에서 **57 failed**를 재현했고, 수정 후 기존 회귀 포함 **368 passed**입니다. policy와 flat/nested fake broker가 카드·event·submit·pending 없이 deny하며 PID·데이터·조회 음성 사례를 유지합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback 로컬 보존.
- Python 3.10 AST·integrations·public·diff·patch-note·notes 검사 PASS. 기록은 저장소 검사에 맞춰 기존 평면 entry에 추가했습니다. 현재 main과 충돌이 없습니다.
- 실제 shell/process/proc_tree·PI·연구·HPC는 미검증입니다. 동적 명령 격리·PID tracker #675와 기존 P2는 범위 밖이며 CI·fresh-head 리뷰·병합·배포는 supervisor 담당입니다.

Fixes #665
PR #791.

새 DAG dispatch가 같은 요청의 성공한 조상 단계에서 수집한 산출 목록·hash를 전달합니다. runner는 요청·step·완료 상태와 hash를 확인한 파일만 `inputs/<step_id>/`로 복사하고, 기존 private/read-root 게이트로 복사본 쓰기와 직접 sibling 읽기를 막습니다. 차단 안내도 `inputs/` 경로를 가리킵니다.

- 링크를 따르지 않는 held handle로 읽고 씁니다. credential·private·비공개 zone·상한 초과·hash 누락/불일치는 입력을 열지 않고 이유를 남깁니다. 재시도는 이전 labhq 복사 목록만 갱신하고 원본과 알 수 없는 기존 입력은 보존합니다.
- 수정 전 mock 재현 9 failed → 관련 mock/static 190 passed, 12 skipped, 24 deselected. 마지막 파일명 판정 변경 뒤 입력 테스트 15 passed를 재확인했습니다. 전체 실패 원문은 작업 로컬 로그에 보존했고 process guard는 유지했습니다.
- 기존 닫힌 shell 문법·read-only task·승인/게시 gate는 유지합니다. 구형 dispatch는 기존 링크 방식을 유지합니다. 새 계약은 입력이 없거나 거부되면 CSO에게 묻게 하며 직접 경로 fallback을 주지 않습니다.
- manual의 단계 입력 절만 갱신했습니다. public·diff·Python 3.10 AST·patch-notes·notes 검사를 적용합니다.
- 실제 process/proc_tree integration·PI 요청·HPC·기관 접근·원자료·연구·게시·설치·배포 미실행. CI·리뷰·병합은 총괄 담당입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 단계
CI 정책 — Fixes #788.

## 한 일
- 예제 없는 prose·schema에 맞는 기록만 문서 검사로 줄입니다. 코드·설정·workflow·test·안전/승인 정책은 full입니다.
- main 재사용은 같은 저장소의 성공한 전체 PR run, 실제 synthetic merge tree, 기존 workflow/helper, 최신 attempt의 모든 gate와 versioned manifest가 맞을 때만 허용합니다. 누락·실패·fork·부분 검증은 full입니다.
- README/문서·기록·공개 검사는 유지합니다. #787을 main에서 합쳐 3.10의 25분 제한·5분 간격 진단을 보존했습니다.

## 테스트 결과
- Guarded mock/static: **131 passed, 2 deselected**. fake git/API/manifest 음성 사례 포함, guard 환경 유지.
- Workflow YAML/AST와 #787 동일성, integrations, 공개 검사, patch-note/기록 검사, diff 검사 PASS.

## 한계
- real-git test 파일과 subprocess integrations 2개는 로컬에서 실행하지 않았습니다. 전체 suite·실제 artifact 왕복·절감 시간은 미검증이며 CI 확인은 controller가 맡습니다.
- 예제·inline code·불명확한 fragment가 있는 문서도 full입니다. 상세 기준: [CI 정책](../reference/ci-reuse.md).

PR #790

## 변경

표준 runner의 TERM/HUP handler가 SIG_DFL·SIG_IGN이면 종료 경로를 설치합니다. stopping 표시→작업 취소·종료 증거→기존 interrupted·재개 카드로 연결합니다. 연결된 gateway의 결과 확인을 최대 5초 기다리고 미확인 결과는 durable outbox에 남깁니다. 사용자 handler·Ctrl+C·게시·승인 gate는 유지합니다.

Fixes #664

## 검증·한계

- 수정 전 기본·무시 TERM/HUP 회귀 4건 실패, 수정 후 mock/static pytest 19 passed, 1 deselected. signal 등록·호출과 gateway 전달은 가짜이며 실제 process·신호·HPC·운영 실행은 없습니다.
- 기존 tests/test_shutdown_recovery.py::test_startup_uses_common_interruption_and_keeps_card는 process guard가 subprocess.Popen.__init__을 차단했습니다. 전체 실패 로그를 보관했고 이 테스트는 재실행하지 않았습니다. 실제 POSIX/WSL startup 통합은 미검증입니다.
- Python 3.10 문법·public·diff 검사 통과. manual 165,483→165,710자. 근거: docs/reference/runner-signals-664.md.
- CI·리뷰·병합·배포는 supervisor 담당입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

Refs #786. 기능 커밋 `06c5a64`는 Ubuntu Python 3.10 pytest의 진단·대기 상한만 추가합니다.

- pytest step을 **25분**으로 제한합니다. 관측된 정상 재실행 13분 47초보다 11분 13초 여유가 있습니다([step timeout 의미](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepstimeout-minutes)).
- `-vv`로 현재 test를 표시하고 pytest 내장 faulthandler는 test가 5분을 넘으면 thread stack을 출력합니다. 별도 watchdog은 5분마다 test/phase·전체 thread·대기 task 최대 50개(stack limit 20)를 출력합니다. capture 전 stderr를 복제하고 종료 시 join은 최대 1초만 기다립니다.
- 다른 matrix/job·test/assertion·권한·설치는 유지합니다. pytest 종료 코드 0–5를 그대로 전달하고 새 의존성은 없습니다.
- 문서 갱신 gate에 맞춰 매뉴얼의 기존 pytest 설명 뒤에 이 기록 링크 한 문장만 추가했습니다. LF 기준 Unicode 문자 수는 165,483→165,572(+89)입니다.
- mock/static **14 passed**: 실제 hook 규격 등록, 합성 coroutine stack, 종료 코드·출력·범위·상한 검증. 실제 thread·signal·process fixture는 미실행입니다. guard 환경 3개 유지·blocked 0건이며 전체 로그를 로컬 artifact에 보존했습니다. YAML·Python 3.10 AST·Bash·integrations·public·records·diff 검사 PASS.

정체의 근본 원인과 실제 Actions timeout/stack 출력은 아직 미검증입니다. #766 limiter fixture 수정과 별도이며 CI·리뷰·병합은 controller 담당입니다.

PR #766, 수정 `98f70d9`. [Python 3.10 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39328092592/job/118352072701)의 전체 traceback을 확인했습니다. 앞선 수정·한계는 [이전 기록](https://github.com/ehojune/bioinfo-team-3d/blob/e0512fd7b9c80bffe68d3ee4bf811e39880467cc/docs/status/2026-10-20-0628-broad-process-stop-681.md)을 따릅니다.

- operation timeout 뒤 progress task 정리 중 외부 1초 제한이 만료됐습니다. 해당 test의 5ms progress override를 제거해 제품 기본 60초 주기를 사용합니다. **80ms deadline·1초 외부 제한은 유지**하고 operation 취소·timeout 알림 assertion을 추가했습니다.
- 완료된 알림의 취소를 놓치는 Python 3.10 경로를 coroutine mock으로 모사해 **1 failed → 3 passed**. 실제 3.10 재실행은 controller 담당입니다. 로컬 Python 3.12에서 `tests/test_external_wait.py` **20 passed**, guard 환경 3개 유지·blocked 0건. 전체 실패명·traceback을 로컬 artifact에 보존했습니다.
- test가 이후 main의 #776에 추가돼 main `e0fa7e4`를 충돌 없이 통합했습니다. 자동 통합 tree와 비교한 직접 수정은 해당 test 하나뿐입니다. 제품 코드는 직접 수정하지 않았으며 late-storage/PDF test는 재실행하지 않았습니다.
- Python 3.10 AST·public·diff·patch-note·notes 검사 PASS.

[#786](https://github.com/ehojune/bioinfo-team-3d/issues/786)의 장기 정체와 같은 원인인지는 **UNVERIFIED**입니다. 실제 subprocess/proc_tree·PI·연구 실행은 미검증이며 CI·리뷰·병합·배포는 controller 담당입니다.

# PI 보고 전 문서 현황 대조

- 결론: HANDOFF의 병합·배포·남은 큐와 일일 상한 근거 링크를 갱신했다.
- 기준: main `e0fa7e48`, PR #776·#777·#778·#781·#782·#783 병합, #766은 CI 수정 단계다.
- README 한·영판과 AGENTS의 기본 역할 13개는 추적 중인 core YAML 13개와 맞는다. 관련 manual의 거부·참고 읽기·외부 조회 상한 설명도 변경 코드와 대조했다.
- 구현·CI·배포와 실제 연구·재현·장치 효과는 구분했다. 새 실운전·bench는 하지 않았다.
- 검증: 공개·직원 표·기록 검사를 적용한다. 제품 코드 변경과 로컬 전체 suite 실행은 없다.

# 재현 receipt sibling import (#772, PR #789)

## 결론

PR #789, 기능 커밋 `f5425c5`. 재현 receipt 실행에서, staged Python entrypoint가 함께 게시한 sibling module(`helper.py`, `lib/util.py`)을 import해도 `fail`로 기록되지 않습니다. Refs #772.

## 재현 (현재 main)

실행 명령이 `python -I -S -B <script>`였습니다. `-I`는 Python 3.11+에서 `-P`를 포함하고, 3.10에서도 script 폴더를 `sys.path`에서 뺍니다. 그래서 같은 폴더의 `helper.py`를 import하는 정상 재현이 `ModuleNotFoundError`로 `fail`이 됐습니다. 새 test 3개가 현재 main에서 모두 `fail`이었습니다.

## 바꾼 것

- Python 실행 명령을 `-E -s -S -B <script>`로 바꿨습니다. `-I`에서 `-P`만 뺀 조합입니다.
  - script의 staged 폴더만 `sys.path` 앞에 옵니다.
  - `-E`로 `PYTHONPATH` 같은 `PYTHON*` 환경을 무시하고, `-s`로 사용자 site를 뺍니다.
  - script를 실행할 때 호출한 폴더는 `sys.path`에 들어가지 않습니다.
- 넘기는 환경은 지금처럼 최소 목록(`PATH` 등과 `PYTHONNOUSERSITE=1`)입니다.
- 문법 검사는 그대로 `-I -S -B -c compile`이고 import하지 않습니다.
- receipt의 `command` 값만 바뀝니다. schema, 상태 이름, 요약은 그대로입니다.
- PR #781의 산출 판정(script별로 이번 실행이 쓴 산출만 셈)과 hard link·링크 폴더 보호는 손대지 않았습니다.
- `docs/manual.md` receipt 단락에 한 문장을 더했습니다.

## 검증 (mock/static만)

- 가짜 `subprocess.run`이 받은 flag와 환경으로 CPython 문서의 `sys.path` 규칙을 흉내 내 import를 풉니다. 실제 interpreter는 띄우지 않았습니다.
  - sibling, 하위 패키지, `steps/<id>/scripts/` 3경우: 수정 전 `fail`, 수정 뒤 `pass`.
  - 격리 test: 호출한 폴더, `PYTHONPATH`, 사용자 site, 다른 step 폴더에만 `helper.py`가 있으면 계속 `fail`입니다. 넘긴 환경에 `PYTHONPATH`·`PYTHONHOME`·`PYTHONSTARTUP`·`PYTHONUSERBASE`·`PYTHONSAFEPATH`가 없는지 확인합니다. 수정 전에는 flag 확인에서 실패했습니다.
  - 문법 검사는 `-I`를 유지하고 실행하지 않는지 확인합니다(회귀 방지).
- #771·#781 test를 포함한 mock receipt test는 17 passed, 1 skipped입니다. skip은 symlink 권한이 필요한 test입니다.
- `scripts/check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것·한계

- **실제 import는 로컬에서 확인하지 못했습니다.** 실제 interpreter의 import 동작은 실행하지 않았습니다. 같은 파일의 기존 실제 interpreter test는 mock guard가 막으므로 CI가 확인합니다.
- **script 폴더 밖 module은 여전히 import되지 않습니다.** script 폴더만 경로에 들어가므로, 다른 step이나 상위 폴더의 module은 import되지 않습니다(일반 `python script.py`와 같음).
- **링크 처리는 Python 규칙을 따릅니다.** 앞 script가 script 파일을 receipt 폴더 밖을 가리키는 링크로 바꾸면, Python 규칙상 링크를 푼 폴더가 `sys.path[0]`이 됩니다. receipt는 sandbox가 아니라는 기존 전제(모듈 docstring) 안의 일이라 새 상태를 만들지 않았습니다.
- #773·#774는 다루지 않았습니다.

PR #766. test 커밋 `71e08b0`으로 [Windows 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39319738372/job/118290456835)를 보완했습니다. 앞선 guard·consult 수정과 남은 범위는 [이전 기록](https://github.com/ehojune/bioinfo-team-3d/blob/24e076a22473352bae72251eb09ae35cb384efad/docs/status/2026-10-20-0527-broad-process-stop-681.md)을 따릅니다.

- `test_late_selection_runs_editor_after_review_then_approval_and_mock_upload`에서 PDF 준비를 Event로 보류해 CI와 같은 `assert publish` 실패를 재현했습니다. PDF 생성·Git 판본 조회를 mock으로 격리한 뒤 **1 failed → 1 passed**, review/status 조합 **4 passed**입니다.
- 기존 검토 전 편집 금지·게시 승인 전 업로드 금지·문서·게시 결과 검증을 유지하고 PDF 호출 1회 검증을 추가했습니다. 제품 코드·timeout은 바꾸지 않았습니다.
- accident guard와 환경 변수 3개 유지, blocked **0건**. 전체 실패명·traceback은 로컬 artifact에 보존했습니다. Python 3.10 AST·public·diff·patch-note·notes 검사 PASS.

실제 PDF 생성·process/proc_tree integration·PI 운영·연구 실행은 미검증입니다. push 후 CI·봇·리뷰 대기와 병합·배포는 supervisor 담당입니다.

PR #776: 내장 외부 API 조회에 단계 첫 조회부터 20분 기한·대기 알림·시간 초과 기록을 추가합니다. Refs #676.

- P1 4317534007 수정: 기관 browser를 async subprocess로 기다리며 취소 시 소유한 handle의 종료를 확인합니다. PDF는 정상 완료·형식 검사 뒤 목적 경로에 반영합니다. 취소된 AlphaGenome SDK thread도 기존 잔류 상한에 집계합니다.
- 이전 P1 4317025129 수정 유지: key_fulltext 자동 조회·기관 간격 대기는 같은 예산을 쓰고 PDF 승인 대기 시간만 제외합니다.
- 수정 전 취소 회귀 4건 실패를 재현했고 수정 후 관련 mock/static pytest 43 passed. guard 환경 유지, blocked 이벤트 0. 실제 process/proc_tree·browser·SDK·기관·운영·HPC·원자료는 실행하지 않았습니다.
- Python 3.10 문법·public·diff·patch-notes·notes 검사 통과. manual 163,348→163,450자. 근거: docs/reference/external-wait-676.md.
- 한계: 원격 SDK 호출 자체의 강제 취소는 보장하지 않으며 잔류 호출을 추적·제한합니다. 직원 직접 스크립트·타사 MCP의 강제 상한이 남아 #676은 유지합니다. CI·fresh-head 리뷰·병합·배포는 supervisor 담당입니다.
- 남은 지적: P2 4317534020(동시 PDF 승인 대기의 예산 갱신)은 범위 밖이며 supervisor의 영향·비용 판단 대상으로 남깁니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

# 재현 receipt 산출 격리 리뷰 P1 (#771, PR #781)

## 결론

P1 3건 가운데 실제 결함 1건은 `4a94324`에서 고쳤습니다. 나머지 2건(패치노트 행, 단계 보고)은 리뷰 대상 `7f7a6bc` 바로 다음 커밋 `7c2d170`에 이미 들어 있어 현재 head에서 재현되지 않습니다.

## 지적별

- 4318264421 (alias 경로의 시각): 앞 script가 선언 산출을 hard link로 만들거나 상위 폴더를 symlink·junction으로 두면, `os.utime`이 receipt 폴더 밖 원본의 시각을 0으로 바꿨습니다. 이제 선언 산출은 다음 세 조건을 모두 만족할 때만 시각을 돌리고 산출로 셉니다.
  - 링크가 아니다.
  - 실제 위치(`resolve`)가 단계 폴더 안이다.
  - `st_nlink == 1`이다.

  alias 산출은 건드리지 않고 `missing_output`으로 둡니다. 고침: `4a94324`.
- 4318264432 (patch-note 행): `patch_notes/entries/codex--receipt-isolation-771.yaml`의 `7f7a6bc` 행이 `7c2d170`에 있습니다. `patch_notes.py check --base origin/main` PASS. `entries/codex/` 아래에 두면 `notes_index.py`가 거부합니다.
- 4318264448 (단계 보고): `docs/status/2026-10-20-0505-codex--receipt-isolation-771.md`가 `7c2d170`에 있습니다.

## 검증 (mock/static만, 가짜 `subprocess.run` 사용)

- 새 test 2개(hard link, 링크 폴더)는 수정 전 head에서 2 failed였습니다. 원본 시각이 0으로 바뀌었습니다. 수정 뒤에는 #771 test와 mock test 12 passed, 1 skipped입니다.
  - 링크 폴더 test는 symlink 권한이 없으면 Windows junction으로 만듭니다.
  - skip된 1개는 symlink 권한이 필요한 끝 경로 링크 test입니다.
- 실제 interpreter를 띄우는 기존 receipt test는 mock guard가 막아 다시 돌리지 않았습니다. CI가 확인합니다.
- `check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것

script가 hard link나 링크로 만든 산출은 이번 실행이 만든 것이어도 `missing_output`입니다. 산출이 receipt 폴더 밖 파일을 가리키므로 보수적으로 세지 않습니다.

Fixes #663

PR #783, 수정 커밋 `4232731`. YAML은 SafeLoader로 파싱한 런타임 문자열별로 검사하고 다른 소스는 전체 텍스트에서 줄바꿈을 포함해 식별자를 찾습니다. 기존 case·separator·NFKC·한국어 조사 경계와 스캔 대상은 유지합니다.

- 합성 문자열로 기존 checker의 **17 failed·6 passed**를 재현했습니다. 최종 `tests/test_bench_no_answer_leak.py`는 **36 passed**입니다.
- folded/literal/quoted YAML·escape·alias·여러 문서와 일반 multiline을 확인했습니다. 별도 scalar·주석·일반 단어는 오인하지 않으며 잘못된 YAML은 실패합니다. YAML 위치는 scalar 선언 줄을 가리킵니다.
- Git 파일 목록만 현재 checkout의 manifest로 대체해 전체 정적 스캔을 수행했습니다. accident guard와 환경 변수 3개 유지, blocked 0건. 전체 실패명·traceback은 로컬 artifact에 보존했습니다.
- Python 3.10 AST·public·diff·patch-note·notes 검사 PASS. test helper 변경이라 manual 갱신은 없습니다. patch-note는 flat entry 한 개에 기록합니다.

실제 bench/trial·연구·process/proc_tree integration은 미실행입니다. CI·리뷰·병합·배포는 watcher 담당입니다.

## 결론

PR #766의 [Windows 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39313316682/job/118268363866)를 test 커밋 `7e08044`로 보완했습니다. 제품 코드는 변경하지 않았습니다. 앞선 process-stop 수정·남은 범위는 [이전 기록](2026-10-20-0441-broad-process-stop-681.md)을 따릅니다.

## 변경·검증

- `test_facilities_ask_without_facilities_goes_to_the_cso_without_waiting`의 마지막 yield에서 중복 2초 timeout이 만료됐습니다. facilities 대기는 즉시 실패시키고 ask 작업의 기존 3초 제한과 답변·라우팅 검증은 유지합니다.
- 가상 시계로 같은 TimeoutError를 재현해 **1 failed → 1 passed**. 관련 facilities mock test는 **4 passed**. Git provenance 조회도 mock으로 격리했습니다.
- 최초 시도에서 Git 메타데이터용 subprocess를 accident guard가 1건 차단했습니다. 실제 실행 없이 경계를 mock으로 바꾼 뒤 추가 차단은 0건입니다. guard와 환경 변수 3개를 유지했고 전체 실패명·traceback은 로컬 artifact에 보존했습니다.
- Python 3.10 AST·diff·공개 검사 PASS. patch-note는 기존 flat entry에 기록합니다.

## 한계

실제 process/proc_tree integration·운영 실행은 하지 않았습니다. push 후 CI 대기·재리뷰·P1=0 판정·병합·배포는 supervisor가 맡습니다.

Fixes #677
PR #782.

검사된 요청 참고 경로를 shell 읽기 판정에 전달하고, 작업 폴더의 PowerShell 디렉터리 생성을 절대·상대경로로 판정합니다. `grep -c`·`wc`·제한된 `od`·직접 파일 hash·`*.zip` 읽기와 신뢰 검사를 통과한 `python --version`을 기존 닫힌 문법에 추가했습니다.

- 수정 전 새 회귀 8 failed → 수정 후 관련 mock/static pytest 173 passed, 12 skipped, 24 deselected. 전체 traceback은 작업 로컬 로그에 보존했습니다.
- 참고 쓰기·credential·통제 구역·read-only task·외부 읽기와 쓰기의 혼합·실행 파일 신뢰 검사·PowerShell 배열/splatting 차단을 유지합니다. 검사 거부·미선택 참고 경로는 읽기 권한을 받지 않습니다.
- Inline Python/heredoc·manifest 내부 경로를 여는 `sha256sum -c`·초기 `cd` 뒤 `;` 연결은 승인 카드로 유지합니다. AST만으로 import 대상·부작용을 증명하는 기능은 추가하지 않았습니다.
- manual의 해당 절만 갱신했습니다(165,919→166,393자). public·diff·Python 3.10 AST·patch-notes·notes 검사를 적용합니다.
- 실제 명령/process integration·PI 요청·기관/HPC·원자료·연구·게시·설치·배포 미실행. CI·리뷰·병합은 총괄이 맡습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

# 재현 receipt script별 산출 격리 (#771, PR #781)

## 결론

PR #781, 기능 커밋 `7f7a6bc`. 재현 receipt에서 같은 단계의 script 여러 개가 한 폴더와 같은 선언 산출을 함께 쓸 때 생기는 false PASS를 막았습니다. 산출을 만들지 않은 script가 앞 script가 남긴 파일 덕분에 `pass`가 되던 문제입니다. 이제 helper 파일 이름 순서와 관계없이 판정이 같습니다. Refs #771.

## 재현 (현재 main)

`labhq/reproduction_receipt.py`는 script를 파일 이름 순서로 같은 `steps/<id>`·`code/<id>` 폴더에서 실행했고, 산출은 파일이 있는지만 봤습니다. 새 test에서 `make.py`가 `result.txt`를 쓰면 다음 결과가 나왔습니다.
- `zz_helper.py`: `pass` (거짓)
- `aa_helper.py`: `missing_output`

## 바꾼 것

- script를 실행하기 전에, 앞 script가 남긴 선언 산출의 수정 시각을 0으로 돌려 둡니다. 실행 뒤에는 모든 선언 산출을 그 실행이 만들거나 다시 썼을 때만 `pass`로 봅니다. 같은 바이트를 같은 시각에 다시 써도 구분됩니다.
- 링크와 폴더는 건드리지도 세지도 않습니다.
- 폴더 공유는 그대로 둡니다. 형제 소스(`lib/util.py`)와 선언하지 않은 중간 파일은 뒤 script가 그대로 읽습니다. 소스만 임시 폴더에 두는 것, 입력을 명시하는 것, 경로 검사는 바꾸지 않았습니다.
- `execution.output_check` 값을 `declared_files_exist_only`에서 `declared_files_written_by_this_run`으로 바꿨습니다. receipt schema와 summary 집계는 그대로입니다.
- `docs/manual.md`의 receipt 단락에 한 문장을 더했습니다.

## 검증 (mock/static만, 가짜 `subprocess.run` 사용)

- 새 test 5개를 만들었고, 수정 전 실행에서 3 failed, 1 passed, 1 skipped였습니다.
  - helper가 앞에 오든 뒤에 오든(`aa_`/`zz_`) 판정이 같은지
  - 두 script가 같은 바이트를 써도 둘 다 `pass`인지
  - 중간 파일, 형제 소스, 폴더 위치가 그대로인지
  - 앞 script가 남긴 링크를 건드리지도 세지도 않는지
- 수정 뒤에는 4 passed, 1 skipped입니다.
- 링크 test는 이 계정이 symlink를 만들 수 없어 skip됐습니다. 링크 경로는 로컬에서 확인하지 못했습니다.
- `scripts/check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과.

## 남은 것·한계

- **기존 receipt test는 로컬에서 확인하지 못했습니다.** 같은 파일의 기존 test 16개는 실제 Python/bash interpreter를 띄우므로 mock guard가 막았습니다. 다시 돌리지 않았고, CI에 맡깁니다.
- **helper 행은 항상 `missing_output`입니다.** 지금은 helper가 앞에 정렬될 때만 그랬는데, 이제 순서와 관계없이 그렇습니다. 그래서 helper가 있는 단계의 요약은 `missing_output`입니다. 어느 script가 entrypoint인지, 어느 산출을 쓰는지는 계획에 정해진 칸이 없어서, 그 계약은 만들지 않았습니다.
- **산출을 나눠 만드는 단계도 `missing_output`입니다.** script마다 "선언한 산출 전부"를 요구하는 기존 규칙은 그대로라, 여러 script가 산출을 나눠 만드는 단계는 각 행이 `missing_output`입니다(지금과 같음).
- **조건부로 쓰는 script는 놓칠 수 있습니다.** 파일이 이미 있으면 쓰지 않는 script는 앞 script 뒤에 실행되면 `missing_output`이 됩니다. 거짓 PASS가 아니라 보수적으로 틀리는 쪽입니다.
- #772–#774는 다루지 않았습니다.

## 결론

PR #766. 기능 커밋 `93d07f8`로 P1 [4317627951](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4317627951)의 xargs literal command 우회를 막습니다. 광역 종료는 승인 카드 없이 거부합니다. Refs #681.

## 변경·검증

- xargs의 short/long 옵션·묶인 flag·붙임/분리 operand·`--`를 넘겨 실제 command를 검사합니다. 선택값 `-e/-i/-l`은 다음 command를 삼키지 않습니다([GNU 옵션 문서](https://www.gnu.org/software/findutils/manual/html_node/find_html/xargs-options.html)). 중첩 wrapper와 shell payload는 기존 검사로 이어집니다.
- head `3bd818c`에 새 회귀를 추가해 **29 failed·19 passed → 최종 관련 mock/static 283 passed**. guard·policy·flat/nested fake broker의 카드 없는 deny와 pending 없음 확인. 명시 PID·기본 echo·조회·출력용 문구·옵션의 파일명/치환 문자열을 보존했습니다.
- accident guard 로드 및 환경 변수 3개 유지 확인, blocked event **0건**. 실패명·traceback 전체를 로컬 test artifact에 보존했습니다. 설치·공용 venv 변경은 없습니다.
- Python 3.10 AST·공개·diff·patch-notes·notes 검사 PASS. manual LF 기준 162,703→162,709자(+6). 중첩 entry를 금지하는 notes gate에 맞춰 기존 flat patch-note 파일을 사용합니다.

## 남은 것

P2 [4317627961](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4317627961)의 here-document 오인은 수정하지 않았습니다. issue 전환은 lead의 영향·비용 선택 뒤 collector가 맡습니다. 실제 shell/process/proc_tree integration·PI 운영·HPC·원자료·분석은 미실행이며 PID tracker #675·동적 명령 OS 격리는 별도 범위입니다. CI 대기·새 head 리뷰·P1=0 판정·병합·배포는 supervisor 담당입니다.

Fixes #682

명시적 모델 계획 거부를 model_refused 사유로 남기고 웹·status에 표시합니다. 자동 재시도·모델 전환 없이 PI가 허용된 다른 CSO 모델 또는 수정 문구를 선택·승인해야 기존 게이트를 거친 새 요청을 만듭니다. 원래 실패는 보존합니다.

- P1 4317267131: 저장된 reference의 source/query_removed 메타데이터가 RequestIn 검증에 섞이던 문제를 고쳤습니다. kind·value·note만 입력으로 재구성한 뒤 기존 Reference·경로·모델·승인 검증을 다시 적용합니다. 원본 reference는 바꾸지 않습니다.
- 신규 6건(사용자·PI 기본·query 제거 reference × 모델 변경·문구 수정)이 수정 전 실패하고 수정 뒤 통과했습니다. 잘못된 URL 거절까지 관련 pytest 46 passed, mock/static만 검증했습니다. 환경 판본의 git subprocess 수집은 fixture로 대체하고 guard 설정을 유지했습니다.
- 최초 검사에서 guard가 git 판본 수집을 차단한 기록과 전체 red/green 출력을 보존했습니다. 해당 수집의 실제 process 경계와 실제 모델·브라우저·운영 효과는 미검증입니다. 이전 CP2 CI 회귀는 Node DOM/static 10건으로 확인한 상태이며 이번에는 재실행하지 않았습니다.
- public·diff·patch-notes·notes 검사 뒤 push합니다. 전체 suite·실제 process/proc_tree integration·운영 요청·원자료·기관/HPC·분석·게시·설치·PI 변경·프로세스 종료·리뷰 요청·병합·배포는 미실행입니다. CI는 push 후 1회 조회하며 기다리지 않습니다.

## 남은 지적

- P2 4317267144(누락된 거부 카드 복구)·4317505805(원래 meta 보존)는 이번 P1 범위에서 수정하지 않았습니다. 후속 선택은 총괄 담당입니다.
- 거부 감지는 명시적 model_refused 또는 API Error 안전장치 서명에 한정합니다. 과학 결과 재현 PASS를 주장하지 않습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

PR #766. 기능 커밋 `67813c8`로 literal 실행 위치·quote removal 검사를 보강했습니다. 배정된 P1 여섯 건을 수정하고, 원문 ForEach 지적 한 건은 기존 `0997a9a`의 stale 근거를 확인했습니다. 광역 종료는 승인 카드 없이 거부합니다. Refs #681.

## 지적별 근거

| 댓글 | 처리 |
|---|---|
| 4317094579 | shell의 결합형 `-lc`와 cmd `/k` payload 검사 |
| 4317094587 | `command -p`·`--` 및 기존 wrapper의 옵션·operand 처리 |
| 4317094594 | 원문은 head `2941c7e`에서 이미 deny. 새 exact 회귀로 확인; 줄바꿈·앞선 문장이 있는 block도 보강 |
| 4317155926 | `if`·`elif`·`while`·`until`·`time`의 실행 위치 검사 |
| 4317155937 | redirection·파일/fd operand를 argv와 구분 |
| 4317155944 | `env -vS`·붙임값·long option split-string 재귀 검사 |
| 4317155962 | quote/escape fragment를 한 word로 읽고 literal quote removal 적용; Windows 경로 판정 유지 |

## 검증

- 기존 head에 새 회귀를 추가해 45 failed·22 passed → 수정 후 전용 문자열/fake 211 passed. 승인 정책의 정적 test 24개를 포함한 최종 실행은 **235 passed**입니다.
- 각 새 실행 사례에서 guard·policy·flat/nested fake broker의 카드 없는 deny와 pending 없음 확인. PID·조회 명령·인용한 출력 문구를 보존했습니다.
- accident guard 로드 확인, `PYTEST_PLUGINS`·`PYTHONPATH`·`LABHQ_MOCK_GUARD_LOG` 유지, blocked event 0건. 실패명·traceback 전체는 로컬 artifact에 보존했습니다. 설치·공용 venv 변경은 없습니다.
- Python 3.10 AST·공개·diff·patch-notes·notes 검사 PASS. manual LF 기준 162,629→162,703자(+74). 중첩 entry를 금지하는 notes gate에 맞춰 기존 flat patch-note 파일을 사용합니다.

## 남은 것

실제 shell/process/proc_tree integration·PI 운영·HPC·원자료·분석은 실행하지 않았습니다. PID tracker #675·동적 명령 OS 격리는 별도 범위입니다. P2는 lead의 영향·비용 선택 뒤 collector가 처리합니다. CI 대기·새 head 리뷰·P1=0 판정·병합·배포는 supervisor 담당입니다.

# storage --rebuild 리뷰 P1 (#754, PR #777)

## 결론

P1 3건 중 실제 결함 1건을 `5b323a3`에서 고쳤습니다. 나머지 2건(패치노트 행, 단계 보고)은 리뷰 대상 `b448227` 다음 커밋 `0745184`에서 이미 들어가 현재 head에서 재현되지 않습니다.

## 지적별

- 4317176241 (case-only 별칭): `--rebuild --name`의 새 저장소가 지금 저장소와 같은지 대소문자를 무시해 비교합니다. 토큰이 있으면 GitHub `full_name`으로 이름을 바꾼 저장소의 옛 이름도 같은 저장소로 봅니다. 새 이름이 404면 새 저장소로 진행하고, 그 밖의 HTTP·연결 실패는 저장 상태를 바꾸지 않고 거부합니다. 고침: `5b323a3`.
- 4317176231 (기능 커밋 patch-note 행): `patch_notes/entries/codex--storage-rebuild-754.yaml`의 `b448227` 행이 `0745184`에 있습니다. `patch_notes.py check --base origin/main --head 0745184` PASS. 지적의 `d367480`은 이 저장소에 없는 커밋입니다. 기능 커밋 단독 범위(`b448227^..b448227`)는 행이 그 뒤 커밋에 있어 실패하며, PR 범위 검사가 기준입니다.
- 4317176235 (단계 보고): `docs/status/2026-10-20-0310-codex--storage-rebuild-754.md`가 `0745184`에 있습니다.

## 검증 (mock/static만)

- 새 test 4개(대소문자 별칭, 이름을 바꾼 저장소, 진짜 새 저장소는 새 이름만 조회, GitHub 오류 시 거부)는 수정 전 코드에서 4 failed, 수정 뒤 `tests/test_storage_rebuild_754.py` 10 passed.
- 같은 파일의 게시 경로 test는 실행 기록이 `git rev-parse`(`integrations.rounds._git_commit`)를 부르므로 그 함수를 stub했습니다.
- mock guard 아래에서 기존 storage test 4개 파일 중 8건이 guard 차단으로 실패했습니다. 차단된 호출은 이 PR이 바꾸지 않은 `rounds._git_commit`과 `runner/versions._run_probe`입니다. 규칙대로 guard 없이 다시 돌리지 않았고, 이 경계는 CI가 확인합니다.
- `check_public.sh`, `git diff --check`, `patch_notes.py check` 통과.

## 남은 것

`--name`으로 지금과 다른 기존 저장소를 고르면 그 저장소 루트에 올라갑니다. 이는 `--to github`와 같은 동작이고 게시 카드가 저장소 이름을 보여 줍니다. P1 범위 밖으로 남깁니다.

PR #776: 내장 외부 API 조회에 단계 첫 조회부터 20분 기한·대기 알림·시간 초과 기록을 추가합니다. Refs #676.

- P1 4317025129 수정: key_fulltext의 공개 본문 조회·기관 요청 간격 대기·fetch도 같은 예산을 씁니다. PDF 승인 대기 시간만 제외하며 이미 쓴 예산은 유지합니다.
- 수정 전 자동 조회 3경로의 기한 우회를 재현했습니다. 수정 후 관련 mock/static pytest 40 passed(27+13), 26 deselected. process accident guard 환경을 유지했고 차단 이벤트는 없었습니다.
- Python 3.10 문법·public·diff·patch-notes·notes 검사 통과. manual 163,275→163,348자. 근거: docs/reference/external-wait-676.md.
- 직원 직접 스크립트·타사 MCP의 강제 상한은 남아 있어 #676은 열어 둡니다. 실제 프로세스·기관·브라우저·운영·HPC·원자료 검증은 실행하지 않았습니다.
- CI·fresh-head 리뷰·병합·배포는 supervisor가 이어갑니다. 남은 지적: 이번 작업의 P2 변경은 없습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

Fixes #682

모델 안전장치가 CSO 계획을 거부한 요청에 `model_refused` 사유와 안전한 한 줄 설명을 남기고 웹·status에 표시합니다. 최초 계획과 계획 교정 모두 같은 실패 경로로 종료하며 자동 재시도·모델 전환은 하지 않습니다.

- 다음 조치 카드에서 PI가 허용된 다른 CSO 모델 또는 수정한 문구를 직접 선택·승인하면 새 요청을 만듭니다. 그대로 종료하면 새 요청 0건입니다. 원래 실패·모델 응답은 기록에 보존하고 표시 사유는 원문을 노출하지 않습니다.
- 새 요청은 기존 create_request 검증·모델 allowlist·요청 예산·연구 승인 계약을 다시 적용하며 이전 승인·게시 상태를 복사하지 않습니다. 인증 실패·잘못된/변경된 모델·취소된 원본은 새 요청을 만들지 않습니다. 카드·응답 기록과 원본 연결로 중복 생성도 막습니다.
- pytest 56 passed, Node DOM/static 18 passed. fixture Hub/ASGI transport·가짜 Adapter/Runner/WebSocket·작은 DOM만 사용했습니다. 전체 suite·실제 process/proc_tree integration·운영 요청·PI 인스턴스·원자료·기관/HPC·분석·게시·설치·프로세스 종료는 미실행입니다.
- manual의 해당 절 한 단락·HANDOFF 큐·status와 패치노트를 갱신합니다. public·diff·patch-notes·notes 검사를 적용합니다. 리뷰·CI 확인·병합·배포는 총괄 담당입니다.

남은 한계: 거부 감지는 명시적 model_refused 또는 제공자의 API Error 안전장치 서명에 한정합니다. 실제 모델 호출·브라우저 실측·운영 효과는 미검증입니다.

## CI 회귀 수정

- 실패: `tests/test_web_3d.py::test_office_in_node[web_cp2_evidence.cjs-fail 0]`. 모델 거부 선택 인자가 추가됐는데 CP2의 2.5D 호출 형태 검사가 예전 인자 목록만 허용했습니다.
- 새 인자를 정확히 확인하도록 갱신하고, CP2 approve/revise/deny에는 모델 재계획 payload가 없다는 DOM 검사를 추가했습니다. 제품 코드와 기존 승인·수정·거부·메모 검사는 유지했습니다.
- 이번 검증은 direct Node DOM/static 10 passed(CP2 7 + 선택 카드 3). 수정 전 동일 Node 검사 실패 1건을 재현했고 전체 CI 로그·red/green 출력·stack을 D 임시 artifact에 보존했습니다. 기존 pytest 56건은 이번에 재실행하지 않았습니다.
- PYTEST_PLUGINS/PYTHONPATH/LABHQ_MOCK_GUARD_LOG 유지. pytest의 subprocess wrapper·실제 process/proc_tree integration·운영 요청은 실행하지 않았습니다. public·diff·patch-notes·notes 검사 뒤 push하며 CI/봇/병합은 기다리지 않습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

Fixes #682

모델 안전장치가 CSO 계획을 거부한 요청에 `model_refused` 사유와 안전한 한 줄 설명을 남기고 웹·status에 표시합니다. 최초 계획과 계획 교정 모두 같은 실패 경로로 종료하며 자동 재시도·모델 전환은 하지 않습니다.

- 다음 조치 카드에서 PI가 허용된 다른 CSO 모델 또는 수정한 문구를 직접 선택·승인하면 새 요청을 만듭니다. 그대로 종료하면 새 요청 0건입니다. 원래 실패·모델 응답은 기록에 보존하고 표시 사유는 원문을 노출하지 않습니다.
- 새 요청은 기존 create_request 검증·모델 allowlist·요청 예산·연구 승인 계약을 다시 적용하며 이전 승인·게시 상태를 복사하지 않습니다. 인증 실패·잘못된/변경된 모델·취소된 원본은 새 요청을 만들지 않습니다. 카드·응답 기록과 원본 연결로 중복 생성도 막습니다.
- pytest 56 passed, Node DOM/static 18 passed. fixture Hub/ASGI transport·가짜 Adapter/Runner/WebSocket·작은 DOM만 사용했습니다. 전체 suite·실제 process/proc_tree integration·운영 요청·PI 인스턴스·원자료·기관/HPC·분석·게시·설치·프로세스 종료는 미실행입니다.
- manual의 해당 절 한 단락·HANDOFF 큐·status와 패치노트를 갱신합니다. public·diff·patch-notes·notes 검사를 적용합니다. 리뷰·CI 확인·병합·배포는 총괄 담당입니다.

남은 한계: 거부 감지는 명시적 model_refused 또는 제공자의 API Error 안전장치 서명에 한정합니다. 실제 모델 호출·브라우저 실측·운영 효과는 미검증입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

# storage --rebuild (#754, PR #777)

## 결론

PR #777. `labhq storage <요청 id> --rebuild [--name <새 저장소>] [--visibility public]`를 넣었습니다. 이미 게시한 요청의 정리본을 지금 편집기로 다시 만들고, 게시 카드에서 승인한 뒤에만 같은 저장소의 새 커밋이나 새 저장소로 올립니다. Fixes #754.

## 바꾼 것

- `ResultStorage.rebuild`, `POST /api/requests/{id}/storage/rebuild`, CLI `--rebuild`를 추가했습니다. `--redraft`·`--republish`·`--to`와 함께 쓸 수 없습니다.
  - `--name`이 없으면 같은 저장소와 branch를 씁니다.
  - `--name`이 있으면 `--to github`와 같은 탐침(`GET /user`, 이름 없는 `POST /user/repos`)을 거친 뒤 새 저장소를 고릅니다. 새 저장소는 기본 비공개이고, 원본 저장소는 바꾸지 않습니다. 지금 저장소와 이름이 같으면 거부합니다.
- 게시 카드: 다시 만든 판은 `auto_publish_results`가 켜져 있어도 카드를 띄웁니다.
- README: 편집자가 README를 고친 뒤 제목 아래에 한 줄을 넣습니다. labhq 판본·commit, 다시 만든 날짜, 원본 저장소 링크입니다. 같은 저장소에 올렸다면 원본 링크 대신 "이전 판은 이 저장소의 커밋 기록에 있습니다"라고 적습니다.
- 이전 판의 mode·repo·branch·report_url은 `result_storage.editions`에 남기고, `result_storage.rebuild`에는 편집기 판본을 기록합니다.
- 거부하는 경우: 게시하지 않은 요청(`--redraft`·`--to github` 안내), 끝나지 않은 요청, 검토 완료 전 요청, 묶음 갱신 중인 요청, `revised_to`가 있는 요청, 끝 저장 위치 질문이 남은 요청, 통제 데이터 요청, 정리·게시가 진행 중인 요청. 거부하면 저장 상태를 바꾸지 않습니다.
- `--redraft`·`--republish`가 게시한 요청을 거부할 때, 그리고 `--to github` 안내에서 `--rebuild`를 알려 줍니다.
- 문서: `docs/manual.md`의 정리본 절에 한 단락을 더했고, #688 단락의 안내를 고쳤습니다.

## 검증 (mock/static만)

- 새 `tests/test_storage_rebuild_754.py` 6개: 같은 저장소에 새 판, `--name`으로 새 저장소, 자동 게시 설정에서도 카드를 띄우는지, 거부 경우와 상태 불변, README 줄 위치, API(401/404/409/200)와 CLI. 편집자·GitHub 업로드·토큰 탐침·편집기 판본(git)은 mock이고, 브라우저는 conftest가 막습니다.
- storage 관련 기존 테스트 24개 파일: 466 passed, 3 skipped. CLI mock 테스트 2개 파일: 10 passed.
- `scripts/check_public.sh` 통과, `git diff --check` 통과.

## 남은 것·한계

- 같은 저장소에 올리면 이전 판에만 있던 파일이 남습니다. 지우는 것은 되돌리기 어려운 작업이라 이번에 넣지 않았고, CLI 출력과 manual에 적었습니다.
- 편집기 commit은 gateway가 처음 읽은 HEAD(`integrations.rounds._git_commit`)입니다. git checkout이 아니면 판본만 적습니다.
- 웹 사무실 버튼은 없고 CLI·API만 있습니다.
- 실제 GitHub 게시, 실제 요청, PI instance는 실행하지 않았습니다.
- 처음에 `tests/test_cli*.py`를 glob으로 묶어 돌렸을 때 5 failed, 59 passed였습니다. 출력에 보인 실패는 `test_cli_adapter.py`의 2건뿐이고, 나머지 3건의 위치는 확인하지 않았습니다. 이 묶음에는 하위 process를 띄우는 adapter·launch test가 섞여 있어, mock-only 정책에 따라 다시 돌리지 않았습니다. storage CLI 경로는 위의 mock 테스트로 확인했습니다.

## 결론

PR #766. 기능 커밋 `6bf40b7`·`0997a9a`로 P1 [4316642063](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4316642063)·[4316642076](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4316642076)의 sudo 옵션·PowerShell script block 우회를 막습니다. 이름 기반 광역 종료는 승인 카드 없이 거부합니다. Refs #681.

## 변경

- sudo 옵션의 operand·붙임값·묶인 short flag·`--`·환경변수를 넘긴 뒤 실제 명령을 검사합니다. 대소문자를 구분해 `-H`와 `-h`를 다르게 처리합니다([upstream parser](https://github.com/sudo-project/sudo/blob/main/src/parse_args.c)).
- 인용 문자열은 통째로 유지하고 `{`·`}`를 명령 경계로 읽어 script block 내부를 검사합니다. block 출력·filter·앞선 문장의 이름 선택 상태도 보존합니다([PowerShell call operator](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_operators?view=powershell-7.5#call-operator-)).
- policy·broker는 같은 guard를 유지합니다. 새 allow 분기는 없으며 명시 PID·숫자 signal·출력용 문구를 보존합니다. manual은 LF 기준 162,588→162,629자(+41)입니다.

## 검증

- 기존 head `516dbdd`에서 새 문자열/fake broker 회귀 31개 실패를 재현했습니다. block 출력·filter 연결 회귀 9개도 각 수정 전 실패를 확인했습니다. 최종 전용 test 144 passed, 승인 정책의 기존 정적 test 24 passed.
- fake broker는 두 우회를 카드·event·submit handler에 보내지 않고 pending 기록도 남기지 않음을 확인합니다.
- 최초 setup은 `markdown_it` 부재로 실패했습니다. 기존 임시 의존성을 읽기 전용 PYTHONPATH로 사용했으며 설치·공용 venv 변경은 없습니다.
- Python 3.10 AST·공개·diff·patch-notes·notes 형식 검사 PASS. patch-note는 중첩 경로를 금지하는 notes gate에 맞춰 기존 `patch_notes/entries/broad-process-stop-681.yaml`에 둡니다.

## 남은 것

검증은 문자열·fake·정적 검사뿐입니다. 실제 shell/process/proc_tree integration·PI 운영·HPC·원자료·분석은 실행하지 않았습니다. 이전 검증 범위 이탈 기록은 [기존 보고](2026-10-19-2341-broad-process-stop-681.md)에 보존합니다. PID tracker #675·동적 명령 OS 격리는 별도 범위입니다. P2 positional PID 배열은 collector가 issue로 추적합니다. push 뒤 CI snapshot만 확인하며 CI 대기·새 head 리뷰·병합·배포는 supervisor 담당입니다.

## 결론

PR #776에서 내장 외부 API 조회에 단계 첫 조회부터 20분 기한과 대기·시간 초과 기록을 추가했습니다. Refs #676.

## 변경·검증

- 일반 labhq_annot API는 재호출·MCP 재시작 뒤에도 기한을 유지하고 작업판에 경과를 알립니다. 시간 초과는 MCP 오류로 남기며 직원은 남은 작업과 누락 보고를 이어 갑니다.
- mock/static 관련 pytest 21 passed, 26 deselected. Python 3.10 문법·diff 검사 통과.
- manual 163,011→163,275자. 상세 근거: `docs/reference/external-wait-676.md`.

## 한계

- 직원 직접 스크립트·타사 MCP는 강제 상한 대상이 아닙니다. 스크립트 timeout 작성 규칙을 추가했고 #676은 열어 둡니다.
- PI PDF·기관·승인 대기와 안전/게시 gate는 유지합니다. 실제 프로세스·외부 서비스·운영·HPC·원자료는 실행하지 않았습니다.
- CI·fresh-head 리뷰·P1 판단·P2 수집·병합·배포는 supervisor가 맡습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

PR #768. 감독 정리 실패 시 PID 기록 보존은 기능 커밋 `4934d50`에 있습니다. P1 [4316951093](https://github.com/ehojune/bioinfo-team-3d/pull/768#discussion_r4316951093)의 patch-note SHA 불일치는 검토 대상 head `7c3f439e`에서 재현되지 않았습니다. 이번 변경은 공개 기록뿐입니다. Fixes #627.

## 근거·검증

- 공개 PR commits는 `4934d50`·`7c3f439e`이며, 기존 patch-note 행은 기능 커밋 `4934d50`과 일치합니다. 댓글의 `deacc2e`는 로컬 revision과 공개 commit API에서 찾지 못했습니다.
- `patch_notes.py check`: `origin/main..7c3f439e`, `4934d50^..7c3f439e`, `7c3f439e^..7c3f439e` 모두 PASS. `notes_index.py --validate` PASS.
- 선택한 mock-only pytest 11 passed. 최초 collection은 `markdown_it` 부재로 실패했고, 기존 임시 의존성을 읽기 전용 PYTHONPATH로 사용해 재실행했습니다. 설치·공용 venv 변경은 없습니다.
- stale 지적으로 분류해 새 실패 test와 기능 수정을 추가하지 않았습니다. 기존 test와 안전 gate는 유지합니다. patch-note는 중첩 디렉터리를 금지하는 notes gate에 맞춰 기존 `patch_notes/entries/supervisor-record-627.yaml`에 둡니다.

## 남은 것

P1 stale 근거를 인라인 답글로 남깁니다. push 뒤 CI snapshot만 확인하고 CI 대기·새 head 리뷰·P1=0 판정·병합·배포는 supervisor에게 넘깁니다. P2는 collector가 issue로 추적합니다. 실제 process/proc_tree integration·PI 운영·HPC·원자료·분석은 실행하지 않았습니다.

AGENTS의 고정 리뷰·수정 1회와 P2 issue 금지, CLAUDE의 반복 리뷰 종료 규칙을 최신 PI 운영 정책에 맞추고 Astra 전체 repo 검토를 bench 비교 앞으로 바로잡았습니다. Refs #760, #739.

- Codex 고정 총괄, 10분 점검·후속, 같은 담당자 재사용, 기본 Codex 2(gpt-6.1-sol)·Opus 1/상한 4·2를 명시했습니다.
- 같은 담당자가 P1을 수정하고 새 head 리뷰를 받아 P1=0까지 반복합니다. P2는 실사용 영향·수정 비용 대비 이득으로 총괄이 선별하며 경미·추정·중복은 원 PR 댓글에 남깁니다. CI·봇 대기는 driver가 맡습니다.
- 한도에서는 새 투입만 멈추고 진행 중 작업을 기다립니다. PI 보고는 한국 시각 00·04·08·12·16·20시이며 중요한 분기만 선택지·권고를 붙여 묻습니다.

- 순서는 공개 소형 dry-run·필수 gate → Astra의 labhq 코드·README·구조 전체 검토 → 동일 요청 bench 비교입니다. 생성 결과 repo blind review는 실행 뒤 별도입니다. HANDOFF 24–25행의 원 PI 승인 링크로 확인합니다.

정책 위치: AGENTS 5–6·13–14행, CLAUDE 6·13행, lookup-only `docs/reference/lead-operations.md`, HANDOFF 24–25행. README 한영판·manual과 HANDOFF 현황·예산 행은 수정하지 않았습니다.

문자 수(LF 정규화 Unicode): AGENTS 1,509→1,828, CLAUDE 1,511→1,394, reference 목차 874→938, 새 운영 기준 0→759, HANDOFF 3,631→3,852. 주요 3문서(README·AGENTS·HANDOFF)는 16,866→17,406자입니다.

검사: public·diff·notes validate·patch_notes 규칙 PASS. 정책 문서 변경으로 test는 실행하지 않았습니다. 미해결: 최종 head의 P1=0 리뷰·CI 확인은 총괄/driver가 이어받습니다.

Fixes #748

문법 검사와 실행 상태를 분리하는 receipt를 정리본·공통 보고서·bench manifest에 연결합니다. 기존 P1 3건은 a369a41로 수정했습니다.

- CI 회귀: 취소된 부분 보고서에 새 재현 검증 절이 붙으면서 기존 전체 문자열 assertion이 실패했습니다. fixture로 동일 실패 1건을 확인했습니다.
- 기존 SVG·부분 보고서 본문과 receipt 절을 합친 전체 문자열을 정확히 비교합니다. 연결된 JSON의 빈 코드/lock 목록·missing lock, 저장된 not_applicable/not_rerun, 원래 cancelled 상태·Result 본문도 검증합니다. 기존 SVG·외부 요청 없음 검사는 유지했습니다.
- 수정 뒤 같은 테스트와 보고서 없는 취소·검토/승인 후 mock 저장 계약까지 pytest 8 passed. 지정 lanes Python·기존 D dependency site를 사용했습니다. public·diff·patch-notes·notes 검사를 적용했습니다.
- 제품 코드·승인 게이트·공개 필터는 변경하지 않았습니다. 전체 suite·실제 process integration·PI 요청/up-down·설치·원자료·HPC·실제 분석·게시는 미실행입니다. push 후 CI 1회 snapshot만 남기고 종료합니다.
- HANDOFF 큐에 이번 회귀와 #748 병합 뒤 같은 담당 #682·Opus #754 순서를 반영했습니다. 확정 한도 7M은 유지합니다.

## 남은 지적

- P2 4315249511(Bash 선행 `-` basename)·4315630188(복수 script 공유 출력)은 이번 회귀 범위에서 수정하지 않았습니다.
- Rscript 실측·실데이터 재실행·수치 동등성은 미검증입니다. fixture PASS는 과학 결과 재현 PASS가 아닙니다.
- CI 대기·새 리뷰 호출·병합·배포는 하지 않으며 총괄 driver가 최종 CI를 확인합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

Fixes #748

재현 receipt를 ReportPackage·정리본·bench manifest에 연결합니다. Python/R/bash 문법과 실행 상태를 분리하고 코드/lock hash·도구·명령·변수 이름(값 제외)을 기록합니다. 기본 not_rerun·입력 누락 blocked_input·도구 없음 unavailable을 유지합니다.

- P1 4315296432: 저장 메서드가 없는 기존 Hub 계약도 benchmark 결과를 유지합니다. 실제 Hub의 save_request는 계속 호출합니다.
- P1 4315630166: terminal 이후 비동기 bundle 완료를 요청의 남은 제한 시간 안에서 기다리고 최신 request로 수집합니다. 완료 전 빈 receipt를 저장하지 않으며 대기 시간 초과는 실패로 남깁니다.
- P1 4315630179: 반환된 rid를 수집용 복사본에 보완합니다. 원래 request와 공개 후보 필터는 유지합니다.
- 신규 회귀 5건이 수정 전 head 5a69d19에서 실패하고 수정 뒤 통과했습니다. 관련 pytest 43 passed(지정 lanes Python·기존 D dependency site), public·diff·patch-notes·notes 검사를 적용했습니다. main은 현재 branch의 조상으로 추가 충돌이 없습니다.
- mock Hub/Runner/Server·작은 manifest/source fixture만 사용했습니다. PI 요청·프로세스 종료·실제 분석·원자료·다운로드·기관·HPC·실제 게시·설치 미실행입니다. CI는 push 후 조회하며 재리뷰·병합·배포는 총괄 driver가 맡습니다.

## 남은 지적

- P2 4315249511: Bash 선행 `-` basename의 option 해석. 이번 P1 수정에서 건드리지 않았습니다.
- P2 4315630188: 복수 script의 공유 예상 출력으로 실행 판정이 순서에 의존할 수 있습니다. 이번 P1 수정에서 건드리지 않았습니다.
- Rscript 실측·실데이터 재실행·수치 동등성 미검증. fixture PASS는 과학 결과 재현 PASS가 아닙니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

Fixes #748

공개용 소스의 문법 검사와 실행 상태를 분리하는 receipt를 추가했습니다. Python compile·R parse·bash -n, 코드/lock hash, 도구 판본·상대 cwd·변수 이름(값 제외)·명령·예상 출력·시각·결과를 기록하고 ReportPackage·정리본·bench manifest에 연결합니다. 기본 not_rerun, 입력 누락 blocked_input, 도구 없음 unavailable을 유지합니다.

- origin/main 4787231을 merge했습니다. HANDOFF·reference 목차·portfolio 충돌 3곳에서 양쪽 내용을 보존하고 receipt와 실행 보고서를 함께 연결했습니다. receipt/bench 동작과 기존 P2는 바꾸지 않았습니다.
- HANDOFF 큐에 #767·#765·#766·#768 OPEN을 반영했습니다. #762·#763·#764의 병합·PI 배포는 총괄 인계 근거이며 실제 연구·게시·Nextflow PASS를 뜻하지 않습니다.
- 10-19 실제 11,376,303/14M, 미사용 2,623,697 → 올림 3M+4M으로 10-20 한도 7M 확정을 HANDOFF/reference에 기록했습니다. README.en 한 문장으로 low-level off와 init preset ab/actions confirm을 구분했습니다. HANDOFF 3,780→3,910자, README.en 19,075→19,161자.
- 이번 검증: subprocess를 금지한 mock/static pytest 2 passed, Python 3.10 AST 4파일 PASS. public·diff·patch-notes·notes 검사를 적용했습니다. 기존 suite와 실제 process integration은 다시 돌리지 않았습니다. 이전 기능 검증 168 passed/2 skipped는 통합 전 결과입니다.
- PI 요청·up/down·원자료·실제 분석·다운로드·HPC·기관 접근·실제 게시·Nextflow·설치 미실행. CLI/storage rebuild는 #754 별도 범위입니다. 최종 push 후 CI는 1회 snapshot만 남기고 총괄 driver에게 넘깁니다.

## 남은 지적

- P2 4315249511: `-`로 시작하는 Bash script basename이 option으로 해석될 수 있습니다. 이번 통합 범위에서 수정하지 않았습니다.
- Rscript 실측·실데이터 재실행·수치 동등성은 미검증입니다. 작은 fixture의 실행 PASS를 과학 결과 재현 PASS로 해석하지 않습니다.
- CI 대기·봇 호출·병합·배포는 총괄 driver가 맡습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론
PR #768. 감독 시작 실패 뒤 정리도 실패하면 PID 기록을 보존하고 오류를 알립니다. 다음 `down`·`up`이 같은 감독을 다시 찾습니다. Fixes #627.

## 변경
- `_await_child_record`는 `_terminate_tree`가 성공했을 때만 감독 PID 파일을 지웁니다.
- 정리 실패 시 기존 시작 실패 이유와 감독 역할·PID·종료 오류·기록 보존 안내를 stderr에 쓰고 exit 2로 끝납니다.
- manual에 실패 후 기록 보존을 설명했습니다. LF 기준 162,210→162,351자(+141).

## 검증
- 새 mock 회귀 6개: gateway·runner × 감독 alive/gone/identity unreadable. 수정 전 6 failed → 수정 후 통과.
- 기존 성공·중복 감독 방지·종료 실패 회귀를 포함해 선택한 mock-only tests 11 passed. PID 기록 원문·종료 호출 인자·stderr·exit 2·기존 감독 재인식을 확인했습니다.
- 실제 process 생성·종료, proc_tree/supervisor integration, PI up/down/status, 원자료·HPC·연구 실행·설치는 하지 않았습니다. 전체 suite는 CI에 맡깁니다.
- Python 3.10 AST·public·diff·patch-notes·notes 형식 검사를 확인했습니다.

## 남은 것
CI·리뷰·병합·배포는 총괄이 이어받습니다. 실제 운영 환경 검증은 하지 않았습니다.

Fixes #748

공개용 소스의 문법 검사와 실제 실행을 구분하는 receipt를 추가했습니다. Python compile·R parse·bash -n, 코드/lock hash, 도구 판본·상대 cwd·변수 이름·명령·예상 출력·시각·결과를 기록합니다. 기본 `not_rerun`, 입력 누락 `blocked_input`, 없는 도구 `unavailable`을 유지합니다.

- 정리본의 `run/reproduction_receipt.json`, 공통 ReportPackage, bench run/score/comparison에 연결했습니다. 실패·도구 없음·미검사를 숨기지 않고 Python 문법 실패의 기존 게시 차단도 유지합니다.
- 허용된 작은 fixture의 명시적 실행은 exit 0 + 선언 출력 존재만 검증합니다. 원자료·다운로드·기관·HPC·분석 재실행·실제 게시를 실행하지 않았습니다. #754의 CLI/storage rebuild는 별도 범위입니다.
- 관련 pytest 168 passed, 2 skipped(중복 제외). Rscript가 없어 실제 R parse 두 경우는 skip했으며 명령·도구 없음 경로는 별도 검증했습니다. public·diff·Python 3.10 AST 검사 통과.
- manual 해당 절·HANDOFF 큐·reference를 갱신했습니다. HANDOFF 3,617→3,780자, manual 161,473→161,875자.

## 남은 지적

- Rscript 실측·실데이터 재실행·수치 동등성 검증은 미실행입니다. fixture의 실행 PASS를 과학 결과 재현 PASS로 해석하지 않습니다.
- CI 조회 후 대기·봇 호출·병합·배포는 총괄 driver가 맡습니다.

## 결론
PR #766. 이름으로 여러 프로세스를 종료하는 명령을 승인 카드 전에 거부하고 자기 작업 폴더의 PID·시작 시각 확인을 안내합니다. Refs #681. 백그라운드 PID tracker(#675)는 남아 있어 부분 해결입니다.

## 변경
- `taskkill /IM`·`//IM`(필터 포함), `pkill`·`killall`, PowerShell 이름·pipeline 종료를 정적으로 검사합니다. 연결·fallback·shell wrapper도 확인합니다.
- policy와 broker에서 같은 guard를 쓰며 거부 시 pending future나 승인 카드를 만들지 않습니다.
- PID 지정 명령과 기존 runner의 PID·시작 시각 검증 cleanup은 유지합니다. manual 162,210→162,588자(LF).

## 검증
- 새 guard 회귀는 명령 문자열·fake broker만 사용합니다.
- 최초 관련 tests 327 passed/6 skipped 중 임시 parent/child fixture integration 1개 실행. PI 운영 검증은 안 했습니다. 실제 프로세스 종료 금지 범위를 넘긴 검증 실수이며 영향은 별도 확인 대상입니다.
- 추가 guard 수정 후 실제 fixture를 제외한 관련 tests: 331 passed, 6 skipped, 1 deselected (`-k 'not real_parent'`). 이후 넓은 tests 재실행 없음.
- 최종 P1 수정: unquoted cmd/PowerShell wrapper의 전체 payload, env 옵션 뒤 명령, 여러 줄 pipeline, InputObject의 이름 기반 producer를 검사합니다. 새 실패 회귀 19개를 재현한 뒤 전용 문자열/fake broker tests 85 passed를 확인했습니다. 기존 숫자 signal·명시 PID·인용한 문구는 유지합니다. 실제 shell·프로세스 종료는 실행하지 않았습니다.
- public·diff 검사를 통과했습니다. patch-notes·notes 형식도 확인했습니다. 전체 suite는 CI에 맡깁니다.

## 남은 것
- PID tracker·정리 도구(#675), 동적으로 조립한 명령의 OS 격리, 다른 엔진의 sandbox 경계는 이번 범위 밖입니다.
- 남은 지적(P2): `Stop-Process 1234,5678`의 positional PID 배열을 이름으로 오인합니다. 단일 PID나 명시 `-Id`를 쓰며 이 후속에서 범위를 넓히지 않았습니다.
- CI·리뷰·병합·배포는 총괄이 이어받습니다. 봇 호출·병합·배포는 하지 않았습니다.

## 결론

PR #765에서 현재 cgroup v2부터 보이는 mount 루트까지의 최소 memory.max를 usable_memory_bytes()에 반영했습니다. Fixes #726.

## 바뀐 것

- membership·mount root·octal escape를 해석하고 mount 경계 안에서 상위 한도를 읽습니다.
- unlimited·누락·잘못된 파일을 건너뛰며 기존 v1·고정 경로 fallback과 Windows 동작을 유지합니다.
- manual 161,473→161,615자. 상세 근거: `docs/reference/cgroup-memory-726.md`.

## 확인·남은 것

- 수정 전 fixture 11 failed·10 passed → 최종 관련 pytest 30 passed. Python 3.10 문법 통과.
- 실제 cgroup 변경·프로세스 종료·HPC·운영 인스턴스 검증은 미실행. 보이지 않는 ancestor는 추정하지 않습니다.
- 전체 suite는 CI에 맡깁니다. CI는 최종 head에서 한 번만 조회하며 봇·병합·배포는 driver가 맡습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

PR #763의 P1 `4314357070`을 수정했습니다. `4314357078`은 기존 head의 패치노트 검사 통과로 stale입니다. Refs #752.

- 상대 workspace는 경로 형태일 때 검사합니다. 일반 문장·URL은 통과하며 실제 workspace 경로는 계속 차단합니다.
- 신규 회귀 7개 실패를 수정했고 관련 pytest 346개 통과·3개 skip을 확인했습니다. 기존 CI 실패 3개도 포함합니다.
- 기존 API·표시 변경에 맞춰 mock과 Node 검사를 갱신했습니다. 공개 검사·패치노트·notes-index 검사 결과는 PR에 기록합니다.
- 새 head의 CI 결과는 PR에서 확인합니다. 재리뷰 요청·병합·운영 실행은 하지 않습니다.

## 결론

PR #764. 정리본과 PDF에 실제 실행된 단계만 그린 분석 파이프라인 모식도와 단계별 실행 타임라인·실행 보고서를 넣고, 단계가 남긴 Nextflow report·timeline·trace·dag를 `run/nextflow/`에 모읍니다. Fixes #756, Refs #760.

## 바뀐 것

- 새 `labhq/run_report.py`: `run/pipeline_diagram.svg`(담당·입력·산출·QC 띠·선행 단계), `run/run_timeline.svg`(시작→종료 막대, 걸린 시간·비용), `run/run_report.md`(표·실행하지 않은 계획 단계·Nextflow 절). 한국어는 SVG `<text>`이고 script·외부 글꼴이 없습니다.
- 값이 없으면 `기록 없음`/`종료 기록 없음`으로 두고 0원·시각을 만들지 않습니다. 비용은 `costs.task_cost_item`의 actual·estimated·unknown을 따릅니다. `run/costs.md`도 요청 비용이 없을 때 `$0.00` 대신 `기록 없음`을 씁니다.
- 내부 자료 단계는 `내부 자료 단계 N`으로만 그리고 입력·산출 이름을 쓰지 않습니다. 판정은 기존 `internal_data_steps` 규칙(`step_uses_internal_data`로 분리)을 그대로 씁니다.
- README·`report/report.md`의 답 아래 `분석 파이프라인` 절에 두 그림·실행 보고서·Nextflow를 링크합니다. 그래픽 초록이 첫 그림이라는 #608 규칙 때문에 Markdown에서는 링크로 두고, PDF에는 두 그림을 넣습니다(RENDER_VERSION 7, 캐시 키에 그림 hash 포함).
- Nextflow: `pipeline_info` 아래 nf-core·`--run_label` 이름까지 인식합니다(예전에는 `report.html`·`timeline.html`·`trace.txt`만, dag 없음). 내부 자료 단계의 기록과 정리 뒤에도 로컬 경로가 남은 기록은 빼고 이유를 적습니다. 정리본 전체는 멈추지 않습니다. 텍스트 1 MB·합계 5 MB 상한 대신 파일당 20 MB 상한을 둡니다. 기록이 없으면 bioinfo-agent 단계는 "사용 여부 미확인", 다른 단계는 "쓰지 않음(산출 기록 기준)"으로 적습니다.
- 실행 경로 확인: labhq는 Nextflow를 직접 실행하지 않습니다. bioinfo-agent의 기본 `config/local.config`가 report·timeline·trace·dag를 모두 켜고 `${outdir}/pipeline_info/`에 씁니다(코드를 읽어 확인). 그래서 `agents/core/bioinfo-agent.yaml`에 네 파일을 `outputs/pipeline_info/`에 복사해 산출로 선언하라는 세 줄을 프롬프트 끝에 더했습니다. #762도 이 파일의 앞쪽 문단을 고치지만 수정 구간이 겹치지 않습니다.
- 문서: manual의 결과 계약·정리본·bioinfo-agent 절, `docs/reference/run-report-756.md`(값 출처·수집 규칙·확인 범위), HANDOFF 큐와 Codex 한도(10-19 14M, 10-20 2M). README 한영판은 정리본 구성을 설명하지 않아 그대로 뒀습니다. LF 정규화 Unicode 글자 수: manual 161,060→161,797, HANDOFF 3,617→3,631, README 11,726·README.en 19,075는 변화 없음.

## 확인

- 새 `tests/test_run_report.py` 9개: 가짜 계획·실행 기록에서 실행한 단계만 포함, SVG `<text>`·script 없음, 타임라인 값·누락, Nextflow 수집·경로 차단·내부 자료 제외·없음 표시, README·report·PDF 연결, 저장된 앞부분에 절 삽입, `edition_assets` 크기 상한.
- 관련 pytest 23개 파일: 661 passed, 2 skipped. 기존 test 4개는 새 그림 수·파일 목록·RENDER_VERSION에 맞춰 기대값을 바꿨고, 두 그림의 이름과 순서, 정리본과 PDF 그림의 동일성을 더 확인합니다.
- `scripts/integrations.py --check`, 공개 저장소 검사, `git diff --check` 통과.
- headless Edge로 두 SVG를 PNG로 그려 글자 넘침이 없는지 확인했습니다. 실제 분석·Nextflow·HPC 실행, 실제 요청의 정리본 게시, 웹 화면 확인은 하지 않았습니다.

## 남은 것

- CI와 리뷰는 총괄이 이어받습니다.
- 실제 bioinfo-agent 실행에서 네 파일이 `outputs/pipeline_info/`에 선언되는지는 다음 실운전에서 확인해야 합니다.
- lane venv에 `markdown-it-py`가 없어 lane tmp에 `--target`으로 설치해 PYTHONPATH로 썼습니다. 공용 venv는 바꾸지 않았습니다.

## 결론

PR #762의 P1 4312241631 수정을 확인하고 새 P1 4314252458을 `2ca1543e`로 고쳤습니다. Fixes #732. Refs #760.

## 바뀐 것

- 승인 전에 공개용 reference 정리를 적용해 소스 전문·fingerprint·대상 repo/base와 업로드를 맞췄습니다.
- 원본·최종 후보의 공개 검사를 유지하고 긴 pipeline 소스에 보고서 길이 제한을 적용하지 않습니다.
- nf-core 우선 배정·건별 승인·CP1/CP2를 유지합니다.

## 확인

- 새 회귀 3개: 수정 전 실패 → 수정 후 통과. 관련 pytest 130개, 별도 source/destination 검증 6개, CP1/CP2 5개 통과.
- Node 상태 회귀·승인 카드 13개·후보 소스 DOM 검증 통과. script/HTML은 문자열로 표시되고 소스 끝까지 펼쳐집니다.
- 승인 전·거절 후 mock GitHub 호출 0회, yes 뒤 PR 1회와 재시작·중복 복구를 확인했습니다.
- 공개 검사·Python 3.10 AST·diff 검사 통과. manual 160,409 → 160,459자.

## 남은 것

- P2 4312241622: init 질문·manual 앞부분·example config의 일회성 동의 문구.
- 새 head CI와 병합은 총괄이 확인합니다. 리뷰 재요청·병합·배포·실제 분석·외부 기여 PR은 실행하지 않았습니다.
- UI는 Node DOM으로 검증했습니다. 실제 브라우저 화면은 UNVERIFIED입니다.

## 결론

PR #763에서 #752의 디스크 점검·init 경로 선택·사용자 지정 workspace 게시 검사·웹 연결 경고를 구현했습니다. Fixes #752.

## 바뀐 것

- doctor: 작업·임시·직원 홈 여유와 설정 가능한 10 GiB/5% 경고.
- init: 작업 경로를 묻고 작은 Windows 시스템 드라이브에 데이터 볼륨 후보 안내. 자동 이동 없음.
- 임의 이름 workspace도 게시 검사에 포함. 기존 웹 상태로 gateway/runner 연결 경고.
- manual 161,060→161,719자. 상세 근거: `docs/reference/disk-preflight-752.md`.

## 확인

- 신규·추가 관련 pytest 41 passed, 기존 관련 묶음 221 passed. 관련 Node 3개 통과.
- 지정 venv의 markdown_it 누락으로 기존 `-I` 패키징 test 1개 실패. 공유 venv·test는 유지.
- 실제 운영·외부 알림·브라우저 시각 QA는 미실행. CI·봇 대기·병합·배포 없음.

## 남은 것

- CI가 전체 suite·패키징 test를 확인해야 합니다. 연결 경고는 열어 둔 웹에서만 보입니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

20시 PI 정기 보고 전 문서를 현재 PR·설정과 맞췄습니다. Refs #760.

- #758·#759 병합, #732 구현 PR #762의 코드 미리보기 P1 보류를 HANDOFF에 반영했습니다.
- AGENTS의 기본 절차를 리뷰 1회·P1 수정 1회·남은 P1은 병합 보류로 맞췄습니다.
- #69 종료 절차를 reference에 보존하고 manual의 깨진 링크를 복구했습니다(지적 4312079758).
- README 한영판은 13개 역할·현재 채널과 일치해 그대로 뒀습니다.
- 주요 3문서: 이번 16,962→16,852자, 최초 28,310→16,852자(LF 정규화 Unicode).

새 실운전·bench·HPC·PI 배포는 하지 않았습니다. #756은 다음 승인 예산 뒤입니다. 검증 결과와 길이는 docs/reference/docs-refresh-2026-10-19.md에 있습니다.

확인: 공개 검사·notes-index validate·diff check 통과. 새 head의 CI는 세션 밖 병합 단계에 맡깁니다.

## 결론

표준 분석은 지원 nf-core를 쓰는 bioinfo-agent에 우선 배정하고 새 pipeline 기여에는 PI의 건별 yes를 받습니다. Fixes #732. Refs #760.

## 바뀐 것

- 간단한 수집·가공·조회는 적합한 직원, 지원 밖 복잡한 새 구현은 engineer/analyst가 맡습니다. 다른 배정의 이유를 계획에 남깁니다.
- 명시적 manifest가 있는 완성 후보만 전달합니다. 승인 카드는 후보 코드·대상 저장소·base에 묶이고 중복 결과와 재시작 뒤에도 결정을 보존합니다.
- 승인 전·거절 후 GitHub 호출은 0회입니다. yes 뒤 기존 공개·비밀값·자료 검사를 거쳐 PR을 1회 만들거나 기존 PR을 복구합니다.

## 확인

- 관련 pytest 398개 통과(중복 제외). 새 회귀 14개는 수정 전 모두 실패했습니다.
- Node 상태 회귀와 승인 카드 17개, 공개 저장소 검사, Python 3.10 AST 11개, diff 검사 통과.

## 남은 것

- CI·자동 리뷰와 병합은 총괄이 이어받습니다.
- 실제 nf-core 분석·bioinfo-agent 기여 PR·배포는 실행하지 않았습니다. 브라우저 화면 확인은 UNVERIFIED입니다.
- README/manual 갱신은 별도 문서 작업 범위입니다.

## 결론

PR #759의 CI 실패 4건을 수정하고 main의 편집기 보정을 합쳤습니다. Refs #757.

## 바뀐 것

- 호환용 `report.md`도 내부 claim을 독자용 근거 번호로 변환합니다. 게시 검사와 승인 게이트를 유지합니다.
- binary 읽기를 기존 인코딩 검사가 판별하는 호출로 맞췄습니다.
- main의 PDF 각주·질문 정리와 CommonMark 코드 영역 보호를 함께 보존했습니다.

## 확인

- 기존 실패: 수정 전 4 failed. 추가 회귀: 수정 전 1 failed, 2 passed.
- 수정 후 관련 pytest 178 passed, main 통합 후 200 passed.
- 공개 저장소 검사와 `git diff --check` 통과.

## 남은 것

- 최종 head의 CI 결과는 PR에 기록합니다. 리뷰 요청과 병합은 총괄이 이어받습니다.

README의 11명 표기와 오래된 HANDOFF를 현재 13개 역할(Claude 8·Codex 5), Codex 고정 총괄, PI 창구 #760에 맞췄습니다. 큰 변경과 매 PI 보고 전 문서 일치 점검을 AGENTS/HANDOFF에 명시했습니다. Refs #760.

- 최신 PI 결정: 10월 19일 9M·20일 2M, #732 배정·기여 정책 승인 반영(코드·검증 진행 중). 제품 CSO·직원 설정은 그대로입니다.
- 네 카드·이미지를 유지하고 긴 인용·비교 수치를 reference로 옮겼습니다. 주요 3문서는 28,310→16,962자(LF 정규화 Unicode), 영문 README는 23,311→19,075자입니다.
- 옛 큐·결정은 조회용 보존본으로 옮기고 현재 PR 순서와 장치 실운전→bench 준비 게이트→#739를 적었습니다. 공개 소형 실운전은 아직 미실행입니다. #758은 18:03 KST Linux CI 2개 PASS·Windows 대기·리뷰 P1 없음입니다.

확인: `tests/test_integrations.py` 기존 33 passed, integrations/vocab 생성기, notes_index validate, patch_notes 규칙, 공개 저장소 검사, diff check 통과. 로컬 파일 링크 142개도 모두 존재합니다. 전후 글자 수와 근거는 `docs/reference/docs-refresh-2026-10-19.md`에 있습니다.

## 결론

PR #759의 지정 리뷰 P1 `4311527815`와 P2 `4311527822`를 고쳤습니다. Refs #757.

## 바뀐 것

- 현재 최종 응답을 `outputs/RESULT.md`에 저장한 뒤 검사해 첫 실행 누락과 재시도 시 이전 응답 검사를 막습니다.
- Markdown 산출 경고는 전체 20개와 초과 안내 한 줄로 제한합니다. 표시 위치 수집도 같은 상한을 씁니다.

## 확인

- 새 회귀 6개: 수정 전 6 failed → 수정 후 통과.
- 관련 pytest: 128 passed, 3 skipped. 여러 파일에 걸친 상한 검사: 3 passed.
- 매뉴얼: 160,299자 → 160,367자.

## 남은 지적

- 최종 push 뒤 CI 결과를 확인합니다. 리뷰 요청과 병합은 총괄이 이어받습니다.

## 결론

PR #759에서 PI 화면에 남은 Markdown 강조·내부 기호를 위치와 수정 안내로 알립니다. Fixes #757.

## 바뀐 것

- CommonMark 파서 하나로 코드 영역을 나눠 asset·근거 수집과 claim 번호 치환에서 건너뜁니다.
- README·report.md·RESULT.md 산출물과 웹 보고를 렌더 검사합니다. 산출 검사 결과는 종료 계약의 FAIL로 전달하고 GitHub 정리본은 게시 전에 차단합니다.
- 직원 공통 강조 지시와 결과 절 하위 audio 허용을 반영했습니다.

## 확인

- main 원본 모듈: 새 회귀 19 failed / 1 passed → 수정 후 통과.
- 관련 pytest: 266 passed / 2 skipped. 최종 Markdown 검사: 56 passed.
- 공개 저장소 검사와 `git diff --check` 통과.
- 매뉴얼: 160,046자 → 160,299자.

## 남은 지적

- 최종 push 뒤 CI 상태를 확인합니다. 리뷰 요청과 병합은 총괄이 이어받습니다.

## 결론

v2 수동 보정 5개와 질문 줄 정제를 편집기에 반영했습니다. 내부 표는 집계표 선언 없이 게시하지 않습니다. Fixes #755.

## 바뀐 것

- PDF claim footnote를 번호형 끝주로 표시합니다.
- 예전 계획은 공개 자료 표만 자동 복구하고 내부 자료 표는 집계표 선언을 요구합니다.
- 입력 README의 이용 조건을 복구합니다.
- README·PDF 질문 줄에서 운영 지시를 빼고 연속 그림 배치를 묶습니다.

## 확인

- 내부 표 게시 회귀 5건을 재현했고 관련 pytest 45 passed입니다. 원래 보정의 관련 pytest는 90 passed였습니다.
- 공개 검사(WSL·LF 사본), 패치노트·기록 검사, `git diff --check` 통과. CI는 push 뒤 확인합니다.
- 이번 수정의 `docs/manual.md`: 161,511자 → 161,541자(+30).

## 남은 것

- CI·보안 수정 리뷰 확인 뒤 병합합니다. 기존 PDF 본문 첫 화면의 질문 갱신 지적(P2)은 남깁니다. #757의 나머지는 PR #759입니다.

## 결론

PR #753의 지정 리뷰 P1 `4310407060`과 P2 `4310407064`를 고쳤습니다. Refs #747.

## 바뀐 것

- claim·asset·음성 위치 검사가 같은 Markdown 코드 마스킹 함수를 씁니다. 코드 예시만 있으면 필수 근거 검사가 실패합니다.
- Excel `.xlsx`·`.xls` 링크를 대소문자 구분 없이 결과표로 분류합니다. 없는 파일은 계속 차단합니다.

## 확인

- 새 회귀 8개: 수정 전 8 failed → 수정 후 통과.
- 관련 pytest: 149 passed, 2 skipped.
- 공개 저장소 검사와 `git diff --check` 통과.
- 매뉴얼: 159,439자 → 159,488자.

## 남은 것

- 최종 push 뒤 CI 결과를 확인합니다. 리뷰 요청과 병합은 총괄이 이어받습니다.

## 결론

PR #753에서 리포팅 공통 결과 구조와 종료 검사를 통합했습니다. Fixes #747.

## 바뀐 것

- 원본·요청 묶음·GitHub 정리본·PDF·웹이 같은 여덟 항목을 같은 순서로 표시합니다.
- 공통 검사기가 누락 파일·생략 사유·내부 claim 표기·음성 결과 위치를 확인합니다.
- 연구·일반·실패 요청의 필수 항목을 구분했습니다.
- 웹 보고서를 안전한 Markdown HTML로 표시합니다.

## 확인

- 관련 pytest: 426 passed, 2 skipped.
- 웹 보고서 Node test: 3 passed.
- 새 검사는 main에서 실패하고 이 브랜치에서 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 무출력이라 중단했습니다.
- `docs/manual.md`: 160,345자 → 160,840자.

## 남은 것

- CI 확인 뒤 봇 리뷰와 병합은 개발 총괄이 이어받습니다.

## 결론

semantics와 그림자 요청의 꺼짐·미평가·오류·정상 평가를 분리했습니다. Fixes #559.

## 바뀐 것

- 정상 평가의 후보 0건만 `정상인데 후보 0건`으로 기록합니다.
- latched·꺼짐은 `미평가(사유)`, 실패·시간 초과는 `오류(code)`로 남깁니다.
- CP1·CP2 카드와 `semantic_report`가 같은 상태를 표시합니다.
- 미평가·오류는 A/B 분모에서 빼고 각각 셉니다.

## 확인

- 새 회귀는 수정 전 10 failed, 수정 후 핵심 328 passed입니다.
- 관련 pytest 413 passed, CP1 subprocess 2 passed입니다.
- `py_compile`과 `git diff --check`는 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 무출력이라 중단했습니다.
- `docs/manual.md`: 280,933 bytes → 280,932 bytes.

## 남은 것

- CI 확인은 push 뒤 진행합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #750에 기관 전문 접근과 PI PDF 요청 경로를 구현했습니다. Fixes #745. Fixes #746.

## 바뀐 것

- 설정으로 켠 기관 profile·proxy를 DOI 한 편씩 시도하며 TDM·외부 모델 허용, 간격, 하루 상한을 확인합니다.
- 실패 논문을 한 카드로 묶고 카드 첨부나 요청 안 inbox의 PDF를 읽습니다.
- 2시간 뒤에는 초록·다른 OA로 진행하고 접근 실패 원장과 보고서 한계에 남깁니다.

## 확인

- 새 test는 main에서 import 실패, branch에서 관련 pytest 107 passed입니다.
- 경계 pytest 277 passed·5 skipped, Node 64 passed, `git diff --check` 통과입니다.
- `scripts/check_public.sh`는 Windows Bash에서 30초 동안 출력 없이 멈춰 종료했습니다.
- 첫 CI의 slow job은 일반 MCP timeout까지 7320초로 늘어난 문제를 찾아, 문헌 MCP에만 긴 대기를 적용하도록 고쳤습니다.
- 첫 일반 CI log에서 도구 목록 기대값과 binary `open` 정적 검사도 고쳤습니다.
- Windows CI에서 종료 권한 없는 시스템 `conhost.exe`를 사용자 후손 실패와 구분했고, 관련 pytest 115 passed·2 skipped입니다.

## 남은 것

- 수정 head의 GitHub CI 확인이 남았습니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

이 PR은 OpenAlex·Crossref·Unpaywall 문헌 경로와 접근 실패 원장을 추가합니다. Fixes #744.

## 바뀐 것

- `labhq_annot`이 OpenAlex 후보·인용 연결, Crossref 판본·license·철회, Unpaywall 합법 OA 위치를 기록합니다.
- Europe PMC 본문이 없으면 합법 OA PDF·HTML을 `.tmp/literature/`에서만 추출하고, 실패 사유 네 가지를 남깁니다.
- runner가 후보·선정·확보·결론 근거 분모와 못 읽은 핵심 논문을 CP2 카드·보고서 한계에 기록합니다.
- lit_scout는 OA 여부로 후보를 미리 거르지 않고 PubMed 검색을 OpenAlex로 넓힙니다.

## 확인

- 새 회귀는 구현 전 collection error, 구현 뒤 49 passed입니다. 관련 pytest는 166 passed·11 skipped입니다.
- OpenAlex·Crossref 실제 응답 모양을 공개 식별자로 각 1회 확인했습니다.
- integrations·`git diff --check`와 공개 금지 문자열·크기·데이터 검사는 통과했습니다.
- `check_public.sh`는 Windows Git Bash에서 60초 무출력이라 중단했습니다.
- `docs/manual.md`: 158,985자 → 160,256자(+1,271자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #743을 최신 `origin/main`과 합치고 자동 리뷰 지적 8건을 고쳤습니다. Fixes #737.

## 바뀐 것

- 보고서 문자열을 공용 게시 정화 경로로 보내 Authorization credential과 모든 절대경로를 가립니다.
- 현재 요청의 최신 shadow 관측만 판정하고, worker 완료 뒤 관측을 요청 snapshot에 고정합니다.
- 연구 계약 요청에만 생성하며 실행 당시 checklist 판본을 보존합니다.
- CP2 카드에서 실제 `semantic_report.md`를 열고 내용을 펼칠 수 있습니다.
- #742의 오류 형식(`code·device·stage·at·안전한 설명`)을 쓰며 예외 문구는 싣지 않습니다.

## 확인

- 최신 main 병합 뒤 충돌 영향 95 passed, 관련 pytest 673 passed·2 skipped입니다.
- CI의 시간대 의존 시각 assertion은 보고서 `generated_at` 기준으로 바꿔 1 passed입니다.
- 웹 회귀는 CP2 링크 9 passed, #742 카드 17 passed입니다.
- `git diff --check`와 공개 금지 항목 수동 검사는 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 90초 무출력이라 중단했습니다.
- `docs/manual.md`: 최신 main 158,699자 → branch 158,984자(+285자, LF 정규화).

## 남은 것

- 지적 8곳 답글은 마쳤습니다. 수정 head의 CI 확인만 남았고 리뷰 재요청과 병합은 하지 않습니다.

## 결론

PR #741을 #740이 포함된 `origin/main`과 합쳐 정리본의 콘텐츠·재현·PDF 규칙을 함께 보존했습니다. Refs #736.

## 바뀐 것

- 단계 스크립트는 원래 `outputs/` 구조와 실행 폴더를 보존하고 재현 안내에 폴더별 명령을 적습니다.
- 서로 다른 개인 경로는 고유한 `LABHQ_INPUT_*`로 바꿉니다. f-string·raw·연결 문자열도 처리하고, 나머지는 경로를 지운 뒤 수동 확인 주석을 붙입니다.
- 새 저장소는 대표 경로만, 기존 저장소는 `labhq/results/<요청 id>/` 호환 경로를 유지합니다.
- PDF 표지에 보고서 제목 전체를 쓰고 그림·설명과 graphical abstract의 지면 규칙을 조정했습니다.
- README 첫 화면·표·claim 각주·수정 이력·갈래 근거 문서는 #740 규칙을 따릅니다.

## 확인

- 이번 P1 회귀 3건은 직전 head에서 3 failed, 수정 뒤 통과했습니다.
- 관련 pytest 188 passed, 게시 Python `py_compile`과 `git diff --check` 통과.
- 실제 Edge PDF 2쪽에서 표지 제목·그래픽 초록·페이지 밀도를 확인했습니다.
- merge 뒤 관련 pytest 286 passed, `git diff --check` 통과.
- `scripts/check_public.sh`는 90초 무출력로 중단해 완료 여부를 확인하지 못했습니다.
- `docs/manual.md`: main 159,391자·branch 159,808자 → merge 159,634자.

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 하지 않습니다.

## 결론

의미 장치 중단·오류를 `제안 0건`으로 숨기지 않고 요청 카드와 기록에 표시합니다. Fixes #738.

## 점검 결과

| 장치 | 경로 | 전에는 | 지금은 |
|---|---|---|---|
| semantics 시작 상태 | CP1 전 점검 | 설정만 보고 실제 중단·epoch·마지막 기록을 놓침 | 상태·epoch·마지막 기록과 복구 명령을 CP1·doctor에 표시 |
| 자동 중단 | breaker·`auto_off` | 로컬 파일·로그에만 남음 | CP1·CP2·웹·status·doctor·실행 기록에 같은 사유·시각·영향을 기록 |
| advisory snapshot | 계획 전 복사 | 예외를 `None`으로 삼켜 제안 0건처럼 보임 | 요청별 `error`로 남기고 다음 요청에서 재시도 |
| advisory projection | 후보 계산 | 예외를 빈 목록으로 삼킴 | 요청별 `error`로 남기며 정보 경계 위반은 계속 중단 |
| 단어장·EDAM·체크리스트·pack | 계획·부록 | 일부 실패가 기능 off나 빈 부록으로 축약됨 | hash·개수 또는 오류·영향을 카드와 실행 기록에 남김 |
| 오류 로그 | 장치 load·projection | 예외 본문과 traceback이 남을 수 있음 | 고정 code와 예외 class만 기록 |
| 웹 재연결 | gateway snapshot | 오래된 장치 event의 중단·경고가 사라짐 | 요청 snapshot에서 같은 상태를 복원 |
| 계획 뒤 재점검 | CP1·최종 기록 | 일시 복구되면 계획에 영향을 준 load 오류가 사라짐 | 최초 load 오류를 끝까지 보존 |

## 확인

- 마지막 리뷰 회귀 3건: 직전 head 기준 각각 failed, 수정 뒤 모두 passed.
- 관련 pytest 89 passed, 웹 카드 17 passed.
- `check_public.sh`, patch-notes, notes-index 통과.
- `docs/manual.md`: 159,873자 → 160,037자(+164자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #740에서 정리본 저장소의 표·첫 화면·보고서 근거·갈래 안내·자료 이용 조건을 보강했습니다. Refs #736.

## 바뀐 것

- 공개 표를 루트·갈래 `tables/`에 두고, 본문이 링크한 표의 존재와 열·행 shape를 게시 전에 검사합니다.
- README 첫 화면 순서를 고정하고, 뺀 표는 이름·사유·행 수·주요 열로 설명합니다.
- 보고서 claim anchor를 근거 각주로 바꾸고 수정 이력은 `run/`으로 옮깁니다.
- 갈래 연결표와 `docs/reference/`를 만들고, 자료별 출처·접근·재배포·인용을 채웁니다.
- 늦은 저장 전환 뒤에도 README의 진행·재현·참고문헌 링크를 유지하고, 정규화 결과가 같은 claim anchor도 서로 다른 근거 위치를 가리킵니다.

## 확인

- 새 회귀 test는 수정 전 3 failed, 수정 뒤 3 passed입니다.
- CI run `39098307497`에서 찾은 README 링크 회귀와 P2 anchor 충돌 회귀를 고쳤습니다.
- late storage·edition·portfolio·result storage 관련 pytest는 214 passed입니다.
- `git diff --check`와 `scripts/check_public.sh`는 통과했습니다.
- `docs/manual.md`: 159,597자 → 159,391자(-206자).

## 남은 것

- push 뒤 GitHub CI 결과를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #735에서 CP2 결론 근거 논문의 읽은 수준을 논문 식별자 별칭까지 맞춰 셉니다. Fixes #697.

## 바뀐 것

- evidence의 `uri`·`locator`와 구조화된 `id_scheme/id_value`에서 PMID·PMCID·DOI를 읽습니다.
- 한 fulltext 응답의 세 식별자를 같은 논문의 별칭으로 보존하고, 하나라도 겹치면 접근 수준을 맞춥니다.
- 본문·초록·서지만·조회 기록 없음으로 세어 CP2 카드와 보고서 `## 한계`에 넣습니다.
- 이 분모가 기존 `fulltext로 조회한 논문 전체` 집계와 다르다고 함께 밝힙니다.

## 확인

- 별칭 회귀 2건은 head `89a7fbb4`에서 2 failed, 수정 뒤 통과했습니다.
- 관련 pytest는 227 passed입니다.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 159,566자 → 159,597자(+31자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 재요청과 병합은 하지 않습니다.

## 결론

PR #734의 리뷰 지적 여섯 건을 turn 집계 규칙 하나로 고쳤습니다. Fixes #701.

## 바뀐 것

- 각 task id의 도구 호출 수를 한 번만 합쳐 성공·실패·교정·slot repair·revision·중단 뒤 재개를 보존합니다.
- id 없는 실패는 다른 도구에 붙이지 않고 `도구 미상 오류`로 셉니다.
- 실행 기록에는 전처럼 단계·직원·도구 이름·호출 수·실패 수만 두며 입력값은 남기지 않습니다.

## 확인

- 지적별 회귀 6건은 직전 head에서 모두 실패했고 수정 뒤 모두 통과했습니다.
- 관련 pytest: 409 passed, 2 skipped.
- `docs/manual.md`: 158,783자 → 158,822자(+39자).

## 남은 것

- push 뒤 CI 결과 확인이 남았습니다. 리뷰 재요청과 병합은 총괄이 이어갑니다.

## 결론

PR #733에서 Europe PMC 양방향 인용 추적과 bioRxiv·medRxiv preprint 검색을 추가했습니다. Fixes #720.

## 바뀐 것

- `citation_chase`가 PMID·PMCID·DOI 한 편의 참고문헌과 인용 논문을 서지·출처·`access_level_candidate`와 함께 돌려줍니다.
- `preprints`는 Europe PMC `SRC:PPR`에서 bioRxiv·medRxiv를 찾고 모든 결과에 `심사 전`을 표시합니다.
- lit_scout·biologist는 핵심 논문마다 한 번 인용 추적을 하고, 방향별 발견 수·새로 넣은 수를 검색 기록에 남깁니다.
- 조회 기록·30일 cache·요청 간격은 기존 `labhq_annot` 규칙을 따릅니다.

## 확인

- 새 회귀는 구현 전 5 failed, 구현 뒤 관련 pytest 79 passed입니다.
- `scripts/integrations.py --check`, `git diff --check` 통과.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 동안 출력 없이 멈춰 timeout으로 끝났습니다.
- `docs/manual.md`: 158,685자 → 159,186자(+501자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 총괄이 이어갑니다.

## 결론

PR #731에서 비용 실측을 계획 `step_id` 단위로 합치고, `DE` 약어와 이름 붙은 부정 분석을 요청 범위 검사에 반영했습니다. Fixes #728.

## 바뀐 것

- 재시도·wake·결과 교정의 여러 `cost_item`을 같은 계획 단계 비용으로 합칩니다.
- 독립된 `DE`는 DEG로 감지하고 다른 단어 속 `de`는 제외합니다.
- `경로 분석은 하지 말고`, `without pathway analysis` 같은 제외 문구는 요청 분석에서 뺍니다.
- 실행 상한의 `max(remaining, planned)` 보호는 유지합니다.

## 확인

- 새 회귀는 main 상태에서 4건 실패했고 수정 뒤 4건 통과했습니다.
- 관련 pytest는 375 passed·1 skipped입니다. 상대 `PYTHONPATH` 때문에 실패한 CP1 2건은 절대 경로로 다시 실행해 2 passed입니다.
- gateway의 `step_id` 비용 기록 test 1건도 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 넘게 무출력 정체되어 중단했습니다.
- `docs/manual.md`: 158,685자 → 158,777자(+92자).

## 남은 것

- push 뒤 CI 결과 확인. 리뷰 요청과 병합은 총괄이 이어갑니다.

## 결론

PR #730의 마지막 P1을 반영해, 열린 read root와 겹치는 internal·restricted 구역의 Read 사전 허용을 없앴습니다. Fixes #725.

## 바뀐 것

- 통제 구역을 품거나 그 안에 있는 작업·project·참고 root에는 `Read(//root/**)`를 만들지 않습니다.
- 그 root의 Read는 realpath 승인 gate가 판정해, 실행 중 생긴 같은-root 별칭도 internal이면 ask로 보냅니다.
- 통제 구역과 겹치지 않는 root의 scoped Read 사전 허용은 유지합니다.

## 확인

- 새 회귀는 직전 head에서 1 failed·1 passed, 수정 뒤 internal·restricted·대조군 3 passed입니다.
- 관련 pytest는 808 passed·39 skipped 뒤 환경 오류 8건만 절대 `PYTHONPATH`로 다시 돌려 adapter 12 passed·1 skipped입니다.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 158,520자 → 158,685자(+165자).

## 남은 것

- push 뒤 CI 결과 확인과 P1 인라인 답글이 남았습니다. 리뷰 재요청과 병합은 총괄이 이어갑니다.

## 결론

PR #729에서 선택한 요청의 공개 진행본을 단계마다 별도 GitHub branch에 올리게 했습니다. Fixes #704.

## 바뀐 것

- 처음 GitHub 저장 선택에 기본 꺼짐인 단계별 진행 게시를 추가했습니다.
- `github.progress_branch`의 공개 Markdown·SVG·코드와 `progress/README.md` 상태표를 단계마다 갱신합니다.
- 내부 단계와 게시 전 검사 실패 파일은 빼고, 일시 오류는 다음 단계에서 재시도합니다. 토큰·권한 오류는 한 번 알린 뒤 끕니다.
- 최종 정리본은 기존처럼 검토·승인 뒤 최종 branch에 게시합니다.

## 확인

- 새 회귀는 구현 전 5 failed, 구현 뒤 7 passed입니다.
- 관련 pytest 395 passed, Node CJS 19 passed입니다.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 158,005자 → 158,402자(+397자).

## 남은 것

- push 뒤 CI 결과 확인. 리뷰와 병합은 총괄이 이어갑니다.

## 결론

PR #727에서 주 산출이 있는 단계는 선택 산출이 빠져도 진행하고 경고를 남기게 했습니다. 필수 산출 누락은 기존처럼 incomplete입니다. Fixes #716.

## 바뀐 것

- 기존 `outputs`는 필수로 유지하고 로그·변환 기록·미리보기용 `optional_outputs`를 추가했습니다.
- 64자리 0 `plan_sha256` placeholder는 실행 중인 승인 계획 hash로 보정하고 경고합니다. 다른 불일치는 계속 거부합니다.
- 글자 치환·형식 변환은 직원이 자기 작업 폴더에서 Python 스크립트 하나로 처리하도록 계획 규칙을 고쳤습니다.

## 확인

- 새 회귀는 구현 전 실패하고 구현 뒤 7건 통과했습니다.
- 관련 pytest: 508 passed, 1 skipped.
- 첫 CI Python 3.12의 호환성 회귀 2건을 고친 뒤 해당 test는 2 passed입니다.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 157,625자 → 157,816자(+191).

## 남은 것

- push 뒤 CI 결과 확인.

## 결론

PR #724의 리뷰 지적 다섯 건을 `e7a549a`에서 반영해 범위 경고와 실측 비용 추정의 과소 상한을 막았습니다. Fixes #707. Fixes #708.

## 바뀐 것

- 부정하지 않은 분석 항목을 `brief.scope`와 실행 `instruction`에서 따로 검사합니다.
- `unknown_count: 0`이며 topic이 겹치는 이력만 비교하고, signature별 실제 단계 수로 평균을 냅니다.
- 실측 추정이 계획값보다 낮아도 실행 상한은 낮추지 않습니다.

## 확인

- 새 회귀 7건은 직전 head `81f2bbd0`에서 모두 실패한 뒤 통과했습니다.
- 관련 pytest 489 passed, 1 skipped입니다.
- `scripts/check_public.sh`와 `git diff --check`가 통과했습니다.
- `docs/manual.md`: 157,434자 → 157,476자(+42자).

## 남은 것

- push 뒤 CI 결과 확인이 남았습니다. 리뷰 재요청과 병합은 총괄이 이어갑니다.

## 결론

PR #723의 P1 다섯 건을 게이트와 메모리 두 부류로 수정했습니다. Fixes #706. Fixes #709.

## 바뀐 것

- 내부 구역 읽기는 출력 모양과 무관하게 같은 승인을 받습니다. 카드는 `내부 자료 행 출력 — 거절 권고` 또는 `머리줄·집계로 보임 — 출력 범위 확인`으로만 구분합니다.
- Windows는 self-Job wrapper가 실제 CLI보다 먼저 Job에 들어갑니다. POSIX는 user `systemd-run --scope`의 `MemoryMax`를 우선 쓰고, 없으면 기본 40%의 프로세스별 `RLIMIT_AS`로 내립니다.

## 확인

- 기존 head에서 새 회귀 test 9건 실패를 확인했습니다.
- 게이트 관련 pytest: 328 passed, 12 skipped.
- 메모리·doctor pytest: 69 passed, 1 skipped. Windows 128 MiB 상한도 실제 강제했습니다.
- 첫 CI Python 3.10은 강제되지 않는 systemd `MemoryMax`를 잘못 골라 6건 실패했습니다. 실제 할당 probe와 오류 번역을 고친 뒤 실패 test 묶음은 28 passed, 1 skipped입니다.
- `scripts/check_public.sh` 통과. 수정 head CI는 push 뒤 확인합니다.
- `docs/manual.md`: 156,110자 → 156,179자(+69).

## 결론

semantics가 labhq 생성 ID를 private 식별자로 오인하던 경로와, citation 부가정보 때문에 P2A PMID를 읽지 못하던 경로를 고쳤습니다. Refs #559. Refs #660.

## 바뀐 것

- request·task·approval ID와 이름이 붙은 생성 hash는 경계 원문에서 빼되, private 경로·restricted 내용 차단은 유지합니다.
- `doctor`·`status`가 중단 이유·시각·경계·`labhq semantics enable`을 보입니다. 기존 `request_id:identifier` 오판은 자동 해제하지 않습니다.
- PMID 뒤 `access_level`이 붙어도 선택을 이어 갑니다. `full_text`만 빌드하고, 나머지는 계획·CP1·부록에 네 상태와 생략 이유를 남깁니다.

## 확인

- 새 회귀 test는 수정 전 5 failed, 수정 뒤 5 passed입니다.
- 관련 파일 24개: 998 passed, 2 skipped. 마지막 P2A 보정: 15 passed.
- `docs/manual.md`: 157,208자 → 157,209자.

## 결론

PR #721의 단계별 내부 자료 판정을 보수적으로 고쳤습니다. 내부 입력의 파일별 단계 귀속을 모두 증명하지 못하면 요청 전체 내부 규칙을 씁니다. Fixes #714. Fixes #715.

## 바뀐 것

- 단계 계획과 결과에서 내부 경로·구역 이름을 함께 검사합니다.
- 요청·계획의 내부 파일을 모두 특정 단계에 귀속해야 공개 단계를 허용합니다.
- 구역 루트만 있거나 파일 하나라도 빠지면 요청 전체 허용 목록과 작은 칸 검사를 적용합니다.

## 확인

- 새 회귀는 수정 전 1 failed, 수정 뒤 통과했습니다.
- portfolio·result_storage·edition·publish 관련 pytest 215 passed입니다.
- 기존 공개 GEO 단계 표 사례도 통과했습니다.
- `docs/manual.md` 157,250자 → 157,286자(+36자)입니다.

## 남은 것

- push 뒤 CI 결과 확인과 이후 리뷰·병합은 총괄이 이어갑니다.

## 결론

정리본 README의 갈래 표에 원장 JSON 대신 claim 문장을 넣고, 맨 위 '답:'은 판정 문장을 고르게 했습니다. Fixes #718.

## 바뀐 것

- 연구 단계 결과는 원장 claim 문장으로 요약합니다. `structured`가 없으면 `text`의 원장 JSON을 읽습니다(갈래 표·단계 표·실패 이유).
- 갈래 표 결과는 첫 claim의 앞 두 문장입니다. 숫자 문장을 고르던 규칙은 claim이 없는 단계에만 씁니다.
- '답:'은 요약과 종합 단계 claim에서 판정 문장("…지지되지 않습니다")을 먼저 고르고, 앞의 '그래서·따라서'와 claim anchor를 뺍니다. 판정 문장이 없으면 지금처럼 요약 첫 문단입니다.
- 문장은 마침표·물음표·느낌표 뒤에서만 나눕니다("단태아보다 여아"를 끊지 않음).

## 확인

- 새 test 3개(`tests/test_edition_readme_718.py`).
- 실제 요청(req_5b008d026a)에서 갈래 표가 claim 문장으로, 답이 "주 가설 '…'는 지지되지 않습니다."로 나옵니다.
- portfolio·README 관련 test 1,479 passed(이 PC의 node 환경 탓인 web 3D test는 CI에서 확인).

## 결론

10-17 시운전 정리본을 멈춘 로컬 경로 오탐을 고치고, 검사가 정리본을 멈추면 그 검사와 파일을 경고에 적습니다. Fixes #713.

## 바뀐 것

- `_LOCAL_SCAN`의 루트 폴더(`/home`·`/data`·`/tmp` 등)는 경로가 시작하는 자리에서만 셉니다. `inputs/om_fetch/data/…` 같은 상대 경로는 걸리지 않습니다.
- 링크·게시 전 검사 실패는 `EditionCheckError`로 냅니다. 저장 경고가 "토큰 권한 확인" 대신 `게시 전 검사에 실패했습니다: <검사> — <정리본 파일>`을 보입니다(값·로컬 경로는 적지 않음).
- manual 결과 저장 절에 검사가 멈췄을 때의 안내를 더했습니다.

## 확인

- 새 test 6개(`tests/test_edition_check_713.py`) 중 3개가 수정 전 실패하고 수정 뒤 통과합니다(상대 경로 2, 경고 문구 1).
- 실제 요청 자료(req_5b008d026a)를 고친 코드로 다시 검사하면, 남는 정리본 파일 23개에서 문제 0건입니다(고치기 전 local_path 3건).

## 남은 것

- 끝 질문이 없는 요청에서 `review-done --storage`가 검토만 저장하고 409를 내는 문제: #714
- 내부 자료 요청의 공개 단계 표가 빠지는 문제(단계별 출처 판정): #715

## 결론

단계 산출 표가 표로 읽히는지와 문헌을 읽은 수준을 runner가 결정적으로 검사하고, CP2 카드와 보고서 한계 절에 한 줄씩 올립니다. Fixes #710. Refs #697.

## 바뀐 것

- `labhq/output_checks.py`: `.tsv`·`.tab`·`.csv` 산출의 칸 구분(글자 `\t`)과 줄별 칸 수 검사, `labhq_annot.fulltext` 조회 기록에서 논문별 최고 접근 수준 집계, 보고서 경고 문장.
- runner가 단계 뒤 두 결과를 `TaskResult.table_warnings`·`literature_access`에 담습니다(비어 있으면 wire에서 빠짐).
- 연구 요청은 `research_contract.result_warnings`(CP2 카드·보고서 `## 한계`), 일반 요청은 `## 보고서 경고`에 들어갑니다.
- 연구 계획 프롬프트: 문헌 갈래는 결론에 쓰는 논문의 본문을 읽고 access_level을 적으며, PI가 원할 때만 '초록만' 경계를 둡니다. 고정 hash를 갱신했습니다.

## 확인

- 새 test 15개(`tests/test_output_checks.py`)는 main에 모듈이 없어 실패하고 이 branch에서 통과합니다. 10-17 시운전의 글자 `\t` 표, R 행 이름 표, 따옴표 CSV, 바깥 경로, 연구 CP2 카드·한계 절까지 봅니다.
- 보고서 경고·wire·연구 흐름·runner 경계 관련 test 59파일 2,016 passed.

## 남은 것

- #697의 '결론 근거 중 초록 수준 논문 수'는 fulltext로 조회한 논문 기준입니다. claim별 근거와 논문을 잇는 집계는 근거 원장에 논문 ID가 생기면 다시 봅니다.

## 결론

PR #711에서 보고서 검토 뒤 처음 고른 저장 위치를 보여 주고 다시 묻게 했습니다. 답하기 전에는 편집자가 정리본을 만들지 않습니다. Fixes #688.

## 바뀐 것

- 계획 단계 질문은 유지하고, 새 요청만 끝 질문을 받습니다.
- 처음 선택 유지·로컬·새 GitHub 저장소·기존 저장소를 웹과 CLI에서 고릅니다.
- GitHub 권한 탐침이 실패하면 빠진 권한과 `labhq init`을 알리고 로컬 정리본을 만듭니다.
- 게시 완료·실패·취소·변경 전 검토 완료 요청은 끝 질문을 열지 않습니다.

## 확인

- 새 회귀는 구현 전 5 failed, 구현 뒤 8 passed입니다.
- 관련 pytest 27파일 877 passed, 웹 Node test 9 passed.
- `scripts/check_public.sh`, `git diff --check` 통과.
- `docs/manual.md` 156,201자 → 156,542자(+341자).

## 남은 것

- CI 확인과 이후 리뷰·병합은 총괄이 이어갑니다.

## 결론

PR #705에서 갈래 계획 정리본을 `studies/` 아래 갈래별로 나누고, 마지막 리뷰의 원고 선택·보고서 보존·상태 표시 회귀를 고쳤습니다. Fixes #686.

## 바뀐 것

- 갈래 단계의 코드·그림·표·단계 보고서는 `studies/<갈래 id>-<이름>/`에, 종합 보고서·그림은 루트에 둡니다.
- `studies/README.md`와 루트 README에 완료·실패 상태, 주요 결과, 갈래 폴더 링크를 넣습니다.
- 원본→정리본 경로 표로 동명 그림 링크를 갈래별로 고쳤습니다.
- 내부 자료 허용 목록과 갈래 없는 정리본 파일 목록은 그대로입니다.
- 여러 종합 단계가 원고를 만들면 모든 갈래를 읽는 최종 단계 중 의존 순서상 마지막 원고를 고릅니다.
- 갈래의 terminal별 성공 여부로 완료·부분·실패를 나누고, 부분 성공이면 성공 결과와 실패 이유를 함께 남깁니다.
- review가 accept이면 최종 CSO 요청 원고를 유지하고, revise이거나 요청 원고가 논문 형식이 아닐 때만 마지막 종합 단계 원고를 씁니다.
- 갈래 Markdown 보고서는 파일명이 아니라 `output_types`의 `report` 선언으로 보존합니다.
- 루트 README 갈래 표에도 완료·부분·실패 상태를 표시합니다.

## 확인

- 마지막 리뷰 회귀 3건은 직전 head에서 각각 실패했고 수정 후 핵심 test 4 passed입니다.
- `portfolio|result_storage|edition|branch` pytest 7파일 155 passed입니다.
- `scripts/check_public.sh`, `git diff --check` 통과.
- `docs/manual.md`는 기존 정리본 문단만 고쳐 156,106자에서 156,201자로 95자 늘었습니다.

## 남은 것

- GitHub CI 확인과 병합은 총괄이 이어갑니다. 이 PR의 리뷰 수정은 여기서 끝냅니다.

## 결론

PR #703에서 서로 다른 근거 갈래를 한 요청으로 계획하고, 일부 실패에도 남은 갈래와 종합을 계속하게 했습니다. Refs #686. Fixes #694.

## 바뀐 것

- PLAN에 선택형 `branches`와 단계별 `branch`를 넣었습니다. 갈래가 없으면 기존 12단계 상한과 차단 방식을 그대로 씁니다.
- 갈래 계획은 기본 5갈래·갈래당 8단계·종합 3단계를 검증하며, 초과하면 자르지 않고 교정 PLAN을 받습니다.
- 한 갈래의 실패는 그 갈래 뒤 단계만 막습니다. 완료 갈래가 있으면 실패 이유와 마지막 완료 단계를 종합 입력에 넣습니다.
- 모든 갈래 terminal이 종합 의존 경로에 들어와야 PLAN을 승인합니다. 실패한 갈래의 파일·본문·작업 폴더는 종합 입력에서 빼고 memo만 넘깁니다.
- 성공한 갈래 terminal이 없으면 갈래별 상태 표와 함께 CP2 전에 실패합니다. 43단계에서도 `id`·`branch`·`depends_on`·`phase`는 digest에서 자르지 않습니다.
- CP1 갈래 표, CP2 갈래별 근거, 종합 보고서의 누락·비유의 결과·대체 분석 규칙과 가설에 맞는 기준 범주 점검을 추가했습니다.
- 고정값을 의도적으로 갱신했습니다: PLAN schema `dc93790d…bf26`, 연구 계획 prompt `6ca7ceba…e1d`.

## 확인

- 리뷰 회귀 4건은 수정 전 모두 실패했고 수정 후 8 passed입니다.
- `cso|contract|research|branch|report|review` 관련 pytest 40파일 1,659 passed입니다. 이번 수정으로 prompt hash 고정값은 바뀌지 않았습니다.
- `scripts/check_public.sh`, `git diff --check` 통과.
- 리뷰 보완으로 `docs/manual.md`는 155,861자에서 155,944자로 83자 늘었습니다.

## 남은 것

- `result_storage`의 갈래별 폴더는 범위 밖 후속입니다.
- 리뷰 요청과 병합은 총괄이 이어갑니다.

## 결론

로컬로 끝난 요청을 나중에 GitHub 저장소로 옮기는 `labhq storage <요청 id> --to github`를 더했습니다. 저장 위치 카드는 PI 문구인 "결과를 GitHub 저장소로 정리할까요?"로 묻고, 토큰이 없거나 권한이 모자라면 무엇이 빠졌는지와 나중에 올리는 법을 보입니다. Refs #688.

## 바뀐 것

- `ResultStorage.to_github`, API `POST /api/requests/<id>/storage/to-github`, CLI `--to github [--name] [--visibility]`. 로컬 정리본이 있는 요청만 옮깁니다. 토큰은 `GET /user`와 이름 없는 `POST /user/repos`(422면 만들기 권한 있음, 아무것도 만들지 않음, #687 실측)로 먼저 확인합니다. 그다음 새 저장소로 바꾸고, 저장소 첫 화면 구성으로 편집자가 정리본을 다시 만듭니다. 게시 카드 승인과 보고서 검토 조건은 그대로라 승인 전에는 아무것도 올리지 않습니다.
- 저장 위치 카드: 질문 문구, 그리고 토큰 없음·권한 확인 실패 때의 안내(`labhq init`, 필요한 권한, `--to github`).
- fine-grained 토큰이면 "Repository access는 All repositories" 안내를 답에 붙입니다.

## 확인

- 새 test 14개(성공 흐름, 검토 대기, 거절 네 가지, 이름·공개 범위, 토큰 없음, 401·403, 통제 데이터, fine-grained 안내, 카드 문구, CLI). 저장·게시·정리본 관련 test 487 passed, 웹 카드 Node test 3개 통과.

## 남은 것 — PI 결정 필요

- 지금 저장 위치 카드는 요청이 **끝날 때**가 아니라 **계획 단계**(CP1 전, 확인 질문과 함께)에 뜹니다. PI는 끝날 때 묻는다고 말했습니다. 시점을 옮기려면 `cso.py`를 고쳐야 해서 #686 2단계 레인이 병합된 뒤 합니다. 옮길지는 #688에서 여쭙니다.

## 결론

`labhq init`이 GitHub 토큰을 묻되 필수는 아니게 하고, doctor가 토큰 앞부분 대신 실제 권한을 탐침해 빠진 권한을 이름으로 알립니다. Fixes #687.

## 바뀐 것

- `labhq/github_token.py`: 환경 변수 → 이 계정만 읽는 `github.token_file` 순서로 토큰을 읽습니다. 파일의 토큰은 gateway가 시작할 때만 자기 환경에 올리고(직원·runner·MCP에는 안 감), 직원에게는 개인 경로로 막힙니다.
- 권한 탐침은 아무것도 만들지 않습니다. 이름 없는 `POST /user/repos`, 내용 없는 `PUT contents`, 제목 없는 `POST issues`에 권한이 있으면 422, 없으면 403이 옵니다(2026-10-16 실측, #687 댓글). classic 토큰은 `X-OAuth-Scopes`로 판정합니다. 네트워크가 안 되면 경고 대신 "처음 게시 때 확인"으로 둡니다.
- init: 필요한 권한 목록과 토큰 만드는 곳을 보인 뒤 숨김 입력으로 묻습니다. Enter로 건너뛰면 결과는 로컬에만 저장합니다. 토큰 값과 파일 위치는 출력하지 않습니다.
- doctor: 토큰이 없으면 "결과는 로컬에 저장"(ok), 있으면 탐침 결과 한 줄. fine-grained 토큰에는 Repository access를 All repositories로 두라는 안내를 붙입니다. 접근 범위는 API로 알 수 없기 때문입니다.
- test 환경은 운영자의 `GITHUB_TOKEN`·`GH_TOKEN`을 지웁니다. 그렇지 않으면 doctor test가 GitHub를 실제로 탐침합니다.

## 확인

- 새 test 21개와 바꾼 #607 doctor test 4개, private path·init·doctor·config·게시 관련 test 통과(441 passed, 323 passed).
- #607 test는 "fine-grained면 늘 경고"를 고정했는데 #687이 바꾸는 동작이라, 탐침 결과로 판정하도록 고쳤습니다. AlphaGenome init test 2개는 getpass 질문 수를 세므로 AlphaGenome 질문만 세게 했습니다.

## 남은 것

- 요청이 끝날 때 "GitHub로 정리할까요?" 질문은 #688입니다.

## 결론

PR #699의 5차 봇 지적 다섯 건을 고쳤습니다. 이번이 마지막 수정 라운드입니다. Refs #686.

## 바뀐 것

- 새 포트폴리오에서 게시하지 않는 PNG·JPG·WebP 링크를 README와 과학 보고서의 생략 표시로 바꿉니다.
- PDF는 review 뒤 고른 canonical 과학 원고를 쓰며 그림 링크는 요청 묶음의 `steps/...` 경로로 되돌립니다.
- 동명 Nextflow 산출물은 단계별 경로에 따로 보존합니다.
- 이전 draft 이관 때 README의 `report.md` 링크를 `report/report.md`로 고칩니다.
- 새 포트폴리오의 긴 보고서 안내도 `report/report.md`를 가리킵니다.

## 확인

- 다섯 회귀는 직전 head에서 5 failed, 수정 뒤 5 passed였습니다.
- 지정된 pytest 18개 파일은 356 passed, Node 4개 파일은 28 passed입니다.
- `scripts/check_public.sh` 통과. patch-notes·notes-index는 기록 커밋 전에 다시 검사합니다.

## 남은 것

최종 push 뒤 CI 결과만 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

직원이 논문 본문을 읽는 기록된 경로를 만들었습니다. `labhq_annot.fulltext`가 Europe PMC 오픈 액세스 본문을 받아 작업 폴더 `.tmp/literature/`에 쓰고, 문헌·생물·QC·리뷰 직원 지시가 그 경로와 `access_level` 칸을 씁니다. Refs #697.

## 바뀐 것

- `labhq/tools/annot_literature.py`: PMID·PMCID·DOI를 Europe PMC에서 찾고, 오픈 액세스 사본은 JATS를 Markdown(절·표·그림 설명·참고문헌)으로 바꿔 씁니다. 사본이 없으면 초록을 씁니다. 결과에 `access_level`(full_text·abstract·metadata)과 라이선스가 붙고, 조회 기록은 다른 annot 도구처럼 `outputs/annotation_queries.jsonl`에 남습니다. 본문은 기록 파일에 다시 넣지 않고 경로와 sha256만 둡니다.
- lit_scout·biologist: 결론에 쓰는 논문은 `fulltext`로 본문을 읽고, CAPTCHA 화면은 "열지 못함"으로 적고 다시 열지 않으며, 유료 논문은 우회하지 않고, 표마다 `access_level` 칸을 둡니다.
- qc_reviewer: 초록에서 뽑은 효과 크기·수·구간과 `access_level` 칸 없는 표를 WARN, 한계 절에 수준별 논문 수. sci_reviewer: 초록만 근거인 효과 크기를 약한 근거로 봅니다.

## 확인

- 새 test 10개와 직원 지시 test, annot·registry·읽기 전용 profile test 132 passed, 1 skipped.
- 실제 Europe PMC 응답(PMC13248341)으로 변환을 확인했습니다: 41KB, 표 2개, 참고문헌 46개.
- 고정 prompt hash는 CSO prompt를 건드리지 않아 바뀌지 않습니다.

## 남은 것

- 보고서 경고에 결정적 검사(초록 수준 근거 수)를 붙이는 일은 #679·#686 레인이 `cso.py`를 고친 뒤 후속으로 합니다.
- 출판사가 호스팅하는 CC 논문은 `publisher_copy` 링크 한 번 시도까지만 합니다. Unpaywall 같은 다른 오픈 액세스 경로는 후속 후보입니다.

## 결론

PR #696에서 큰 연구 계획의 StructuredOutput 오류를 진단·복구하고, 계획 크기를 설정값으로 경고합니다. Fixes #679.

## 바뀐 것

- Claude partial stream을 합쳐 실패 원문 전체를 작업 폴더 `.tmp/structured_output_failures/`에 저장합니다.
- 재시도 prompt가 비ASCII 문자를 `\u` escape로 바꾸지 않게 하고, 잘림 의심 plan은 설명·반복·큰 표를 줄여 갈래별로 요약하게 합니다.
- `orchestrator.plan_max_chars`(기본 50,000자)를 넘는 유효한 plan은 실제 문자 수와 상한을 경고로 기록하되 자르지 않습니다.

## 확인

- 새 회귀는 main 코드에서 6 failed, branch에서 관련 pytest 21 passed입니다.
- `scripts/check_public.sh`, `git diff --check`가 통과했습니다.
- 고정 prompt hash는 바뀌지 않았습니다. `docs/manual.md`는 153,365자에서 153,459자로 94자 늘었습니다.

## 남은 것

- 실제 직원 CLI 재현은 하지 않았습니다. CI 결과는 최종 push 뒤 확인합니다.
- 리뷰 요청과 병합은 하지 않습니다. 남은 지적 없음.

## 결론

직원용 글쓰기·그래픽 초록 스킬을 실제 엔진 로더에 연결하고 PR #693의 봇 지적 두 건을 수정했습니다. Fixes #684, Fixes #689.

## 바뀐 것

- `humanizer` 1.6.1-local과 MIT 고지, `graphical-abstract` 스킬·템플릿·예시를 넣었습니다.
- 쓰기 가능한 Claude 직원은 bundled plugin을 명령줄로 받고 Codex 직원은 `.agents/skills` 복사본을 읽습니다.
- 읽기 전용 실행은 skill 요구를 제거하고 금지 표현·전문어·쉼표·그래픽 초록 규칙을 prompt로 받습니다.
- 보고서·정리본 README의 한국어 금지 표현 QC와 윤문 보존 검사 함수를 추가했습니다.

## 확인

- 리뷰 회귀 2건은 직전 head에서 2 failed, 수정 후 통과했습니다.
- 관련 pytest 126 passed, 4 skipped. 공개 저장소·패치노트 검사는 커밋 전에 다시 확인합니다.
- 고정 prompt hash는 바뀌지 않았습니다. 이번 수정에서 manual은 153,219자에서 153,365자로 146자 늘었습니다.

## 남은 것

- 윤문 단계 연결은 후속 범위입니다. #683·#690의 CSO 그림·보고서 규칙은 건드리지 않았습니다.
- CI는 최종 push 뒤 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #692에서 연구 보고서 그림을 SVG 원본 품질로 보존합니다. Fixes #683, Fixes #690.

## 바뀐 것

- 분석 단계는 핵심 그림 1~2개를 SVG로 선언하고, 보고서는 결과 절에 넣거나 이유를 한 줄로 적습니다.
- QC는 그림 누락, SVG 없는 PNG·JPEG, 한글 `<text>` 없는 SVG를 WARN으로 남기되 exit 판정과 감사 번들을 막지 않습니다.
- PDF는 원본을 재인코딩·재샘플링하지 않고 세로 그림의 표시 높이만 220mm로 맞춥니다. `RENDER_VERSION=3`으로 옛 cache를 버립니다.
- 정리본 원본은 무손실로 두고 승인 카드 미리보기만 파일당 1 MB·합계 5 MB로 제한합니다. SVG는 base64 data URI를 `<img>`로 표시합니다.
- 그림 파일을 만드는 화면·브라우저 캡처 경로는 코드에서 발견되지 않았습니다.
- SVG는 로컬 요청 묶음 원본 대신 비밀값·사용자 경로를 가린 게시 사본을 정리본·승인 카드·GitHub에 씁니다. 가림 뒤 비밀 패턴이 남으면 게시하지 않습니다.
- 결과 절은 선언된 핵심 그림별로 링크나 한 줄 생략 사유를 검사하고, 빠진 그림마다 WARN을 남깁니다.

## 확인

- 새 회귀: main 8 failed → branch 8 passed.
- 리뷰 회귀 pytest 69 passed, Node 8 passed. `scripts/check_public.sh` 통과.
- 2차 리뷰 회귀 4건은 수정 전 4 failed → 수정 후 4 passed.
- 3차 리뷰 회귀는 수정 전 3 failed → 관련 pytest 58 passed. 기존 절대경로 rewrite test도 통과했습니다.
- `docs/manual.md`: 149,918자 → 151,752자(+1,834자).

## 남은 것

- 그림별 누락 검사는 계획의 `key_figures`만 대상으로 삼고, 선언 없는 예전 묶음은 결과 그림 0개일 때만 경고합니다.
- 리뷰 수정 head의 CI 결과는 push 뒤 확인합니다. 리뷰 재요청과 병합은 하지 않습니다.

## 결론

PR #659의 마지막 라운드에서 P1 4285536192·4285536198을 수정했습니다. Refs #638, Refs #666.

## 바뀐 것

- PowerShell 비 URL 콜론 operand는 실제 작업 폴더 안 드라이브 절대 경로만 허용합니다.
- staff PATH로 찾은 실행 파일이 쓰기 불가한 시스템 위치에 있거나 셸 내장일 때만 닫힌 문법의 자동 허용을 적용합니다.
- 명령·cwd별 해석을 판정마다 한 번 수행하고 실행 파일 경로를 근거에 남깁니다.

## 확인

- 새 회귀 10건: 수정 전 10 failed, 수정 후 통과. 쓰기 권한 회귀 2건 추가.
- 관련 pytest 715 passed, 19 skipped. 공개 저장소 검사 통과.
- 기존 허용 test는 시스템 PATH와 일반 staff 권한을 명시하며 실제 파일·디렉터리 쓰기 권한 검사는 별도 검증합니다.

## 남은 것

- profile·함수 덮어쓰기, 판정과 실행 사이 교체 등 같은 부류의 남은 명령 해석 우회는 #666(OS sandbox)에서 다룹니다.
- CI 결과는 push 뒤 PR에 기록합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

Fixes #667. PR #668. StructuredOutput JSON 파싱 오류를 같은 세션에서 재시도하되, 유효한 최종 결과와 예산 한도를 먼저 확인합니다.

## 바뀐 것

- `runner.structured_output_retries`: 기본 2회. 오류 원문의 고정 문구로 판정하며 resume 비용·사용량을 합산합니다.
- 같은 turn의 최종 `structured`가 유효하면 잔존 `parse_error`가 있어도 다시 호출하지 않습니다.
- 각 resume 전 task 한도와 요청 잔여 한도 중 작은 금액에서 이전 호출 비용을 뺍니다.
- parse 오류는 retry wrapper가 직접 보존하고, 일반 tool error를 결과에 담는 기존 `general_result_contract` 경계는 유지합니다.
- 한도 초과는 ‘형식 오류’ 카드 한 번으로 알리고, 자동 재계획으로 한도를 초기화하지 않습니다.
- 공통 직원·비서실장 prompt에 원문 근거와 원인 추측 금지 규칙을 넣었습니다. 통역에도 도구 오류 근거를 전달합니다.
- 사용자 텍스트·명령·정규식은 계획 prompt에서 고쳐 쓰지 않습니다. 재시도 지시문에서 모델이 자기 JSON의 경로와 escape를 고치게 합니다.
- `research.result_corrections`와 별도입니다. output types off 계약과 prompt hash 고정값은 그대로입니다.

## 확인

- P1 회귀 2건: 수정 전 2 failed → 수정 후 전체 15 passed
- 관련 `structured_output`/general evidence/CSO/login pytest 273 passed. 전체 suite는 CI에 맡깁니다.
- `scripts/check_public.sh` 통과. patch-notes·notes-index 검사와 push 후 CI 상태는 PR에 기록합니다.

## 남은 것

실제 직원 CLI 실험은 하지 않았습니다. Claude 이벤트 변환부터 runner·CSO 카드까지 모의 오류로 검증했습니다. 리뷰 요청과 병합은 하지 않습니다.

# PR #659 — task 한정 grant 제거

## 결론

명령 해석만으로 실행 코드 전체를 고정할 수 없어 task 한정 grant를 제거했습니다. 닫힌 문법의 작업 폴더 리터럴 읽기·쓰기 자동 허용은 유지합니다.

## 바뀐 것

- grant 생성·재사용·종료 정리와 관리 스킬 hash 계산을 삭제했습니다.
- 결정 카드의 **이 작업 동안 같은 부류 허용** 선택지를 없앴습니다.
- 구버전이 저장한 `allow_task_class` choice는 runner 재연결 때 전달하지 않습니다.
- 스크립트·실행 코드는 매번 카드로 보내며, 반복 승인 축소는 OS sandbox 격리로 별도 이슈에서 다룹니다.

## 확인

- 관련 pytest: 652 passed, 16 skipped
- 관련 Node: 27 passed
- `scripts/check_public.sh` 통과
- 매뉴얼 150,826자 → 150,677자, 참고 문서 1,937자 → 1,584자

## 남은 것

push 뒤 CI 결과는 PR 본문에 기록합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #659의 P1 4285239660 수정. task grant는 `uv run --no-project --no-config <관리 script.py>` 직접 실행만 허용합니다. Refs #638, Refs #553.

## 바뀐 것

- 두 옵션을 모두 요구해 hash 밖 cwd의 project/backend·설정 탐색을 막습니다. 옵션 순서는 자유입니다.
- 다른 `uv run`·`python`·`python3`·`uvx`와 환경 변수 접두는 grant 없이 매번 카드입니다.
- 기존 닫힌 문법·쓰기 link 검사·관리 스킬 전체 hash·작업 종료 시 만료는 유지합니다.

## 확인

- 새 회귀 26건: 수정 전 10 failed/16 passed → 수정 후 모두 통과.
- 관련 pytest: 443 passed/13 skipped. cwd에 pyproject·backend·uv.toml을 만든 뒤 카드/재사용을 검증했습니다. 명령과 backend는 실행하지 않았습니다.
- 매뉴얼과 문법 참고를 갱신했습니다. 공개 검사·patch-notes·notes-index 결과와 push 뒤 CI는 로컬 인계 기록에 남깁니다.

## 남은 것

CI 결과 확인 뒤 총괄에게 인계합니다. 리뷰 재요청·병합은 하지 않습니다.

## 결론

PR #659의 P1 네 건을 닫힌 문법·쓰기 link 검사·관리 스킬 전체 트리 hash로 수정했습니다. 카드 감소보다 정확성을 우선합니다. Refs #638, Refs #553.

## 바뀐 것

- 변수·대입·치환·간접 실행·Python 본문은 카드. cd는 맨 앞 한 번, 뒤는 `&&`만 허용.
- 쓰기 대상·부모의 실제 경로와 reparse point, 기존 파일의 nlink=1 확인.
- 작업 스크립트에는 grant 없음. runner가 고른 쓰기 범위 밖 관리 스킬만 전체 트리 hash로 재사용.
- 자동 허용 로그와 카드에 문법 판정 근거 기록.

## 46건 재생

합성 문맥에서 판정기만 호출했습니다. 실행 당시 문맥은 없어 PI instance 재실행 결과로 보지 않습니다.

| 결과·남는 카드 부류 | 건수 |
|---|---:|
| 자동 허용 | 10 |
| 대입·확장·치환 | 13 |
| cd 위치·연결 | 3 |
| 스킬 실행 | 3 |
| Claude 저장 출력 shell 접근 | 3 |
| 다른 직원 venv | 1 |
| PowerShell 복합 식 | 1 |
| 입력이 잘려 재생 불가 | 12 |

재생 가능한 34건의 카드는 수정 전 18→24건입니다. [문법과 사례 번호](docs/reference/tool-approval-grammar.md).

## 확인

- 네 P1 모두 수정 전 자동 허용을 재현, 수정 후 카드. hard link의 세 쓰기 변형과 grant 재사용 포함.
- #553·#638·기존 PR을 포함한 관련 pytest: 853 passed, 38 skipped. 마지막 추가 후 해당 두 파일: 114 passed, 12 skipped.
- Node 승인 UI 10 passed. public·patch-notes·notes-index 검사는 로컬 로그에 기록.
- 매뉴얼 149,109→148,958자, 참고 문서 2,711→1,672자.

## 남은 지적

- 잘린 입력 12건과 당시 실행 문맥은 미확인입니다. 46건 오판 0건으로 보고하지 않습니다.
- 자동 허용 로그는 broker 장애 때 전달되지 않을 수 있습니다. 경로 검사는 OS sandbox가 아닙니다.
- CI는 최종 push 뒤 결과를 이 본문에 덧붙입니다. 리뷰 요청·병합은 하지 않습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

Fixes #652. PR #655의 P1 4284969814를 수정합니다. `ShutdownInterrupted`를 일반 `Exception`으로 분리하고 runner만 종료돼도 각 호출자가 중단 상태를 저장하도록 했습니다. 진짜 `asyncio.CancelledError`는 기존 취소 경로를 따릅니다.

기존 변경은 유지합니다: 종료 근거가 있는 요청은 `interrupted`와 재개 카드로 보존하며, 게시 카드는 PI 검토 뒤에만 엽니다. `mark-interrupted` 정정에는 저장된 종료 근거나 실패 직전 gateway 신호 기록이 필요합니다.

## 호출부와 처리

`git grep`의 직접 호출과 콜백 전달을 모두 확인했습니다. 요청 단계는 `run_request()`의 공통 처리에서 실행 중 단계를 정리하고 `_set_status(..., "interrupted")`로 재개 카드를 저장합니다.

| `run_step()` 호출부 | 종료 중단 처리 |
|---|---|
| `run_request`: direct, briefing, 최초·revise·clarify 계획, review, synthesis | 요청 공통 처리로 전파 |
| `run_request`: precedent | 일반 오류 fallback 전에 중단을 다시 던져 요청 공통 처리로 전달 |
| `run_dag.run_one`: 단계 실행·결과 JSON 정정 | 다른 실행 단계를 취소·회수한 뒤 요청 공통 처리로 전달 |
| `_run_solo`: 단독 실행·과학 리뷰 | 요청 공통 처리로 전파 |
| `_research_report`: 연구 리뷰·최종 보고 | 요청 공통 처리로 전파 |
| `prepare_recruits` → `recruit.planning.prepare_plan`: recruit_check·plan_recruit | 실패 receipt로 바꾸지 않고 요청 공통 처리로 전달 |
| `paper_skills.prepare_plan`: paper_skill | 변환 실패로 바꾸지 않고 요청 공통 처리로 전달 |
| `run_followup` | `interrupted`·종료 근거 저장, 다시 질문 허용, live 화면도 중단 상태 유지 |
| `answer_ask` → `_start_ask().route()` | `resolved` 저장, 중단·재시도 안내가 담긴 rejected 응답으로 원 작업의 waiter 해제 |
| `ResultStorage.finish`: result_editor | 저장 상태 `interrupted`; 편집자 runner 재연결 때 schedule. 재연결이 결과 도착 전·마지막 저장 중이어도 재개 |

## 확인

- 기본 회귀: 수정 전 5 failed / 5 passed → 수정 후 10 passed.
- 예외 계층 변경 뒤 발견한 콜백 회귀 3건도 수정 전 실패를 확인했습니다.
- 최종 새 회귀 18건 포함 관련 pytest **732 passed**. 기존 #655 테스트 포함.
- follow-up reducer: 수정 전 중단 상태 assertion 실패 → 수정 후 3 passed.
- 관련 Node **4 passed**. 공개 검사·patch-notes·notes-index 통과.

## 남은 지적

push 뒤 CI를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

Fixes #657. PR #661. 필수 증거 칸 누락은 한 번 확인한 뒤 출처가 있는 빈칸으로 드러내고, 성공한 독립 단계의 결과는 CP2로 보냅니다.

## 바뀐 것

- 같은 세션에 칸 id → 시도 상태·이유만 질문합니다. 코드가 `slot_repair` 또는 `omitted_by_output` 행을 씁니다. 모두 `unavailable`이며 근거를 지지·반박하지 않습니다.
- 원본 근거가 없는 `attempted_found`는 검증된 발견으로 승격하지 않습니다. 무응답·재개 불가·질문 실패도 출처와 이유를 CP2·reviewer·보고서 한계에 남깁니다.
- 실패 단계의 의존 단계만 건너뜁니다. 성공한 독립 단계가 없으면 요청 실패이며, 예산·PI 승인·frozen PLAN 검사는 유지합니다. environment는 기존 시설팀으로 갑니다.
- 오류 서명·요청 id·단계 id를 기존 dev log 또는 state의 `dev-reports.jsonl`에 남깁니다. PI에게 개발자 연락을 맡기지 않습니다.
- `slot_repair`는 별도 후속 turn schema를 씁니다. 내부 기록 필드 `origin`·`attempt_status`는 최초 step schema에서 제외해 off 경로 계약을 유지합니다.
- 칸 질문은 일반 요청 예산으로 계산합니다. 현재 비용과 한 task 예약액이 상한을 넘으면 질문 없이 `omitted_by_output`으로 남기고 원본 ledger를 유지합니다.
- manual과 `docs/decisions.md`에 기존 필수 칸 salvage 금지 규칙을 바꾸는 이유와 안전장치를 기록했습니다.

## 확인

- 예산 경계 test 3건: 수정 전 **2 failed, 1 passed** → 수정 후 **3 passed**. 파일 전체는 **22 passed**입니다.
- 앞선 관련 pytest **1,158 passed, 1 skipped**. 전체 pytest는 CI에 맡겼습니다.
- CI 실패 재현 뒤 output-types **14 passed**, shadow 제거 **1 passed**. 고정 hash는 갱신하지 않았습니다.
- CP2 Node **7 passed**, `scripts/check_public.sh` 통과. 기존 prompt hash pin 변경 없음.
- 이번 수정에서 manual **152,941 → 153,025자**.
- patch-notes·notes-index 검사와 push 후 CI 상태는 기록 커밋 후 확인합니다.

## 남은 지적

- 실제 직원 CLI·PI 요청은 실행하지 않았습니다. 리뷰 요청과 병합은 총괄에게 넘깁니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #655 P1 4284691846·4284691856을 같은 branch에서 수정했습니다.

## 바뀐 것

- runner 후처리·취소·재시작 복구는 `_shutdown_result`로 종료 근거를 붙이고 outbox에 보존합니다.
- gateway 시작·대기 복구·재개 시간 초과·CSO 종료·PI 정정은 `interrupt_request`로 상태·이벤트·재개 카드 한 장을 기록합니다.
- 요청의 interrupted 직접 대입은 공통 함수 안 한 곳입니다. 별도 followup 상태는 제외합니다.

## 확인

- 새 회귀: 수정 전 4 failed / 2 passed → 수정 후 6 passed.
- 관련 pytest 361 passed / 2 skipped; 일반 취소·HPC job 복구·기존 Hub 시작 경로 유지.
- 공개 검사 통과. patch-notes·notes-index 검사는 기록 커밋 전 실행합니다.

## 남은 것

push 뒤 CI 확인과 P1 인라인 답글을 남깁니다. 리뷰 요청·병합은 하지 않습니다.

## 결론

PR #659. 작업 폴더 승인 오판을 줄이고 같은 스크립트를 이번 작업 동안 허용하는 선택을 추가합니다. **부분 완료: 재생 가능 34건에서 카드 32→18건. 오판 0건 조건은 미달입니다.** Refs #638, Refs #553.

## 전후 재생

main `568c29e` 기준. 원본 입력이 완전한 26건은 먼저 main에 그대로 넣었습니다(카드 24, 허용 2). 실행 당시 cwd·private_paths·open_reads가 없어 문맥은 가정했습니다. 이후 개인 경로를 합성 폴더로 바꿔 전후를 비교했습니다. 명령을 실행하지 않고 판정기만 호출했습니다.

| 부류·사례 번호 | 건수 | main 카드 | 변경 후 카드 |
|---|---:|---:|---:|
| 이미 허용: 23·38 | 2 | 0 | 0 |
| 스킬 읽기: 10·18·29·32·35·40 | 6 | 6 | 0 |
| 요약에서 복원한 mock 삭제: 9·12·16·17·24·28·34·36 | 8 | 8 | 0 |
| 남은 스킬 실행·복합 명령 | 14 | 14 | 14 |
| Claude 저장 출력 shell 접근: 1·15·21 | 3 | 3 | 3 |
| 다른 직원 venv: 31 | 1 | 1 | 1 |
| 잘린 입력, 재생 불가 | 12 | 미확인 | 미확인 |

## 바뀐 것

- 작업 폴더 안 리터럴 읽기·쓰기·재귀 삭제 허용, `..`·링크·junction·자격 증명·다른 작업 경계 유지.
- 자동 허용은 카드 대신 작업 로그. 읽기 전용·restricted·위험 명령 규칙은 유지.
- 적격 스크립트 카드의 **이 작업 동안 같은 부류 허용**: 실제 task 문맥·도구·스크립트 경로와 SHA-256·인자를 매번 확인. 작업 종료 때 만료, 재접속 때 선택 전달.
- 매뉴얼 승인 절 상세는 reference로 이동: 149,788→148,710자.

## 확인

- 관련 pytest: 802 passed, 35 skipped(잘린 입력 12건 포함). 최종 restricted glob 보정 후 관련 테스트 686 passed, 19 skipped.
- Node 승인 UI: 22 passed. 브라우저 시각 확인: UNVERIFIED.
- `scripts/check_public.sh` 통과. 패치노트·status 기록 및 patch-notes·notes-index 검사를 통과했습니다.
- PI instance·PI venv·실제 서비스 실행·원본 JSON 커밋 없음. 전체 pytest는 CI에 맡깁니다.

## 남은 지적

- 원본 12건의 전체 입력과 당시 실행 문맥이 없어 46건 전체 재생을 확정할 수 없습니다.
- 스킬 실행·복합 명령 14건은 아직 묻습니다. 부류 허용은 리터럴 `python script.py`·`uv run script.py`에 한정되며 임의 shell 묶음은 지원하지 않습니다.
- Claude 저장 출력 shell 접근 3건은 기존 경계를 유지했습니다. 자동 허용 로그는 broker 통신 장애 때 전달을 보장하지 않습니다.
- 봇 리뷰 요청·수정·병합은 하지 않습니다. CI 결과는 push 후 확인합니다.

## 결론

PR #656. 결정·작업판을 주 영역으로 넓히고 실패 결과를 바로 엽니다. Refs #435.

## 바뀐 것

- 탭 위 계획 흐름: 현재 단계·담당·완료 접기, 폰에서도 같은 위치.
- 결정 패널 약 903px/1440px, 직원 요약 카드 54px. ×로 영역을 닫고 우하단 버튼으로 복원.
- 드래그 크기와 닫힘을 instance별로 저장. 이전 배치 크기는 새 기본값으로 이관.
- 결과 버튼의 이동 대상이 작업판 맨 위여서 이유·남은 결과가 화면 밖에 남았습니다. 결과 영역으로 직접 이동하고 초점을 옮깁니다. 클릭 이벤트 자체의 무반응은 데모에서 재현되지 않았습니다.

## 확인

- 필수 회귀 5개: 기준 main 실패 → branch 통과. 이관·폰·instance 격리·완료 접기도 통과.
- Node 전체 65파일, 234 passed/0 skipped. 관련 pytest 11 passed. 마지막 결과 동작은 관련 Node 24개 통과.
- 시각 기준 32개 갱신, public 검사 통과. [전후 화면과 근거](../reference/office-focus-layout/README.md).

## 남은 것

최종 push의 CI 상태는 PR 본문에 기록합니다. PI instance와 3D 파일은 변경하지 않았습니다. 리뷰 요청·병합은 총괄이 이어받습니다.

PR #655 · branch `shutdown-is-interrupted` · 코드 `c55954a`

종료 중 계획 생성이 `exit 129`로 끝나도 요청을 `interrupted`로 보존해 재시작 때 재개 카드를 엽니다. 게시 카드는 PI 검토 뒤에만 열고 실패·중단·revise 대체 시 열린 카드를 닫습니다.

Fixes #652

| 판정 근거 | 결과 |
|---|---|
| POSIX HUP·INT·QUIT·KILL·TERM의 `128+N` 또는 `-N` + gateway/runner 종료 표시 | interrupted |
| Windows `1`, `0xC000013A`, `0xC000010A`(signed 포함) + 종료 표시 | interrupted |
| 같은 코드지만 종료 표시 없음 / 종료 중이어도 다른 오류 코드 | failed 유지 |
| 재개 뒤 새 시도 | 이전 종료 근거 제거 후 다시 판정 |

- 공통 `run_step()`에서 단계·CSO 계획·답변 반영/revise 계획·최종 보고의 종료를 판정합니다. runner 결과, CLI 신호 처리, web shutdown, supervisor 없는 down도 종료 근거를 전달합니다.
- `pi_reviewed_at` 조건은 auto-publish 설정과 무관합니다. `review_done`이 저장본을 다시 schedule하며, 카드 종료는 `close_approval`을 씁니다.
- `labhq request mark-interrupted <id>`는 PI 인증 API에서 이전 상태를 이벤트에 저장하고 정정합니다. 기존 실패는 저장된 종료 근거 또는 실패 직전 10초 안의 gateway 신호 로그가 필요합니다. 현재 종료 표시만으로 과거 실패를 바꾸지 않습니다.

검증: origin/main `063a89d`에서 새 테스트 **23 failed / 7 passed**, branch **30 passed**. 관련 pytest **367 passed / 2 skipped** 후 마지막 상태 보존·카드 조건 변경은 새 테스트 30개로 재확인했습니다. 공개 검사·diff 검사 통과. 전체 pytest는 CI에서 확인합니다.

남은 지적: PI 데이터는 확인·정정하지 않았습니다. 과거 종료 로그가 최근 64 KiB에 없거나 시간 조건이 맞지 않으면 정정 명령은 거부합니다.

# PR #651 — 비용 모름 표시와 호출 없는 dry-run

Refs #625.

## 결론

비용을 확인하지 못한 새 호출은 0이 아니라 **비용 모름**으로 남고, `labhq send --dry-run`은 모델·도구·네트워크 호출 없이 계획 전 호출과 비용 범위를 보여 줍니다.

## 바뀐 것

- 과거 숫자 0 행은 유지하고 새 미확인 행은 `None`; 합계는 확인·추정액과 비용 모름 건수를 분리
- 정규직·파견직에 `fast`·`balanced`·`reasoning` 등급, 설정 한 곳에 출처·확인일·호출당 범위
- dry-run은 브리핑·선행 연구·첫 CSO 계획을 표시하고 계획 결과가 필요한 뒤 단계는 미정으로 표시
- manual은 262,279 → 264,415 bytes(+2,136)

## 확인

- 새 회귀: main 코드 4 failed → branch 15 passed
- 관련 pytest 491 passed; 마지막 영향 범위 261 passed
- 전체 CJS 64파일, 공개성 검사, `git diff --check` 통과

## 남은 것

추출 캐시·재개와 요인 실험 bench는 다음 조각입니다. CI 확인·봇 리뷰·병합은 개발 총괄이 이어받습니다.

# PR #650 — 벤치 정답 식별자 누출 검사

Fixes #623.

## 결론

bench case YAML의 정답 식별자가 비벤치 코드로 새면 CI가 파일과 줄, 원본 사례를 밝혀 실패합니다.

## 바뀐 것

- `result_fields.equals`의 string 식별자를 자동 수집하며 0건이면 실패
- 추적 중인 `labhq/**/*.py`, `agents/`, `config/`만 검사하고 bench 코드·데이터·test 제외
- 임시 복사본 누출 탐지와 일반 예시 문구 비탐지를 회귀로 고정

## 확인

- 관련 pytest 8 passed
- `scripts/check_public.sh` 통과

## P1 후속

- 식별자 경계를 ASCII 영숫자로 판정해 `GKN1을`, `(COL1A1과)`, `6OIM은구조`도 누출로 잡습니다.
- 영문 단어 안의 부분 일치는 계속 제외합니다.

## 남은 것

리뷰 요청과 병합은 총괄이 이어받습니다.

## 결론

Fixes #619. PR #649. 편집자 정리본의 내부 주소 우회와 긴 절 목록의 입력 한도 초과를 막았습니다.

## 바뀐 것

- HTTP(S) 역슬래시와 ASCII 공백·제어문자를 정규화한 뒤 host를 판정합니다.
- percent encoding·userinfo·legacy IPv4·IPv6·끝 점으로 쓴 내부 host도 같은 표에서 차단합니다.
- 개요·본문·생략 절 목록이 문서별 한도를 함께 쓰며, 긴 목록은 앞뒤 제목과 중간 개수로 줄입니다.

## 확인

- 공개 링크 회귀는 수정 전 7 failed, 수정 후 63 passed
- 5,000절 회귀는 수정 전 30,000자 한도에 199,089자, 수정 후 한도 이내
- 관련 pytest 85 passed, `scripts/check_public.sh` 통과

## 남은 것

CI 결과는 push 뒤 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #648에서 Windows CI의 staff-control flake를 사건 순서 검증으로 고쳤습니다. 원인은 제품 결함이 아니라 `sleep(0.1)` 뒤 `t1`이 시작됐다고 가정한 test 시간 의존입니다. Refs #59.

## 바뀐 것

- `t1`의 fake CLI 시작 뒤 `t2`를 제출
- `t2`가 semaphore 대기에 들어간 뒤 halt 전송
- 제품 halt 경로와 단언 강도는 그대로 유지

## 확인

- 수정 test 20회 연속 통과
- 관련 pytest 5 passed
- `scripts/check_public.sh` 통과

## 남은 것

- push 뒤 CI를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

# PR #647 — WSL 미설정 doctor 출력 정리

Refs #612.

## 결론

WSL을 설정하지 않은 Windows 설치의 `doctor`는 선택 기능이 꺼졌다는 한 줄만 표시합니다.

## 바뀐 것

- WSL 활성화 상태와 직접 실행한 `setup-wsl --check`는 개별 점검을 유지
- Claude 로그인 안내에 해당 WSL runner의 실제 `claude-staff` 경로 표시
- 새 회귀 2건으로 미설정·활성화 출력을 고정

## 확인

- 새 회귀: main 코드 2 failed → branch 2 passed
- `test_init_wizard.py`, `test_doctor.py`, WSL 관련 test: 187 passed
- `scripts/check_public.sh` 통과

## 남은 것

리뷰 요청과 병합은 총괄이 이어받습니다.

## 결론

Refs #644, PR #646. 세션 고정·재전송·solo 재시작·파이프라인 파일 요청의 CI 회귀를 복구했습니다. main에도 있는 Windows wizard 실패는 그대로 둡니다.

## 바뀐 것

- 전송은 roster 변경 시 계산한 `agent_runner`를 사용합니다. 전송 직전 재계산으로 담당을 지우지 않으며 세션 pin과 재전송 대상 검사는 유지합니다.
- runner 상태 없는 재개 계획 조회는 빈 유효 roster를 돌려줍니다. `last_runner_rosters` 이름은 유지합니다.
- 기존 테스트·CI 설정은 바꾸지 않았습니다.

## 확인

| 검증 | 결과 |
|---|---|
| main `5c0cfc2` Windows 비교 | wizard 1 failed, halt·semantics 제거 포함 7 passed |
| 수정 branch 관련 테스트 | 178 passed, main과 같은 wizard 1 failed |
| 지정 전체 pytest `-q -x -p no:cacheprovider tests` | 2706 passed, 12 skipped 뒤 같은 wizard 실패로 중단 |
| wizard 1건 제외 전체 실행 | 7243 passed, 67 skipped; 로컬 Node shim 때문에 web 연동 64 failed |
| 실제 Node 실행 파일로 실패 64건 재검증 | 64 passed |
| CJS 전체 | 64개 파일 통과 |
| 공개 저장소 검사 | 통과 |

## 남은 지적

- **전체 pytest 단일 실행 PASS는 아닙니다.** wizard는 main 로컬 및 Actions 38722581183에서도 같은 실패입니다.
- halt는 main·수정 branch 로컬에서 통과했습니다. 테스트와 runner 실행 코드는 main과 같습니다. 기존 Windows CI 실패 원인은 이번 실행으로 확정하지 못했습니다.
- push 후 CI 판정은 PR checks와 로컬 인계 로그에 남깁니다. 리뷰 요청·병합은 하지 않습니다.

## 결론

Fixes #644. 실행 중 task가 있으면 직원의 담당 runner를 유지하고 모두 끝난 뒤 새 task부터 준비된 WSL에 배정합니다. WSL 시작만 실패한 `up`은 경고 한 줄과 exit 0으로 끝납니다.

## 바뀐 것

- `Hub.effective_roster()`를 routing·등록 hosted_agents·generation 판정·불확실 전송·dispatch 재연결·카드 복구·채용 조회의 단일 출처로 사용합니다.
- 미완료 task의 저장된 runner ID로 담당을 고정합니다. 전송 불확실 timeout도 담당을 풀지 않으며 새 task를 다른 runner로 보내지 않습니다.
- grep(`git grep -n -e runner_agents -e last_runner_rosters -- labhq`) 결과는 server.py 8곳뿐입니다: 초기화 262·306, 재시작 offline 알림용 ID 목록 314, 계산 함수 2080·2082·2107, 입력 저장 2136·2138. 계산 함수 밖에 원본 roster를 읽어 담당을 정하는 경로는 없습니다.

## 확인

- origin/main `5c0cfc2`: 새 회귀 4 failed. 수정본: 같은 4 passed(native 실행 중 대체 runner 연결·재연결, 새 task도 native 유지, 모든 task 완료 후 WSL, 원본 접근 AST 검사, WSL 오류 경고/exit 0).
- 관련 pytest 278 passed/1 skipped. 채용 경로 변경 후 추가 pytest 103 passed(새 회귀 4건 포함).
- 기존 불확실 전송 test는 reconnect timeout만 0.01초로 줄이고 담당 유지·미완료 단언을 추가했습니다. 채용 test의 mock은 유효 roster API로 바꿨습니다.
- `scripts/check_public.sh` 통과.
- clone 전용 venv·격리 state·mock socket/WSL 사용. 전체 pytest는 CI에 맡깁니다.

## 남은 지적

- 없음. CI 결과는 push 후 확인합니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

Refs #622. PR #645. OKF Loremaster의 인용문·수치 원문 대조기를 독립 모듈로 옮겼습니다.

## 바뀐 것

- 주장 표기 정밀도로 수치를 대조하고 locator가 찾은 문장은 80단어로 제한
- Unicode 글자·숫자 보존, Python 3.10 호환, OKF schema 의존 제거
- Apache-2.0 전문·NOTICE·원본 커밋과 변경 표시, manual 라이선스 예외

## 확인

- 관련 pytest 222 passed, 2 skipped
- Python 3.10 grammar·한글 인용문·구간·가운뎃점·단어 창 회귀 통과
- wheel 라이선스 파일 포함, `scripts/check_public.sh` 통과

## 남은 것

2단계인 `lit_scout`·`verify.py` 연결은 하지 않았습니다. 리뷰 요청과 병합은 총괄이 이어받습니다.

## 결론

Refs #589. PR #643에서 Windows의 `labhq down`이 supervisor·role과 자식 프로세스의 실제 종료를 기다리게 했습니다. 두 CI 실패의 `_rust.pyd` 잠금은 종료 요청 직후 임시 checkout을 지운 경합이었습니다.

## 바뀐 것

- Windows process tree의 각 process와 기록된 root PID 종료를 최대 10초 확인합니다. timeout이면 role·PID를 알리고 exit 2로 끝납니다.
- clean-install은 `down` 뒤 임시 checkout `.venv`의 Python process 잔존을 role·PID 이름으로 검사합니다.
- clean-install 임의 포트에서 PI 포트 8787·55921을 제외합니다.

## 확인

- 새 종료 test 2건: 수정 전 2 failed, 수정 뒤 2 passed.
- 관련 pytest: 119 passed, 1 skipped.
- 로컬 `python scripts/clean_install_check.py`, `scripts/check_public.sh` 통과.

## 남은 것

- PR #643 CI 확인 전입니다.

# PR #630 — WSL 준비 판정·불확실 전송

| 항목 | 결과 |
|---|---|
| P1 4281071639 | 최초 runner·incarnation에 불확실 task 고정, 대상 변경 시 재전송 없이 uncertain |
| P1 4281071647 | 점검·활성화·gateway가 같은 준비 판정 사용, 미준비 상태는 native 선택 |
| 필수 준비 | bioinfo manifest·skill, Java·Nextflow, WSL Claude CLI·직원 로그인 |
| 검증 | 수정 전 회귀 실패 → 수정 후 통과, 관련 pytest 334 passed / 9 skipped, 전체 CJS 통과 |
| CI 보정 | Linux 임시 경로를 WSL 공유 경로로 잘못 사용한 새 test fixture를 수정 |
| 후속 인계 | push 뒤 새 P1 4281444658·P2 4281444670, 추가 수정 없음 |
| 범위 | 실제 WSL·PI 인스턴스 변경 없음, 리뷰 요청·병합 없음 |

근거: `tests/test_wsl_readiness.py`, `labhq/wsl_readiness.py`, `labhq/gateway/server.py`.

## 결론

Refs #634. PR #638의 P1 `4281094837`을 수정했습니다. 수정 커밋 `5b886e2`, main 병합 `07fd2ae`.

## 바뀐 것

- `scoped_open_reads()`에서 모든 읽기 예외를 최종 검사합니다. 현재 작업·논문 스킬 빌드 폴더와 명시한 plugin·skill·자기 Claude project만 남깁니다.
- workspace·talent 공용 root, 다른 작업 subtree, staff 공용 상위 경로와 링크 별칭을 제외합니다. 같은 목록을 Claude·Codex·managed shell이 받습니다.
- main의 카피바라 표시와 이 branch의 편집자 이름 보존을 함께 반영했습니다.

## 확인

- 새 회귀 23건: 수정 전 23 failed → 수정 후 23 passed. 다른 작업의 `outputs/RESULT.md` 차단·자기 파일 허용을 세 경로에서 확인했습니다.
- 관련 pytest 567 passed·3 skipped. #553 비공개 불변식 포함.
- 전체 Node CJS 62개 파일과 공개 저장소 검사 통과.
- 기존 공용 root 허용 단언을 차단 단언으로 바꾸고 Claude 저장 출력 fixture에 현재 작업 경로를 명시했습니다.
- PI 인스턴스·PI venv·HPC 사용 없음. 테스트는 clone venv와 격리 홈에서 실행했습니다.

## 남은 것

- 동적으로 조합한 셸 경로는 기존 정적 판정의 한계입니다.
- CI 결과는 PR checks에서 확인합니다. 리뷰 요청·병합은 하지 않습니다.

## 결론

Refs #640. PR #642. 단독 처리 직원 `solo`는 로컬 설정에 `character`가 없어도 사자로 보입니다.

## 바뀐 것

- 2D: 작은 전신 사자, 갈기·꼬리, 완료 체크리스트
- 3D: live roster 전용 procedural 사자와 가벼운 갈기
- manual: gitignore 대상 `agents/core/solo.local.yaml` 설정법

## 확인

- 신규 test는 main에서 실패하고 branch에서 통과
- 관련 pytest 87 passed, 1 skipped; Node CJS 전체와 visual baseline 32 views 통과
- 데스크톱·폰 전후 화면을 빈 임시 포트에서 확인

## 남은 것

CI 결과는 push 뒤 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

PR #638의 P1 3건·P2 1건을 수정했습니다. Refs #634.

| 지적 | 수정 |
|---|---|
| 4280803723 | Bash escaped space·인접 따옴표는 POSIX 토큰으로 합칩니다. 해석 실패·drive-relative 경로는 묻습니다. |
| 4280939685 | 통제·개인 경로는 읽기라도 높음·노출 확인 필요로 표시하고 동작별 설명을 유지합니다. |
| 4280939701 | Codex read-only profile에도 관리 폴더·해당 스킬의 읽기 grant를 적용합니다. 개인 하위 경로 deny는 유지합니다. |
| 4280939708 | 위치 label은 절대경로·구분자·대소문자와 링크 목적지를 확인합니다. `..`·링크로 벗어나면 label을 붙이지 않습니다. |

## 확인

- 수정 전 head `5c35f20`: 새 테스트 40건 중 33 failed·7 passed로 네 지적 재현.
- 최종 관련 pytest: 722 passed·8 skipped. 새 회귀 테스트 45건 포함.
- `test_claude_staff_home.py` 등 기존 보안 테스트 수정 없이 통과. 카드 테스트의 개인 경로 위험 기대값만 중간→높음으로 변경.
- 관련 Node: 16 passed. PI 인스턴스·사용자 홈·PI venv·실제 엔진 사용 없음.
- CI 결과는 push 후 PR 본문에 기록합니다. 병합·리뷰 재요청은 하지 않습니다.

## 남은 지적

- 동적으로 만든 셸 경로는 기존 정적 판정의 한계입니다. 해석하지 못한 관리 읽기는 확인을 요구합니다.

## 결론

Refs #630. 두 P1의 경로 변환을 모델 선언과 runner 경계 함수로 통합했다. gateway가 Linux여도 WSL 결과를 Windows runner에 전달한다.

## 바뀐 것

| 지적 | 수정 |
|---|---|
| P1 4280657526 | 공유 경로는 gateway OS와 무관하게 Windows 형식으로 저장하고 대상 runner의 `runtime_env`로 배포한다. 결과·이벤트·manifest·bundle이 같은 변환 함수를 쓴다. |
| P1 4280657538 | project·reference·upstream 경로를 함께 변환하고 setup에서 reference root와 프로젝트 root를 WSL 설정에 넘긴다. |

`RunnerPathFields`에 경로 필드와 값·목록·mapping 변환 방식을 선언했다. 모델 필드의 이름·타입 주석으로 누락을 검사한다. 프롬프트·자유 형식 결과·상대 산출 경로는 보존한다.

## 확인

- PR head `fe83ff8`: 두 P1 및 reference root 회귀 3건 실패 → 수정 후 통과.
- 관련 pytest 297 passed / 27 skipped. 산출물 타입·논문 스킬·라운드 기록 107 passed.
- 새 경로 필드 누락 감지, 원격 gateway의 이벤트, 실제 임시 project·reference 파일 읽기, manifest 원본 보존과 bundle 입력 목록 확인.
- 전체 테스트는 CI에서 실행한다. push 후 결과는 PR checks와 로컬 인계 기록에 남긴다.

## 남은 것

기존 WSL 설정은 정지 후 setup을 다시 실행해야 reference root를 받는다. PI 인스턴스·실제 WSL은 실행하지 않았다. 리뷰 요청·병합은 총괄에게 넘긴다.

## 결론

PR #641에서 #615의 Windows CI 시간 측정 test 두 개를 사건 순서 검증으로 바꿨습니다. 파일 쓰기를 Event로 막은 채 DB commit과 health 응답이 release 전에 끝나는지 확인합니다. Refs #606.

## 바뀐 것

- `test_a_stalled_file_write_does_not_hold_up_a_database_commit`: blocked `fsync`와 DB commit 완료 순서를 직접 확인
- `test_health_answers_while_the_runner_writes_through_a_slow_disk`: blocked `fsync` 중 health 응답이 먼저 도착하는지 직접 확인
- 벽시계 임계값과 probe 지연 비율 단언 제거

## 확인

- 수정한 두 test 20회 연속 통과: 매회 2 passed
- 관련 pytest: 30 passed
- `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과

## 남은 것

- push 뒤 CI와 전체 재실행 1회를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

Refs #634. PR #638의 staff 폴더 읽기 범위·자격 증명 이름 오판·활동 표시·auto 승인 설명을 수정했습니다.

## 바뀐 것

- Paper2Agent 읽기는 논문 스킬 빌드 작업의 해당 스킬 하위로 한정합니다. 일반 작업과 다른 스킬은 열지 않습니다.
- 자기 빌드 폴더와 저장 출력의 파일 읽기는 유지합니다. 저장 출력의 Bash 접근은 계속 묻습니다.
- `auth`·`oauth`·`token` 같은 상위 폴더명은 허용하고 자격 증명 파일명과 비밀 디렉터리는 보호합니다.
- 직원 활동은 한국어 요약, 원문은 자세히에 표시합니다. auto에서도 묻는 규칙을 설명합니다.

## 검증

- 수정 전: 기존 보안 테스트 3건과 새 pytest 8건 실패. 새 Node 2건 실패.
- 수정 후: 관련 pytest 643 passed/7 skipped. 실제 node.exe를 쓰는 웹 pytest wrapper 86 passed/1 skipped(Node 61개 포함). `test_claude_staff_home.py` 변경 없음.
- CI의 웹 상태 비교 실패 2건도 표시용 요약을 별도 필드로 분리해 수정했습니다. 기존 상태 비교 테스트는 그대로 통과합니다.
- 공개 검사·diff 검사 통과. 패치노트·목차 검사와 push 후 CI 결과는 PR에 기록합니다.
- 브라우저 실화면: UNVERIFIED. PI 인스턴스·HPC 사용 없음.

## 남은 지적

- 동적으로 조합한 셸 경로는 기존 정적 판정의 한계입니다.

## 결론

Refs #630. 봇 2차 지적 5건을 수정했다. Windows와 WSL은 같은 작업 폴더를 쓰며 승인·예산 정책도 함께 적용한다.

## 바뀐 것

| 지적 | 수정 |
|---|---|
| P1 4280129662 | 공유 drive의 경로 변환을 한 함수로 모았다. 선행 입력·작업 폴더·산출·bundle을 연결하고 Linux 전용 작업 폴더는 거부한다. 공유 root가 확인된 WSL만 bundle hostname 예외를 받는다. |
| P1 4280129675 | setup마다 Windows 정책을 복사하고 개인·통제 구역 경로를 변환한다. 변환 실패는 거부하며 기본 개인 경로 보호는 두 OS 모두 유지한다. |
| P1 4280232224 | 연결 확인 뒤에만 enabled를 저장한다. WSL이 끊기면 연결된 Windows 직원에게 배정한다. |
| P2 4280129685 | 설정 검증·up·background·직접 runner·WSL start에서 ID 중복을 거부한다. |
| P2 4280232232 | NAT fallback의 기본 포트를 ws 80·wss 443으로 나눈다. |

## 확인

- 원래 PR head `6fc5c69`: 새 회귀 22 failed / 2 passed. 수정 후 24 passed.
- 관련 pytest: 463 passed / 32 skipped. 시작 경로·CSO 추가 확인: 257 passed.
- 격리 실측: Windows 단계 → WSL 단계 파일 전달과 최종 bundle 수집 성공. 누락 0건, WSL 시험 runner exit 0. 실제 모델 호출 없이 CLI 파일 작업으로 확인했다.
- WSL manual 1,226자 → 1,506자. public·patch-notes·notes-index 검사는 기록 커밋과 함께 확인한다. CI는 push 뒤 PR checks에서 확인한다.

## 남은 것

- Windows 정책 변경 뒤 WSL을 멈추고 setup을 다시 실행해야 한다.
- 리뷰 요청·병합은 하지 않았다. 실측 원본과 테스트 로그는 clone의 `.pr-drafts/`에 보관한다.

## 결론

Refs #636. 인사팀(recruiter)을 비버에서 카피바라로 바꿨습니다. 편집자(editor)는 2D·3D 모두 비버로 남습니다.

## 바뀐 것

- 이름·배정: `카피바라 인사팀` / `capybara`. 메신저 표시는 `카피바라(인사팀)`입니다.
- 2D: 캐릭터 1 스타일의 둥근 몸·작은 귀·뭉툭한 코와 명함 소품을 추가했습니다.
- 3D: fallback 없이 가벼운 procedural 카피바라를 만들고, 비버 editor archetype을 분리했습니다.
- README 데모 GIF·still, 직원 표, 3D 캐릭터 표를 갱신했습니다. PR #639 본문에 데스크톱·폰 전후 화면을 붙였습니다.

## 확인

- 새 test `tests/web_recruiter_capybara.cjs`는 main에서 실패하고 이 branch에서 통과합니다. 이름·매핑·정규직 동물 고유성·2D/3D 분기를 검사합니다.
- Node test 59개 파일 모두 통과. 관련 pytest 99 passed, 1 skipped.
- 2D visual test 32 views가 기존 baseline과 같아 baseline은 바꾸지 않았습니다.
- 데스크톱·폰은 빈 임시 포트 4개에서 캡처했고 8787·55921은 쓰지 않았습니다.
- `scripts/check_public.sh` 통과.

## 남은 것

- GitHub CI 결과를 확인합니다.

## 결론

Refs #634. labhq 관리 폴더의 단순 읽기에 생기던 승인을 줄이고 도구 카드에 작업·위치·목적·규칙·위험·승인/거절 결과를 표시합니다.

## 바뀐 것

- 경로: 관리 폴더 읽기만 예외로 처리합니다. 개인 상위 폴더는 보호하며 자격 증명·개인 하위 경로·링크/상위 경로 우회는 계속 차단합니다. 작업 자신의 파일 쓰기 권한은 유지합니다.
- 설명: 복합 읽기 명령을 나눠 설명하고 빌드 단계·논문 제목을 붙입니다. 템플릿이 해석하지 못한 명령만 통역 대상으로 넘깁니다.
- 이름: 실제 Task 담당은 recruiter였습니다. 이름을 첫 단어로 줄여 편집자와 모두 ‘비버’로 보이던 표시를 역할까지 보이게 고쳤습니다.
- 화면: 2.5D·3D 공통 카드와 CLI가 같은 설명을 씁니다. 원문은 자세히/`approvals --details`에 남깁니다.

## 확인

- 원본 main `5d35959`에서 새 회귀 22 failed/28 passed → branch 50 passed. Node 카드·직원 이름 회귀도 main에서 실패했습니다.
- 관련 pytest 683 passed/7 skipped, Node 7개 파일 통과. 이후 추가한 장착 스킬·플러그인 읽기 4건도 통과했습니다. 전체 suite는 CI에 맡깁니다.
- `scripts/check_public.sh`·diff 검사 통과. patch-notes·notes-index와 CI 결과는 PR에 기록합니다.
- 브라우저 실화면 확인: UNVERIFIED. PI 인스턴스에서 시운전하지 않았습니다.

## 남은 지적

- 셸에서 실행 중 만들어지는 경로는 기존처럼 정적 경로 판정의 한계입니다. 읽기 예외는 정해진 단순 문법만 처리합니다.

## 결론

PR #635에서 데스크톱 2.5D 패널 경계를 마우스와 키보드로 조정하게 했습니다. 더블클릭하면 기본 크기로 돌아가고, 데모·라이브·instance별로 저장합니다. 폰과 3D는 그대로입니다. Refs #633.

## 바뀐 것

- 사무실–Command Center 세로 경계, 작업 영역–직원 시트 가로 경계
- 최소·최대 크기, 화살표, 접근성 이름, localStorage 실패 시 기본 배치
- [데스크톱 전](../design/2026-10-15/resizable-panels/desktop-before.png) · [후](../design/2026-10-15/resizable-panels/desktop-after.png) / [폰 전](../design/2026-10-15/resizable-panels/phone-before.png) · [후](../design/2026-10-15/resizable-panels/phone-after.png)

## 확인

- 새 Node test는 main 소스에서 실패하고 branch에서 3건 통과
- 관련 pytest 84 passed, 1 skipped
- Node 전체, 공개 저장소 검사, 화면 기준선 32 views 통과

## 남은 것

- CI 결과는 push 뒤 확인합니다. 리뷰 요청과 병합은 하지 않습니다.

## 결론

Refs #632. 새 세션에서는 검토를 마친 완료·실패·취소 요청을 작업판과 제목 선택에서 빼고 `지난 요청`으로 접습니다. 검토 대기 요청은 그대로 보입니다.

## 바뀐 것

- 2.5D·3D·CLI가 현재 사무실과 지난 요청을 같은 기준으로 나눕니다.
- 머리글은 이번 세션 비용만 표시하고 전체 누적 비용은 도움말과 지난 요청 요약에 남깁니다.
- 요청 기록·검색·보고서·저장 결과는 지우지 않습니다.
- `docs/manual.md` 웹 사무실 설명은 146,796자에서 146,847자로 51자 늘었습니다.

## 확인

- 새 Node·pytest test는 main에서 실패하고 이 branch에서 통과했습니다.
- 관련 pytest 72 passed, 1 skipped. `tests/*.cjs` 전체 통과.
- 데스크톱 1440px·폰 390px 전후 화면을 `docs/status/assets/clean-office-open/`에 남겼습니다.

## 남은 것

- GitHub CI 결과는 PR을 연 뒤 확인합니다.

## 결론
Refs #621. PR #631. `alphagenome_variant`가 GRCh38 변이를 권장 scorer로 평가하고 한국어 요약과 원 수치 표를 돌려줍니다.

## 바뀐 것
- `score_variant` + `RECOMMENDED_VARIANT_SCORERS` + `tidy_scores`. 활성도 전용 scorer는 제외하며 절대 차이에는 증가·감소를 붙이지 않습니다.
- 기본·최대 1MB, 변이 10개, allele 1,000 bp. 조직/ontology 조회 후 필터, 답 상위 20행(최대 100), 파일·캐시 상위 1,000행.
- SDK·API v1·ALL_FOLDS·scorer 설정을 기록하고 캐시 키에 포함. 키는 기존 파일에서만 읽습니다. 비상업 API 조건·임상 근거 아님 표시.
- `variant_effect` 점검표·manual·선택 extra·doctor 안내 갱신. 기존 구간 도구 유지.

## 검증
- 새 mock 테스트 21개: main `38c0f37` worktree에서 21 failed → branch에서 통과.
- 관련 pytest 114 passed, 1 skipped(기존 numpy 선택 테스트). SDK·API는 mock, 실제 키·API 사용 없음.
- `scripts/check_public.sh`, `scripts/notes_index.py --validate`, `scripts/integrations.py --check` 통과.
- 전체 suite는 CI. `scripts/patch_notes.py check --base origin/main --head HEAD` 통과.

## 남은 지적
- 실제 API 실측은 지시 범위 밖. 서버 build ID는 SDK가 공개하지 않아 미확인으로 기록합니다.
- SDK가 일부 RPC 오류를 재시도하므로 10개 변이는 최대 50회 RPC 시도가 될 수 있습니다.
- 출처·SDK 계약: [참고 기록](https://github.com/ehojune/bioinfo-team-3d/blob/alphagenome-variant-score/docs/reference/alphagenome-variant.md).

## 결론

Refs #612. 1단계: `setup-wsl`로 로컬 WSL runner를 준비하고 Windows gateway에 연결합니다. 실행 위치 카드는 2단계로 남깁니다.

## 바뀐 것

- `setup-wsl --check`: 배포판·Java·Nextflow·Docker·bioinfo-agent·labhq·등록·연결을 항목별 표시.
- setup: 사용자 venv·editable 설치, 없는 Nextflow만 설치, Linux 전용 state·설정·작업 폴더. 관리자 명령은 안내만.
- 토큰은 stdin과 600 설정 파일로 전달. Windows `up/down`과 `wsl-runner start/stop`은 기존 supervisor를 재사용.
- bioinfo-agent를 WSL에 배정하고 Windows 재연결로 덮지 않음. doctor WSL 절과 CSO 도구 안내, manual 추가.

## 확인

| 항목 | 결과 |
|---|---|
| 새 mock test 12개 | main `33e9603`: 12 실패(동작 assertion 4, 새 API 부재 8); branch: 12 통과 |
| 관련 pytest | 409 passed, 3 skipped; 전체 suite는 CI |
| Node | `tests/web_agent_capabilities.cjs` 통과 |
| 실제 setup·check | Ubuntu 22.04, 별도 설정·state·임시 포트의 Windows gateway에 `lab-wsl` 연결; 8항목 모두 ok |
| 반복·재시작 | setup 재실행 성공, `down → up` 뒤 WSL 재연결 |
| 도구 | Java 17.0.20.1, Nextflow 24.10.5, Docker 29.7.1 |
| Nextflow + Docker | Ubuntu 22.04 컨테이너의 HELLO 1 task, COMPLETED / exit 0, `hello.txt=hello`, report·trace 생성 |
| 토큰 | Linux 폴더 700·설정 600, Linux 로그 11개와 Windows 로그에서 토큰 0건 |

실측은 mock 직원으로 runner 연결을 확인하고 Nextflow를 WSL에서 직접 실행했습니다. WSL Claude CLI·직원 로그인은 없어서 bioinfo-agent의 LLM 실행은 검증하지 않았습니다. 초기 Alpine 컨테이너에는 Bash가 없어 실패했고 Ubuntu 이미지로 완료했습니다. PI 인스턴스·PI venv·보호 포트·HPC·관리자 명령은 사용하지 않았습니다.

## 남은 것

- WSL Claude CLI와 직원 전용 로그인 준비. setup은 자격 증명을 입력하지 않습니다.
- CI 결과는 PR checks에서 확인. 리뷰 요청·병합은 하지 않습니다.
- 로컬 실측 근거: `.pr-drafts/live-evidence.json`, `hello.log`, `live-setup-wsl-check.log`, `test-main.log`, `test-final.log`(모두 커밋 제외).

## 결론

Refs #609. PR #629 (`report-paper-format`). 운전 기록과 과학 보고서를 분리하고 일반·연구·solo 보고서의 기본 형식을 논문 순서로 바꿨습니다.

## 바뀐 것

- 과학 본문: 제목·요약·서론·방법·결과·논의·결론·참고. 별도 과학 부록에는 점검표·리뷰·가정만 둡니다.
- 운전 정보는 `execution_record`·`execution_record.md`로 이동합니다. 라운드에는 과학 본문 대신 묶음·게시 정리본·Nextflow 산출 링크를 남깁니다.
- 쓰기 가능한 최종 보고서 단계가 graphical abstract를 만들고 CSO는 첫 그림으로 링크합니다. 기존 자동 흐름도와 #608 PDF 정책은 유지합니다.
- `edition_sources` 출처 추출을 참고 절에 재사용합니다. PDF·편집자·검색·verify·CP4·웹·CLI 호환을 확인했습니다. 옛 CP4 초안의 hash는 유지합니다.

## 확인

- 새 pytest 11건: main `33e9603`에서 11 failed → branch에서 11 passed.
- 관련 pytest: 중복 제외 1,119 passed, 5 skipped. Node 63개 파일 통과. 전체 pytest는 CI에 맡깁니다.
- plan/research prompt hash는 #609 규칙 추가에 맞춰 갱신했습니다. 기존 검사는 내용·한도·가림을 유지하고 운전 정보의 저장 위치만 바꿨습니다.
- manual: 143,262 → 143,367자(+105). 공유 STATUS·패치노트 목차는 수정하지 않았습니다.

## 남은 것

- push 후 CI는 실행 중이며 최종 결과는 PR에서 확인합니다. 브라우저 시각 확인은 UNVERIFIED이며 웹 검증은 Node 테스트입니다.
- 리뷰 요청·대기·병합은 하지 않습니다.

## 결론

Refs #607. 시운전 정리본 게시가 fine-grained 토큰 때문에 실패한 뒤에는 다시 시도할 길이 없었습니다. 이제 `labhq storage <요청 id> --republish`가 로컬 정리본 그대로 게시 카드를 다시 엽니다. 토큰 권한 오류는 이유와 할 일을 쉬운 말로 알리고, `labhq doctor`가 미리 경고합니다.

원인: 게이트웨이 토큰이 선택한 저장소만 허용하는 fine-grained 토큰이라 새 저장소를 만들 수 없습니다(`POST /user/repos`가 403). 저장 상태가 `failed`가 되면 `result_storage.redraft`가 거부해 되돌릴 방법이 없었습니다.

## 바뀐 것

- **다시 올리기**: `labhq storage <요청 id> --republish`, `POST /api/requests/{id}/storage/republish`. 상태가 `failed`인 정리본을 편집자 없이 로컬 초안 그대로 게시 카드로 다시 엽니다. `card_lock`을 씁니다. 이미 게시한 정리본, `failed`가 아닌 정리본, 통제 데이터 요청, 검토 완료 전 요청, 안 끝난 요청, 올리는 중인 요청, 로컬 초안이 없는 요청은 409로 거부합니다.
- **다시 만들기**: `--redraft`가 `failed`도 받습니다. `--republish`와 같이 쓸 수 없습니다.
- **권한 오류** (`result_storage.upload`): 새 저장소 만들기 403은 토큰이 저장소를 만들 수 없다는 말과 `gh repo create <소유자>/<이름> --private`(공개로 골랐으면 `--public`), 토큰 저장소 접근에 추가(Contents: Read and write), `--republish`를 알립니다. 기존 저장소 쓰기·조회의 403·404는 토큰이 그 저장소에 파일을 쓸 수 없다는 말과 추가 방법을 알립니다. 이유는 `result_storage.warning`에 저장합니다. rate limit 403·5xx 등은 기존 일반 문구에 재시도 명령을 붙입니다. 토큰과 GitHub 응답 본문은 경고·로그에 넣지 않습니다.
- **doctor**: 토큰이 fine-grained(`github.token_env` 변수의 접두사만 비교)이면 한 줄 경고합니다. 네트워크를 쓰지 않고 토큰은 출력하지 않습니다.
- `docs/manual.md` 저장 위치·편집자 절.

## 확인

- 새 test 23건(`tests/test_publish_retry_607.py`)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다. mock editor·mock transport만 씁니다.
- 기존 test 한 줄 수정: `test_redraft_refuses_published_and_not_yet_drafted_editions`가 `failed`도 거부하던 단언에서 `failed`를 뺐습니다. 동작이 바뀐 부분입니다.
- 관련 기존 test 180건 통과(`test_result_storage.py`의 runner를 띄우는 1건은 이 PC에서 빼고 CI에 맡김). `scripts/patch_notes.py check`, `scripts/check_public.sh` 통과.

## 남은 것

- 빈 저장소(첫 커밋 없음)에 contents API로 `branch: main`을 지정해 쓰면 404가 날 수 있습니다. 실제 GitHub를 부르지 않아 확인하지 못했습니다. `gh repo create`에 `--add-readme`를 주면 피합니다.
- 웹 사무실에는 재시도 버튼이 없습니다(CLI·API만).
- 시운전 정리본은 PI가 토큰의 저장소 접근에 새 저장소를 추가한 뒤 `labhq storage <요청 id> --republish`로 다시 올립니다.

## 결론

Refs #621 (A, C). 사람 GRCh38의 `vep`가 AlphaMissense를 함께 받아 변이마다 점수와 분류를 쉬운 한국어로 돌려주고, 변이 해석 요청에 붙는 `variant_effect` 점검표(6항목)를 더했습니다. B(AlphaGenome 변이 점수)와 D(사전 계산 atlas)는 이 PR에 없습니다.

## Ensembl REST 확인 (2026-10-15, 읽기 전용 호출 7회)

- 요청: `POST https://rest.ensembl.org/vep/human/region?canonical=1&hgvs=1&mane=1&AlphaMissense=1`, 본문 `{"variants": ["7 140753336 labhq0 A T . . ."]}` (BRAF V600E, GRCh38). Ensembl release 116(`GET /info/data`).
- 응답 필드: `transcript_consequences[].alphamissense = {"am_pathogenicity": 0.9927, "am_class": "likely_pathogenic"}`. missense인 transcript에만 붙습니다(BRAF 24개 transcript 중 13개). MANE Select `ENST00000646891`(`mane_select: NM_004333.6`, `canonical: 1`)도 같은 값입니다.
- 같은 옵션이 `/vep/human/hgvs`(`ENST00000646891.2:c.1799T>A`)에서도 같은 값을 돌려줍니다. TP53 R175H(`17 7675088 C T`)는 0.9857 likely_pathogenic, BRCA1 frameshift(`17 43124027 ACT A`)는 transcript 70개 모두 `alphamissense` 없음입니다.
- 모르는 옵션(`NotARealPlugin=1`)과 사람이 아닌 종(`mus_musculus`)은 HTTP 200에 필드 없음입니다. 서버가 옵션을 조용히 무시하므로 필드가 없다는 것만으로 "점수 없음"을 단정할 수 없습니다.
- GRCh37 서버(`grch37.rest.ensembl.org`)도 점수를 주지만 분류 이름이 `pathogenic`(옛 이름)입니다. 지시대로 GRCh38 사람에만 요청하고, 옛 이름도 읽도록 번역표에 넣었습니다. GRCh37을 켜려면 `VEP_PLUGIN_ASSEMBLIES`에 더합니다.
- 분류 기준(Ensembl 플러그인 문서): `likely_benign` < 0.34, `likely_pathogenic` > 0.564, 그 사이 `ambiguous`.

## 바뀐 것

- `labhq/tools/annot.py`: 사람 GRCh38 VEP 요청에 `AlphaMissense=1`. 옵션이 캐시 키(`options`)에 들어가 이전 답이 완전한 답으로 다시 쓰이지 않습니다. 요약의 `alphamissense`는 MANE → canonical → 첫 transcript 순으로 고른 transcript의 점수만 읽습니다(`AlphaMissense: 0.99 (병원성 가능성 높음)`). 선택된 transcript에 점수가 없으면 `점수 없음 (이 transcript)`으로 두고, 다른 transcript의 점수는 `alphamissense_other`에 분리합니다. missense가 아니면 `해당 없음`입니다. Ensembl이 플러그인 요청을 거부하면(400·500) 플러그인 없이 한 번 더 보내 나머지 VEP 결과를 살리고 그 항목은 `unavailable`로 적고 캐시하지 않습니다.
- 출처 기록: 결과 `source.additional_sources`(와 `annotation_queries.jsonl`·결과 파일)에 AlphaMissense의 라이선스·이용 조건·인용, 결과 `note`에 한 줄 경고.
- `labhq/tools/annot_mcp.py`(도구 설명), `labhq/orchestrator/cso.py`(roster 한 줄).
- `variant_effect` topic: `output_types.yaml`, `topic_checklists.yaml`(6항목), `edam_map.yaml`(null, 맞는 EDAM topic이 하나로 정해지지 않아 local), `edam_subset.yaml`, `docs/vocabulary.md`(생성), `docs/reference/topic_checklists_sources.md`(PMID 9건, 2026-10-15 PubMed 대조).
- 의도해서 바꾼 pin: `test_output_types.py` `PLAN_PROMPT_SHA`, `test_output_types_research.py` `RESEARCH_PROMPT_SHA`(topic 규칙에 항목이 하나 늘어서), `test_output_vocab.py`의 key·topic 개수. 모두 #621 주석.
- `docs/manual.md`(공개 자원 조회 도구, topic 점검표).

## 라이선스 (지시와 다른 점)

지시는 "CC BY-NC-SA 4.0, 비상업"이었으나 원자료는 다릅니다. DeepMind README(`google-deepmind/alphamissense`, 2024-03-13 "Update AlphaMissense predictions database license" 이후)와 Ensembl 플러그인 문서(`VEP_plugins/AlphaMissense.pm`, release/116) 모두 예측값을 CC BY 4.0이라 적습니다. 플러그인 문서는 데이터 이용이 Google Cloud Platform 약관을 따른다고 덧붙입니다. 그래서 기록에는 CC BY 4.0(출처 표기 필요)과 "2024-03-13 전 배포본은 CC BY-NC-SA 4.0(비상업)"을 함께 적었습니다. PI가 비상업 조건을 그대로 두라고 하면 `ALPHAMISSENSE_SOURCE`의 문구만 고치면 됩니다. 임상 근거가 아니라는 고지는 DeepMind의 "임상용으로 검증·승인되지 않았다"를 따랐습니다.

## 확인

- 새 test 30건(`test_annot_alphamissense.py` 23, `test_variant_effect_topic.py` 7)과 고친 기존 assertion 1건(`test_annot.py`의 VEP 질의 문자열)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다(main 쪽은 labhq·docs 변경을 stash해 돌림).
- 관련 pure test 1,200여 건(annot, vocab, checklist, prompt, semantics) 통과. PI 시험이 돌고 있어 프로세스를 띄우는 test(MCP stdio 서버, runner 연결, 하위 `python` 호출)는 로컬에서 돌리지 않았고 CI에 맡겼습니다.

## 남은 것

- B: AlphaGenome `score_variant` 전환. D: 사전 계산 atlas.
- AlphaMissense 데이터 판본은 REST가 알려 주지 않아 Ensembl release로만 기록합니다. 표 자체(Zenodo·GCS)를 받아 쓰는 대안은 쓰지 않았습니다.

## 결론

Refs #620. OKF Loremaster를 읽기만 하고 검토해 참고 자료에 올렸습니다. 코드 변경은 없고, 채택할 일은 후속 issue 두 건(#622, #623)으로 넘겼습니다.

## 바뀐 것

- `docs/references.md`·`docs/references.en.md`: OKF Loremaster 행(검토만 했고 코드는 가져오지 않았다고 표시).
- `docs/reference/okf-loremaster.md`(새 노트)와 `docs/reference/README.md` 색인 한 줄: 프로젝트 개요, 가져갈 것 네 건의 매핑, Apache-2.0 조건.
- 후속 issue: #622(원문 대조 검증 이식, 이어서 선행 연구 조사 적용), #623(벤치 정답 값 누출 검사).

## 확인

- 대상 저장소는 파일만 읽었고 실행·설치하지 않았습니다(커밋 `ead3819`).
- `scripts/check_public.sh` 통과, 바꾼 문서의 상대 링크 4개 파일 모두 깨진 곳 없음.
- 코드 변경이 없어 test는 돌리지 않았습니다.

## 남은 것

- README 본문에는 References 절이 없어서 README '더 읽기'가 연결하는 `docs/references*.md`에 넣었습니다.
- 코드를 실제로 옮기면 그 행을 "코드를 옮겼다"로 고칩니다(#622).

## 결론

`labhq up`이 역할(gateway, runner)마다 감독 프로세스를 하나씩 띄웁니다. 감독이 gateway·runner를 자기 자식으로 시작해 끝까지 기다리므로, 두 프로세스의 부모 PID는 사는 동안 재사용될 수 없습니다. 감독은 역할이 끝난 시각과 종료 코드도 남깁니다. #589에서 얻지 못하던 단서입니다. 시운전이 끝난 뒤 배포합니다. Refs #589.

## 남은 경로

#597은 labhq 자신의 종료 경로만 고쳤습니다. `up`이 끝나면 launcher는 죽은 `up`을 부모로 기록한 채 남습니다. labhq 밖의 코드(test의 트리 종료, 다른 도구)가 부모 PID만 보고 트리를 끄면, 그 번호를 물려받은 프로세스를 따라 gateway·runner가 함께 죽습니다. 10-15 10:38~10:48에 #597을 배포한 뒤에도 같은 모양으로 꺼졌습니다. 밖의 코드는 고칠 수 없으니, 그 PID가 다른 프로세스에 배정되지 않게 합니다.

## 선택한 설계

(a) 감독 프로세스를 택했습니다. 부모가 살아 있으면 PID는 재사용될 수 없고, 종료 코드도 같은 자리에서 남습니다. (b) `PROC_THREAD_ATTRIBUTE_PARENT_PROCESS`는 부모를 다른 오래 사는 프로세스로 지정하는데, 그 프로세스가 죽으면 같은 문제가 돌아옵니다. STARTUPINFOEX를 ctypes로 직접 만들어야 하고 권한이 없을 때의 경로도 갈라져, 단순하지 않아서 뺐습니다.

(a)만으로는 한 곳이 비어 있어 하나를 더했습니다. 감독 자신의 부모는 죽은 `up`입니다. 그 PID가 재사용돼 트리 종료가 거기서 시작하면 감독이 자식으로 잡히고, gateway·runner가 따라 죽습니다. 그래서 감독은 시작할 때 조상 프로세스 4개의 handle을 열어 둡니다(`proc_tree.pin_ancestors`). 열린 handle이 있는 동안은 끝난 프로세스의 PID도 반환되지 않습니다. #597의 종료 helper가 이미 쓰는 성질입니다. `up`은 감독이 이 일을 하고 역할을 기록할 때까지(최대 20초) 끝나지 않습니다. 조상 중 하나인 `up`이 살아 있어야 그 PID를 붙들 수 있습니다.

감독은 역할마다 하나입니다(gateway용, runner용). 한쪽만 다시 띄우는 `up`과, runner만 끄고 gateway는 두는 웹 종료(`down --keep-gateway`)가 있습니다. 감독 하나가 둘을 맡으면 시작·종료 명령을 감독에게 보내는 통로가 따로 필요합니다. 역할마다 하나면 `up`·`down`이 지금처럼 프로세스를 띄우고 끄는 것으로 끝납니다.

## 바뀐 것

- 새 `labhq/supervisor.py`: 조상 handle 고정, 역할 시작과 `labhq-<role>.pid` 기록(이전과 같은 형식), 대기, `child_exit` 기록(역할 PID·종료 코드·Windows 상태 hex·POSIX signal·가동 시간·종료 요청 여부)을 `<role>-supervisor-diagnostics.jsonl`에 남김. 역할이 시작되지 못하면 `child_start_failed`.
- `supervisor.restart`(설정, **기본 꺼짐**): 켜면 스스로 끝난(종료 코드 0이 아닌) 역할을 다시 시작합니다. 5초에서 시작해 두 배씩 최대 120초 기다리고, 600초 안에 5번을 넘기면 그만둡니다(`restart_giving_up`). 종료 코드 2는 labhq 자신의 시작 거부(설정 거부, #613의 다른 설정 소유 기록)라 같은 거부가 되풀이되므로 다시 시작하지 않습니다(`restart_skipped`). `down`·웹 종료가 먼저 남기는 표시 파일(`labhq-<role>.stopping`)이 있으면 다시 시작하지 않습니다.
- `labhq/cli.py`: `up`이 감독을 띄우고 `labhq-<role>-supervisor.pid`로 기록한 뒤 역할 기록을 기다립니다. 감독이 먼저 끝났거나 20초 안에 기록하지 않으면 감독을 정리하고 exit 2. 감독이 다시 시작하는 중인 역할에는 감독을 하나 더 띄우지 않고 응답을 기다립니다. `down`은 감독을 먼저 끝낸 뒤 역할에 남은 것을 끝내고(그 사이 끝나 PID가 재사용됐을 수 있어 기록을 다시 읽음), 감독 신원을 확인할 수 없으면 아무것도 끄기 전에 exit 2입니다. `status`·`doctor`는 감독 PID와 역할의 마지막 종료(시각·코드·PID·가동 시간·`down` 요청 여부)를 보여 줍니다. 감독 기록은 있는데 감독이 없거나 신원을 읽을 수 없고(접근 거부, 다른 설정 파일의 기록) 역할만 살아 있으면 warn입니다. 이 변경 전에 시작한 역할은 "감독 없음(감독 도입 전 방식으로 시작됨)"으로 알립니다.
- #614(인스턴스 identity)와 함께: `_up_unlocked`는 #614의 검사(시작 전 `check`, 응답마다 `verify`, 확인된 gateway만 기록)를 그대로 두고 감독 쪽을 얹었습니다. gateway·runner의 소유 기록과 identity는 프로세스가 `-c`로 받은 설정에서 스스로 계산하고, 감독은 `up`과 같은 `-c`를 넘기며 역할 기록에 이 설정의 경로를 씁니다. 그래서 감독이 따로 넘기거나 기록할 것은 없고, `up`의 확인은 전과 같이 맞습니다. 감독이 다시 시작하는 중인 역할의 응답에도 `verify`를 겁니다.
- `labhq/gateway/shutdown.py`: 웹 종료가 gateway를 끄기 전에 종료 표시를 남깁니다.
- `labhq/settings.py`, `config/labhq.example.yaml`(패키지 사본 포함), `docs/manual.md`: `supervisor` 설정과 설명.
- `labhq/proc_tree.py`: `pin_ancestors`.
- `tests/test_no_tree_kill.py`(가드): tests/와 labhq/의 모든 텍스트 파일(주석·docstring 포함)에서 `taskkill` 뒤 같은 문장의 `/T`·`-T` 스위치를 찾습니다. 여러 줄 인자 목록과 `/F/T`도 잡습니다. 점검 결과 실제 `/T` 호출은 tests/에도 labhq/에도 없었고, 설명 주석·docstring 8곳만 걸려 말을 바꿨습니다. 부모 PID만 보고 트리를 끄는 코드도 없었습니다(`adapters/base.py`의 `_idle_process_tree`는 읽기 전용).

## 확인

- 새 unit test 79건(`test_supervisor.py` 25, `test_supervisor_cli.py` 26, `test_supervisor_up_identity.py` 4, `test_pin_ancestors.py` 7, `test_no_tree_kill.py` 17). 가짜 프로세스·가짜 시계와 실제 PID와 겹치지 않는 값만 쓰고, 실제 프로세스 primitive에 닿으면 실패하게 막아 두었습니다. main(병합 전)에서는 모두 실패합니다(가드는 8곳이 걸리고, 나머지는 `labhq.supervisor`·`pin_ancestors`·`latest_child_exit`가 없어 실패).
- 기존 test 2건을 새 동작에 맞췄습니다: `test_cli_launch.py`의 up·down 기록 test(이제 감독 기록을 확인), `test_process_diagnostics.py`의 로그·버퍼링 test(감독의 스트림을 확인, 역할의 스트림은 `test_supervisor.py`).
- 실제 프로세스 test 6건은 CI 전용입니다(`tests/test_supervisor_process.py`): 실패한 역할의 종료 코드 기록, 재시작과 상한, 밖에서 죽인 역할은 재시작하고 표시된 정지는 재시작하지 않음, POSIX signal 전달, **Windows: 감독이 끝난 부모의 PID를 쥐고 있어 그 PID가 열린 채 남음**, 진짜 gateway가 감독의 자식으로 돌고 `down`이 둘 다 끝냄. 기존 `test_web_shutdown_process.py`도 이제 감독 아래의 진짜 gateway를 씁니다. Windows `clean-install` job이 진짜 `up`→`down`을 돕니다.
- 로컬에서는 프로세스를 띄우거나 끄는 test를 돌리지 않았습니다(PI 시운전 중). 병합 전에 그 밖의 121개 test 파일 3600건, 병합 뒤에는 #614의 `test_instance_guard.py`·`test_init_defaults.py`와 예시 설정 일치 test를 포함해 449건 통과, `scripts/check_public.sh`·`scripts/patch_notes.py check` 통과. 읽기만 하는 확인으로 `pin_ancestors()`를 이 PC에서 한 번 불러 조상 4개를 쥐었다 놓았습니다.

## 위험과 남은 것

- 배포 뒤 `down`→`up`을 한 번 해야 감독 아래로 들어갑니다. 그 전에 시작한 gateway·runner는 이전 방식 그대로입니다.
- 감독 자체가 밖에서 강제 종료되면 역할은 다시 죽은 번호를 부모로 가진 채 남습니다. `status`·`doctor`가 warn으로 알리지만 막지는 못합니다. 감독이 죽을 때 역할을 함께 끝내는 job 묶음은 두지 않았습니다.
- 조상 handle을 못 열면(권한, 이미 끝난 부모) 역할은 그래도 시작하고 이유를 `parents_pinned` 행에 남깁니다. 그때는 보호가 없습니다.
- Windows `down`은 트리를 말단부터 끄므로 감독이 `child_exit`를 쓰기 전에 끝날 수 있습니다. POSIX는 기록합니다.
- 프로세스가 역할마다 늘어납니다(감독 launcher와 interpreter). 명령줄로 gateway·runner를 찾는 도구에는 `labhq.supervisor`도 보입니다.
- 10-15의 정지가 PID 재사용 때문이라는 증거는 아직 가설입니다. 다음에 꺼지면 감독의 `child_exit`가 종료 코드를 남깁니다.

Refs #589

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 결론

Refs #608. #611 리뷰의 P2 세 건을 고쳤습니다. 게시 PDF는 PI가 검토한 정리본이 담을 수 있는 그림만 보여 주고(크기 제한만 풉니다), 보고서의 graphical abstract는 첫 그림이 되며, `labhq verify`는 그려 넣을 abstract 자리를 셉니다.

## 바뀐 것

- **PDF 그림 자격** (`labhq/result_storage.py`): `edition_assets()`의 판정을 `edition_candidates()`로 꺼냈습니다(manifest가 `copied`, `steps/<id>/outputs/` 경로, 정리본이 담는 형식, 가림이 필요 없는 이름). 정리본 복사는 여기에 크기 한도(파일당 1 MB·합계 5 MB)를 더하고, `pdf_report_text()`는 크기 한도 없이 같은 목록만 링크로 남깁니다. 미보고 묶음 파일, manifest에 없는 파일, gif 같은 형식은 PDF에서도 `*(그림 생략: 설명)*`입니다. 기존 `_BundleFigures`(일반 파일이면 통과)는 없앴습니다.
- **abstract 위치** (`labhq/report_abstract.py`, `abstract_first`): 보고서가 링크한 유효한 abstract가 첫 그림이 아니면 첫 제목(코드 밖) 아래 첫 그림으로 옮깁니다. 캡션·제목·대상은 그대로입니다. PDF(`build_report_html`)와 정리본 `report.md`·README 발췌에 적용하고, 묶음의 `report.md`는 건드리지 않습니다.
- **verify** (`labhq/report_figures.py`, `bundle_figure_check`): 묶음에 있는 자체 abstract가 없으면 결과 그림 한도를 10장으로 둡니다. 자체 abstract가 있으면 11장 그대로입니다. 메시지는 두 경우를 따로 적습니다.
- `docs/manual.md`(그림 정책, abstract, 정리본 PDF 문단).

## 확인

- 새 test 8건(`tests/test_pdf_figure_review_fixes.py`)은 main 코드에서 모두 실패하고 이 branch에서 통과합니다. main 쪽은 새 함수의 import를 비운 채 돌려 동작으로 실패하는 것을 확인했습니다.
- 의도해서 고친 기존 assertion 3건: #611에서 미보고 그림이 PDF에 남는 것과 11장 문구를 고정했던 `test_pdf_figure_policy.py` 2건과 `test_result_storage_pdf.py` 1건.
- 관련 Python test 1139건과 Node test 58개 파일 통과, `check_public.sh` 통과.

## 남은 것

- 없음.

## 결론

Refs #613 (PI 결정 C). 새 `labhq init` 설정이 PI가 시험한 동작으로 시작하고, bioinfo-agent 위치를 찾거나 묻고, `doctor`와 `up`이 설정이 다른 인스턴스를 쉬운 한국어로 경고합니다. 기존 설정은 바꾸지 않습니다.

## 바뀐 것

**init 기본값** (`config/labhq.example.yaml`과 패키지 사본, `labhq/init_defaults.py`)

| 설정 | 새 설정의 값 | 전에는 |
|---|---|---|
| `research.enabled` · `research.evidence_checkpoint` | true · true | false · false |
| `research.active_packs` | `bulk_tumor_normal@3`, `single_cell_de@3` | 빈 목록 |
| `semantics` | `{mode: ab, actions: confirm}` | `off` |
| `plan.declare_output_types` | true | false |
| `policy.data_zones` | `runner.workspace_root`(인스턴스는 자기 폴더)를 `internal` 구역으로 추가 | 없음 |

- 마지막 줄은 PI 설정에 들어 있는 항목입니다. 의미 모델은 `public`·`internal` 구역 안의 산출물만 hash를 재므로(manual) 없으면 `ab`의 재사용 후보가 비게 됩니다.
- 기준 목록 `REFERENCE_BEHAVIOUR`은 설정 경로와 값 6개뿐이고 토큰·경로·이름이 없습니다. 예시 설정 두 사본과 `init` 결과가 어긋나면 `tests/test_init_defaults.py`가 실패합니다.
- 일부러 켜지 않은 것: `policy.bioinfo_agent.pipeline_pr`(동의를 묻는 값), `dev_log.repo`, `BIOINFO_AGENT_DIR`(PC마다 다름), `hpc.scheduler`(init이 감지).

**bioinfo-agent 연결** (`labhq/bioinfo_link.py`, `init_wizard.py`)
- 저장하는 경로는 상대로 받아도 절대 경로입니다(직원 task는 자기 작업 폴더 기준으로 상대 경로를 읽습니다). `doctor`는 상대 경로로 적힌 설정을 warn합니다.
- 찾는 순서: `--bioinfo-agent PATH` → 기존 `BIOINFO_AGENT_DIR` → `D:/bioinfo-agent`·`C:/bioinfo-agent`·`~/bioinfo-agent`·`~/Desktop/bioinfo-agent` → WSL의 `/mnt/d/bioinfo-agent`·`/mnt/c/bioinfo-agent`(#612).
- 찾으면 `--yes`는 바로 연결하고 대화형은 확인합니다. 못 찾으면 대화형은 경로를 묻고 `--yes`는 묻지 않습니다.
- 끝내 없으면 설정에 넣지 않고 "bioinfo-agent 연결 안 됨 — …"과 고치는 법을 출력합니다. `doctor`의 `bioinfo-agent` 행이 같은 말로 warn을 냅니다(폴더에 plugin이 없어도 warn).
- `--bioinfo-agent`로 준 폴더에 plugin이 없으면 init이 멈추고 아무것도 쓰지 않습니다. 경로는 출력하지 않습니다.

**다른 인스턴스 경고** (`labhq/instance_guard.py`, `cli.py` up, `gateway/server.py` health, `doctor.py` `instance` 행)

실행 중인 gateway와 runner는 시작할 때 **정체**를 적습니다: 시작한 설정 파일과 `gateway.state_dir`·`runner.state_dir`·`runner.id`·포트의 지문(해시, 비밀값 없음). 기록은 gateway state 폴더의 `labhq-gateway-owner.json`, runner state 폴더의 `labhq-runner-owner.json`이고, gateway는 `/api/health`의 `identity`(해시만, 경로·token 없음)로도 돌려줍니다. 검사는 모두 "state·runner state·포트를 쥔 쪽의 정체"를 지금 설정과 비교하는 하나의 규칙입니다.

| 쥔 쪽 | 정체가 다르면 `up` | `doctor` |
|---|---|---|
| gateway state 폴더(owner 기록, 옛 pid 기록) | 멈춤. 그 설정의 `open`·`down`을 보임. 같은 설정 파일인데 시작한 뒤 바뀐 설정이면 `down` 뒤 `up` | warn |
| runner state 폴더(`runner-<id>.sqlite3`·control 폴더·입력 hash cache, `runner.id`가 달라도) | 멈춤 | warn |
| 포트: 다른 설정의 gateway 기록(그 설정을 가리킬 때만), 또는 답한 gateway의 `identity` | 멈춤. 아는 설정이면 `labhq -c <그 설정> open`, 모르면 찾는 법 | warn(기록만) |
| 정체를 안 알려 주는 이전 판 gateway | 이 설정의 살아 있는 기록이 보증할 때만 받아들임. 아니면 경고하고 기록 안 함 | - |
| 마지막으로 그 state 폴더를 쓴 설정이 다름(`labhq-last-config.json`) | 경고 | warn |
| 이 PC의 주 설정과 다름(`labhq-primary-config.json`) | 경고. 바꾸려면 `up --make-primary` | warn |

- `up`은 gateway가 답할 때마다(시작 전, 방금 시작한 뒤) 정체를 확인하고, 확인된 gateway만 last·주 설정에 적습니다. 새 gateway가 포트를 못 잡아 다른 gateway가 답해도 방금 시작한 것으로 치지 않습니다.
- `labhq runner`·`labhq gateway`를 직접 띄워도 시작할 때 같은 검사를 하고(`claim`) 막히면 `Runner`를 만들기 전에 끝납니다.
- `doctor`는 읽기만 하고 HTTP를 하지 않습니다. 포트에서 답하는 모르는 gateway는 `up`만 압니다.
- `--instance`로 만든 설정은 일부러 따로 쓰는 것이므로 주 설정이 되지 않고 비교하지도 않습니다. 설정 파일이 지워진 기록은 무시합니다. 러너만 있는 PC는 포트 비교를 건너뜁니다.
- 경고문은 doctor에서 홈 폴더를 `~`로 가리고, 고치는 명령에는 실제 경로를 씁니다.

**dev log** — `dev_log.repo`는 기본값 없이 init이 owner/name으로 한 번 묻습니다(Enter면 비움, 형식이 틀리면 비움, `--force`는 기존 값 유지). 비어 있으면 `doctor`의 `dev log` 행이 설정하는 법을 알려 줍니다.

**문서** — README 설치 절, `docs/manual.md`(init, doctor, 겹침 경고, bioinfo-agent 연결).

**격리 설치 검사** — `scripts/clean_install_check.py`가 `init`에 Enter 세 번만 줘서, 질문이 하나 늘자 마지막 동의 질문에서 EOFError가 났습니다(임시 HOME에서 재현). 입력을 Enter 여덟 번으로 늘렸습니다.

## 확인

- 새 test 69건: `tests/test_init_defaults.py` 36건, `tests/test_instance_guard.py` 33건(봇 지적 4건 대응 16건 포함, 대응 전 소스에서는 16건 모두 실패). 임시 폴더와 가짜 process 기록만 쓰고 실제 프로세스·포트는 쓰지 않습니다. 옛 예시 설정으로 되돌리면 새 test 6건과 고친 semantics test 1건이 실패합니다.
- 기존 test 수정 3곳(약화 없음): `test_init_wizard.py`의 Windows `--yes` 구역 기대값(빈 목록 → 작업 폴더 internal 구역 하나)과 대화형 답 순서(dev log 질문 한 줄 추가), `test_semantics_shadow_settings.py`의 "예시 설정이 off" 확인(코드 기본 off 확인은 유지하고, 예시는 ab·confirm으로 확인). 이 test는 action layer만 지우는 검사(`test_semantics_shadow_remove`)에서도 돌아서 `actions` 키를 뺀 설정으로 mode를 확인하고, actions 쪽 확인은 그 layer가 소유한 `test_semantics_actions.py`에 뒀습니다. `tests/conftest.py`는 흔한 위치 탐지를 테스트마다 비웁니다(이 PC의 `D:/bioinfo-agent`에 test가 기대지 않게).
- 첫 CI에서 위 semantics test가 3개 환경 모두 실패했고(`ShadowConfig(actions=True)`가 action layer 제거본에 없음), 고친 뒤 `test_semantics_shadow_remove.py` 6건을 로컬에서 통과시켰습니다.
- 로컬 Windows pytest 전체: 82%까지 전부 통과, 나머지에서 59건 실패. 59건은 main에서도 똑같이 실패합니다(`tests/test_web_3d.py` 58건은 격리 HOME 아래에서 `node.exe`를 못 찾음, `test_staff_control_steps.py` 1건은 `tasklist` 출력이 None). node test 58개 파일은 `node --test`로 모두 통과.
- `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.

- 봇 지적 4건(PR 인라인): runner state 폴더 겹침(P1), 같은 token으로 답하는 미등록 설정의 gateway 채택(P1), 후보 설정의 프로세스 기록 소유자(P2), 상대 bioinfo-agent 경로(P2). 앞의 셋은 같은 부류(정체)라 사례별 패치가 아니라 위의 정체 기록·비교 한 가지로 닫았습니다.

## 남은 것

- 정체를 안 알려 주는 이전 판 gateway는 그 설정 자신의 live 기록이 없으면 확인할 수 없어, 경고만 하고 받아들이되 기록하지 않습니다. 다시 시작하면 정체가 붙습니다.
- 같은 설정 파일을 고친 뒤 다시 시작하지 않은 프로세스가 돌고 있으면(정체의 지문이 바뀜) `up`이 `down` 뒤 `up`을 안내하며 막습니다. 포트만 바꾼 경우처럼 새 포트가 비어 있어도 같은 state 폴더를 옛 프로세스가 쥐고 있으면 막힙니다.
- `orchestrator.solo_agent: solo`는 PI 설정에 있지만 저장소 roster에 `solo` 직원이 없어 init 기본값에 넣지 않았습니다.
- `plan.declare_output_types`는 이슈가 켜라고 했지만 PI 시험 설정에는 없습니다(시험 근거 없음). 예시 설정의 `projects:` 자리표시자(`your-account/my-project`)도 PI 설정(`projects: []`)과 다르지만 범위 밖이라 두었습니다.

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
- 새 test 30건: `tests/test_durable_writer.py`(28), `tests/test_loop_liveness_606.py`(2, 새 API 없이 쓰여서 main에서도 돌아갑니다). 기존 test는 하나도 바꾸지 않았습니다.

## 독립 리뷰 반영(PR 리뷰 3건)

리뷰어 둘이 따로 찾았고 적대적 확인을 거친 결함 셋입니다. 각각 고치기 전 head에서 test가 실패하는 것을 확인했습니다.

| 결함 | 원인 | 고침 | test |
|---|---|---|---|
| P1: `task.cancel`이 두 번 오면 `task.result`가 안 나가 gateway가 영영 기다림 | 취소·실패 처리(`_run_guarded`)가 `emit`을 두 번 이어 불렀는데, `emit`이 이제 멈추므로 둘째 취소가 둘 사이에 들어옴 | 닫는 사건(상태 + `task.result` + finished 표시)을 `Runner.emit_all`로 한 작업에 묶었습니다. `controls.finish()`도 보내기 전에 불립니다. `_run_task`의 닫는 쌍 9곳, 거부 경로, 승인·ask 쌍, job 제출·상태·완료와 그 표시도 같은 규칙입니다. 각 사건은 계속 `Runner.emit`을 거칩니다. | `test_a_second_cancel_while_the_closing_events_commit_still_delivers_the_result_once` |
| P2: 마지막 결과를 보내다 취소되면 취소 결과가 하나 더 나가 gateway가 덮어씀 | 결과 commit은 끝까지 가는데 취소 처리가 둘째 `task.result`를 보냄 | runner가 task마다 "결과가 이미 나갔다"를 적고(`_result_queued`), 취소·실패 처리는 그러면 아무것도 더 보내지 않습니다. 새 실행이 시작되거나 guarded 실행이 끝나면 지웁니다. | `test_a_cancel_during_the_final_result_does_not_queue_a_second_result` |
| P2: Windows에서 쓰기 thread의 `os.replace`가 loop thread의 `read_owned`와 겹치면 `PermissionError` | `read_owned`의 열기는 `FILE_SHARE_DELETE`가 없어 교체를 막고, 교체 중에는 열기도 거부됨 | `HeldDir.replace`가 Windows에서 `util.replace_file`처럼 짧게(최대 약 1초) 기다려 다시 시도합니다. `read_owned`의 열기도 `PermissionError`를 짧게(최대 약 0.1초) 다시 시도하고, 그래도 안 되면 이전처럼 없음으로 읽습니다. | `test_replace_waits_out_a_reader_that_still_has_the_file_open`, `test_read_owned_retries_a_sharing_violation_instead_of_reporting_the_file_missing`(Windows 전용) |

옮기지 않은 "멈추지 않음" 가정 한 곳: `_on_control`의 `agent.control` + 안내 로그 두 사건은 정보 표시라 갈라져도 상태가 어긋나지 않아 묶지 않았습니다. 승인 응답의 `agent.status` + ack는 둘째가 빠져도 gateway가 결정을 다시 보내 ack가 나가므로 그대로 뒀습니다. gateway가 같은 task의 둘째 `task.result`를 무시하게 하는 것은 하지 않았습니다(runner 쪽에서 막았고, gateway는 이번에 안 건드립니다).

## 리뷰 3차 반영

| 지적 | 고침 | test |
|---|---|---|
| P1: `Registry.asave_contract`가 두 복사본을 `await` 둘로 쓰고 roster 갱신은 그 뒤라, 둘 사이의 취소가 반쪽 고용을 남김 | 두 복사본과 roster 갱신을 한 `durable.run` 작업으로 묶었습니다. 파일은 쓰기 thread에서 쓰고, roster dict는 작업이 끝나기 전에 스스로 예약한 callback으로 loop에서 바꿉니다(다른 thread가 dict를 바꾸면 loop의 `roster()` 순회가 깨질 수 있어서). | `test_a_cancel_between_the_two_contract_copies_cannot_leave_a_partial_hire` |
| P2: 놀고 있는 lane에서 다른 thread의 동기 `call` 둘이 둘 다 직접 실행되어 순서를 우회함 | 직접 실행도 lane의 실행 lock 아래서 한 번에 하나씩 합니다. 대기열의 작업도 같은 lock을 잡고 돕니다. 이미 lane 안(작업이나 직접 호출 속)에서 부르는 호출은 자기를 기다리지 않고 그 자리에서 실행합니다. | `test_two_threads_writing_one_path_on_an_idle_lane_land_in_call_order` |
| P2: `contract.update`(`extend`·`release`·`activate`·`rehire`)가 아직 loop에서 동기로 씀 | 하지 않았습니다. 후속으로 넘깁니다: 성능 문제(동기 fsync), 정합성 영향 없음. | - |

내가 추가한 비동기 helper 가운데 `await durable.run(...)`이 연속으로 둘 이상인 곳을 모두 찾아(AST로 찾고 눈으로 확인) 정리했습니다.

| 곳 | 연속 `await` | 판단 |
|---|---|---|
| `Registry.asave_contract` | 파일 2 + roster | 위 P1로 한 작업 |
| `_run_task`의 `RESULT_<id>.md`·`RESULT.md` | 2(반복문) | 새 `awrite_owned_many`로 한 작업. 취소가 하나만 쓰게 두지 않습니다. |
| `awrite_task_md`, `aupdate_run`, `awrite_meta`, `aput`, `aenqueue`, `aack`, `asave_contract` 안의 파일들, `_send_batch`·`emit_all` | 1 (`awrite_reference_files`와 `AgentAdapter.prepare` 호출은 여러 파일을 한 작업으로 이미 묶음) | 그대로 |
| `AgentAdapter.run`: prepare, `command.txt`, `stderr_tail.txt` | 3, 단계가 서로 다름 | 파일마다 독립이고 취소되면 그 실행이 끝나므로 그대로 |
| `_run_task`의 `aupdate_run`들(시작·입력·종료), 참고 파일 뒤 `TASK.md` | 여러 번, 단계마다 완결 | 사이에 이미 다른 `await`가 있었으므로 이전과 같은 취소 지점입니다. 그대로 |
| `Runner._on_message`의 승인 응답(`agent.status` 뒤 ack) | 2 | 취소돼 ack가 빠지면 gateway가 결정을 다시 보내 ack가 나가므로 그대로 |
| `_poll_jobs`, `collect_observed_outputs` | 2, 서로 다른 분기·반복 | 각각 한 묶음(앞 절의 `emit_all`)이거나 배타적 분기라 그대로 |

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

- 새 test 30건은 모두 통과했습니다. `tests/test_loop_liveness_606.py`의 2건은 main에서 실패합니다(`/api/health` 1.01초, loop 정지 2.01초). 나머지는 새 API를 쓰니 main에서는 import부터 실패합니다.
- 충돌 모의: 부분 파일은 없고(대상 파일은 옛것이나 새것), 적용된 쓰기는 대기열 앞쪽 K개였습니다. 파일 3지점, SQLite 2지점, runner 재시작 1건(outbox 앞쪽 4개와 중단된 task 결과 하나, 순번 연속)을 확인했습니다.
- 전체 pytest(리뷰 반영 전 head): 6,651 passed, 59 failed, 66 skipped. 실패 59건은 이 PC에서 main도 똑같이 실패하는 것입니다(`test_web_3d.py::test_office_in_node` 58건은 node 경로, `test_staff_control_steps::test_a_cli_start_the_runner_cannot_record_ends_the_cli`은 `tasklist` 출력 해석).
- `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.
- 리뷰 반영 head의 CI: pytest 3.12·Windows 통과, 3.10은 `/api/health` 조사 100여 번 중 하나가 0.41초 걸려 실패했습니다(나머지는 수 ms). CI 기계의 일시 지연으로 보고, 조사 하나라도 stall의 9할(0.45초)을 넘으면 실패하고 20번에 한 번까지만 0.2초를 넘길 수 있게 test를 고쳤습니다. main에서는 여전히 실패합니다.
- 리뷰 반영 뒤에는 PI 시운전 중이라 프로세스를 띄우는 test를 로컬에서 돌리지 않았습니다. 프로세스를 띄우지 않는 Runner·Hub test 1,000여 건과 새 test를 돌렸고(통과), 나머지는 CI에 맡겼습니다.

## 남은 것

- `Registry.extend`·`release`·`activate`·`rehire`(runner의 `contract.update` 메시지)는 동기 `save_contract`를 loop에서 부릅니다. lane이 놀고 있으면 fsync 두 번이 loop thread에서 직접 돕니다. 느린 디스크에서 그동안 runner의 heartbeat와 다른 task 처리가 멈춥니다(성능 문제, 정합성 영향 없음). 후속입니다.

- gateway SQLite 쓰기 약 894번(위 표)은 loop에서 동기입니다. 느린 디스크에서 gateway API는 아직 커밋 시간만큼 멈춥니다. 위 세 가지를 풀 설계가 먼저 필요합니다.
- 동기 호출 쪽은 같은 대상의 대기열 뒤에서 기다립니다. 그래서 runner에서 아직 동기로 남은 `record_run`은 쌓인 쓰기가 끝나기를 기다린 만큼 loop을 붙잡습니다(이전에는 자기 쓰기 시간만큼). 실제 엔진은 task당 2~3번입니다.
- Windows에서 `write_owned`의 교체(`HeldDir.replace`)는 백신이 막 쓴 파일을 잡고 있으면 `PermissionError`로 실패합니다. 다른 곳은 `util.replace_file`이 기다려 주지만 여기는 재시도가 없습니다. 새 test(같은 파일을 1,000번 바꿈)에서 보였고 이번 PR 밖입니다.

## 결론

Refs #608. PI 결정(10-15)대로 보고서 PDF의 그림 정책을 바꿨습니다. PDF 그림은 크기 제한이 없고 늘 요청 묶음의 원본이며, PDF는 50 MB 안으로 그림 해상도를 줄여 맞춥니다. 그림은 11장까지이고 한 장은 graphical abstract입니다. 그림이 없던 요청도 PDF에 abstract가 생깁니다.

## 바뀐 것

- **그림 크기** (`labhq/report_pdf.py`): `FigureEmbedder`의 파일당 8 MB·합계 48 MB 제한을 없앴습니다. 정리본 PDF도 정리본의 1 MB·5 MB 복사본이 아니라 묶음 원본으로 인쇄합니다(`result_storage.pdf_report_text`가 정리본과 같은 가림을 거친 본문에 묶음 그림 링크를 답니다). 정리본 복사본의 한도는 그대로입니다.
- **크기 맞춤** (`print_within_cap`): 첫 인쇄가 `github.edition_pdf_max_mb`(기본 50, 최대 75)를 넘으면 브라우저가 그림을 JPEG로 다시 인코딩하는 판(긴 변 2600→600 px, 품질 0.85→0.35, 6단계)으로 다시 인쇄합니다. 맞을 때까지 한 단계씩 줄이고, 끝내 안 맞으면 가장 작은 PDF를 그림을 모두 넣은 채 두고 크기를 알립니다. 인쇄 시간 초과도 다음 단계로 넘깁니다. 새 의존성은 없습니다. 알림은 게시 카드·정리본 README·`X-Labhq-Reason`에 나옵니다. 캐시 키에 상한이 들어갑니다(`RENDER_VERSION` 2).
- **11장** (`report_figures.MAX_REPORT_FIGURES`): 임베더가 11장까지 넣고 나머지는 표지에 "넣지 못했어요"로 적습니다. 묶음 점검은 11장 초과를 문제로 적고, 링크한 그림을 누적 상한 너머로 지키는 수도 11장입니다(24에서 변경). 작성 지침 `REPORT_FIGURE_RULE`도 11장과 abstract 요청으로 바꿨습니다.
- **graphical abstract** (`labhq/report_abstract.py`): 보고서에 abstract 링크가 없으면 계획의 단계·입력 출처·보고서 결론으로 질문 → 데이터 → 방법·단계 → 핵심 결과 흐름도 SVG를 그려 PDF의 첫 그림, 정리본의 `graphical_abstract.svg`(README·`report.md`의 첫 그림)로 넣습니다. 사무실 색 토큰, 시스템 글꼴, 외부 글꼴·네트워크 없음, 모든 글자 이스케이프입니다. 정리본에서는 라벨에 정리본 문서와 같은 가림을 씁니다. 묶음에 없는 abstract 링크는 묶음·정리본·PDF에서 지웁니다.
- **업로드** (`integrations/github.py`): `put_binary`가 base64 크기를 100 MB 한도로 보내기 전에 검사하고 쓰기 제한 시간을 900초로 둡니다. `edition_pdf_max_mb` 최대를 90에서 75로 낮췄습니다. 그 한도를 넘는 PDF만 이유를 적고 뺍니다.
- `docs/manual.md`(보고서 PDF·정리본·설정 절), 두 `labhq.example.yaml`.

## 확인

- 새 test 28건(`tests/test_pdf_figure_policy.py`)은 main 코드에서 import 단계부터 실패하고 이 branch에서 통과합니다. 실제 Edge test 1건은 브라우저가 없으면 건너뜁니다.
- 의도해서 바꾼 기존 test: `test_report_pdf.py`(이미지 수, 8 MB 한도), `test_result_storage_pdf.py`(PDF 출처·상한·기본값·링크 수), `test_late_storage.py`(정리본 첫 줄). 프롬프트 hash pin은 바뀌지 않았습니다.
- 관련 Python test 1131건과 Node test 58개 파일 통과, `check_public.sh` 통과.
- 이 PC의 Edge로 직접 확인: 6.2 MB PNG 3장(첫 인쇄 19.5 MB)을 한도 2 MB로 돌리면 3.7 → 2.8 → 1.5 MB로 3단계에서 맞고 그림 3장이 모두 들어갑니다. 13.4 MB PNG 7장(첫 인쇄 98 MB)은 한도 50 MB에서 1단계에 19 MB가 됩니다.

## 남은 것

- 작성자(CSO)가 직접 만든 abstract 파일은 아직 쓰이지 않습니다. CSO의 기본 도구가 Read·Glob·Grep뿐이라 파일을 만들 수 없고, 합성 단계 작업 폴더는 묶음에 들어가지 않습니다. 단계가 `graphical_abstract.png|svg`를 산출물로 내서 보고서가 링크할 때만 그 파일을 쓰고, 나머지는 labhq가 그립니다. CSO에 쓰기 도구를 주고 합성 산출물을 묶음에 싣는 일은 보안 자세가 바뀌므로 PI 결정이 필요합니다.
- 전체 pytest는 CI에서 돌립니다.

## 결론

Refs #607. 시운전 정리본에서 확인된 편집자 문제 다섯 가지를 고쳤습니다. 출처가 비지 않고, 편집자가 보고서 전문을 받고, README 링크 줄은 한 번만 나오며, 게시 카드가 떠 있는 정리본을 다시 만들 수 있고, 끝난 요청의 낡은 재개 카드가 지워집니다.

## 바뀐 것

- **출처** (`labhq/edition_sources.py`, 새 파일): `SOURCES.md`가 보고서·부록·정리본에 들어간 `resource_versions` 파일에서 URL, DOI(`https://doi.org/…`), PMID·PMC, GEO(GSE·GSM·GPL), SRA(SRR·SRP·SRX·SRS), ArrayExpress, BioProject 번호를 찾아 링크로 적습니다. 중복(`doi:` 글자와 `doi.org` 링크, PMID와 PubMed 링크)은 한 줄로 합치고 종류별로 묶습니다. `resource_versions` 표의 자료·버전·출처 링크는 "소프트웨어·자료 버전"으로 적습니다. 모든 주소는 호스트 거름 하나(`public_link`)를 거칩니다. 호스트는 소문자·끝 점 제거·IDNA로 정규화하고, IPv4·IPv6·IPv4-mapped·십진/8진/16진 IPv4 표기와 `.local`·`.internal`·`.lan`·`.home.arpa`·`.localhost`·한 단어 호스트를 비공개로 보며, userinfo와 토큰 쿼리는 지웁니다(`resource_versions`의 출처 열과 칸 안의 주소도 같고, 비공개 주소는 "(비공개 주소 생략)"으로 바뀝니다). 링크 안의 DOI는 쿼리 없이 경로의 DOI 하나로만 적고, DOI 안의 균형 잡힌 괄호(`10.1016/S0016-5085(98)70201-6`)는 지키며, `PMID:` 뒤의 1~9자리 번호는 짧아도 받습니다. 아무것도 못 찾았을 때만 "출처를 찾지 못했습니다"를 씁니다.
- **편집자 입력** (`result_storage.editor_prompt`): README 초안(보고서 앞 1800자)이 아니라 보고서·진행 기록·재현 방법·출처를 전문으로 보냅니다. 문서가 한도(보고서 10만 자, 나머지 3만 자)를 넘을 때만 개요와 온전한 절을 보내고, 어느 절이 본문 없이 갔는지 적고 "기록이 불완전하다고 쓰지 말라"고 알립니다. 편집자 지시문(`agents/core/editor.yaml`)에도 같은 줄을 넣었습니다. 편집자 없이 만드는 기본 README는 보고서 앞부분을 문단·문장 끝에서 자르고 "report.md에 있습니다"를 붙입니다.
- **링크 줄**: README의 문서 링크 줄은 편집자가 쓴 것이 있으면 그대로 두고, 빠진 링크만 한 줄로 더합니다.
- **다시 만들기**: `labhq storage <요청 id> --redraft`, `POST /api/requests/{id}/storage/redraft`. 게시 카드가 떠 있거나 PI가 거절한 정리본의 편집자를 다시 돌려 이전 카드를 닫고 새 카드를 엽니다. 정리본 폴더는 새로 만들어 이전 파일이 남지 않습니다. 이미 게시한 정리본, 게시가 시작된 정리본(승인 뒤 올리는 중), 정리본이 없는 요청, 안 끝난 요청은 이유와 함께 409로 거부합니다. 게시 카드에 대한 답과 다시 만들기는 같은 lock(`card_lock`)으로 직렬화합니다.
- **낡은 재개 카드** (`labhq/gateway/server.py`): 요청이 끝날 때, PI가 검토 완료를 표시할 때, 게이트웨이를 시작할 때 그 요청의 재개 카드를 지우고 `approval.expired` 이벤트와 `approval_decision` 기록을 남깁니다. 낡은 재개 카드에 답해도 끝난 요청을 다시 돌리거나 실패로 바꾸지 않습니다.
- `docs/manual.md` 편집자 절.

## 확인

- 새 test 함수 25개(parametrize 포함 80건: `tests/test_edition_quality_607.py` 75건, `tests/test_resume_cards_607.py` 5건)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다. mock editor·mock GitHub만 씁니다. 출처 fixture는 시운전 보고서 모양(GSE132465·GSE144735·GSM3868436, PMID 25706627·25706628과 DOI)입니다.
- 관련 Python test, Node test 전부, `scripts/patch_notes.py check`, `scripts/check_public.sh` 통과.

## 남은 것

- 편집자가 입력에 없는 말을 지어내 "끊겼다"고 쓰는 일 자체는 막지 못합니다. 입력과 지시문에서 이유를 없앤 것까지입니다.
- 시운전 정리본(`req_75b2fd4b0c`)은 이 PR이 병합된 뒤 `labhq storage req_75b2fd4b0c --redraft`로 다시 만듭니다.

## 결론

Refs #598. `test_full_lab_flow_with_mock_agents`가 Windows CI에서 흔들린 원인은 제품 결함이 아니라 60초·90초 고정 상한이었습니다. 느린 Windows 디스크에서는 흐름이 계속 진행되는데도 상한을 넘습니다. 상한을 "진행이 멈춘 뒤 120초"로 바꿨고 검사 내용은 그대로입니다.

## 원인

- 이 test는 한 번에 `write_owned`(임시 파일 쓰기, fsync, 교체)를 약 290번 event loop 안에서 동기로 부릅니다(`os.fsync` 호출 289회 실측).
- CI 실패 두 건 모두 마지막 사건이 `task.dispatched`이고 요청은 `running`이었습니다. 실패한 test는 111~115초 걸렸고, #592의 다른 시도에서는 같은 test가 31초에 통과했습니다.
  - #592 run 38516233410(시도 2): `[max-turns]` 요청의 60초 대기. s2만 끝났고 s3이 막 dispatch된 상태였습니다.
  - #604 run 38632176341(시도 1): 첫 요청의 90초 대기.
- s2 직후 s3이 dispatch되는 것은 로컬의 정상 사건 순서와 같습니다(s3이 s2에 의존). 순서 결함은 없고, 아래 지연 주입에서도 모든 assertion이 통과합니다.
- 러너의 fsync 시간 자체는 CI 로그에 없어서 재지 못했습니다. 원인은 로컬 재현으로 확인했습니다.

## 바뀐 것

- `tests/test_e2e_mock.py`의 `_until`이 `progress`를 받습니다. 진행(요청 상태·결과 수·그 요청의 사건 수)이 바뀔 때마다 기한이 새로 시작하고, 멈추면 `HANG_S`(120초) 뒤 실패합니다. 어떤 대기도 `CEILING_S`(1200초)를 넘지 않습니다. 실패 메시지의 `observed`는 그대로입니다.
- 요청 종료 대기 4곳, recruit·HPC·깨어남 대기, 계약직 등록 대기는 진행 기준으로, 시작·등록·승인 대기는 120초 고정으로 바꿨습니다. `_until`의 기존 10~90초 상한은 모두 바뀌었습니다(종료 때 task를 정리하는 10초는 그대로).
- `_wait_for_request_terminal`은 `timeout`(진행 없이 기다릴 최대 시간) 인자를 그대로 받습니다. `tests/test_research_cp1.py`가 이 helper를 60초로 부르고 있어서, 그 test도 같은 진행 기준을 따릅니다(상한이 느슨해지기만 합니다).
- 가짜 시계로 도는 helper test 3건(진행 중이면 계속 기다림, 멈추면 실패하고 본 상태를 알림, 끝없이 움직여도 상한에서 끝남)을 더했습니다.
- 제품 코드는 바꾸지 않았고 지운 assertion이나 skip은 없습니다.

## 확인

- 지연 주입(`os.fsync`마다 event loop을 멈춤, 이 PC의 임시 plugin이며 커밋하지 않음):

  | 지연 | 수정 전 | 수정 후 |
  |---|---|---|
  | 0.5초 | 통과(158초) | - |
  | 1.0초 | 실패(95초, #592·#604와 같은 `timed out waiting for request ... to reach a terminal state`, status running) | 통과(305초, fsync 289회) |

- 지연 없이 20번 연속 실행: 20/20 통과(파일 5건, 회당 11~14초).

## 남은 것

- 같은 비용이 제품에도 있습니다. 러너가 task마다 event loop에서 fsync를 동기로 기다려서, 느린 디스크에서는 gateway 응답도 같이 늦어집니다. 이번에는 test만 고쳤습니다.

## 결론

Refs #584 (4번). 편집자 정리본에 `report.pdf`를 넣고, 정리본 안의 그림 링크가 정리본이 가진 파일만 가리키게 했습니다. PDF를 못 만들거나 크기 한도를 넘으면 PDF만 빼고 게시합니다. 통제 데이터 요청은 전과 같이 정리본 단계에서 멈춥니다.

## 바뀐 것

- **PDF 포함** (`labhq/result_storage.py`): 정리본을 다 쓴 뒤 `report_pdf.build_report_file`로 PDF를 만들어 `report.pdf`로 같은 폴더에 둡니다. 원본 묶음이 아니라 정리본 폴더의 가려 쓴 `report.md`와 정리본이 가진 그림에서 인쇄하고(`build_report_file(..., source=정리본 폴더)`, 캐시는 쓰지 않음), 부록은 넣지 않습니다. README가 `report.pdf`를 링크하고 게시 카드에 한 줄(`pdf`)이 보입니다.
- **건너뜀**: 브라우저가 없거나, 인쇄가 실패하거나, 만드는 중 오류가 나면 "PDF는 포함하지 않았어요: 이유" 한 줄을 README와 카드에 남기고 나머지는 그대로 게시합니다. 이유 문장은 고정 문구라 경로·오류 내용이 새지 않습니다.
- **크기 상한**: `github.edition_pdf_max_mb`(기본 20). 넘으면 같은 방식으로 건너뜁니다. 재개한 초안은 디스크의 PDF가 `%PDF-`로 시작하고 상한 안일 때만 올립니다.
- **업로드** (`integrations/github.py`): `put_binary`를 더하고 `put_image`가 이를 부릅니다(메시지만 다름). 이진 파일 규칙은 그림과 같습니다.
- **그림 링크** (`edition_figure_links`): 보고서의 `<workdir_id>/outputs/<파일>`을 정리본이 복사한 `steps/<step_id>/outputs/<파일>`로 고칩니다. 정리본에 없는 그림(미보고, 1 MB 초과, 형식 제외)이나 정리본 밖 경로는 `*(그림 생략: 설명)*`로 바꿉니다. 코드 안은 건드리지 않고, http(s)·data: 링크는 그대로 둡니다.
- **게시 카드**: `result_publish` 카드의 `pdf` 항목에 "보고서 PDF" 이름을 붙였습니다(`labhq/web/ui/decide.js`).
- **테스트 격리** (`tests/conftest.py`): 모든 test에서 `find_browser`를 None으로 둡니다. 정리본 test가 실제 Edge를 띄우고 있었습니다(기존 `test_new_repo_and_upload_only_after_review`가 PDF를 받아 실패해서 알았습니다).
- `docs/manual.md`(정리본 절, 설정 절)와 두 `labhq.example.yaml`.

## 확인

- 새 test 12건(`tests/test_result_storage_pdf.py`)과 Node 1건(`tests/web_result_storage.cjs`)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다. PDF 포함(빌더 mock), 브라우저 없음·인쇄 실패·오류·실제 빌더 무브라우저 건너뜀, 상한(경계 포함), 그림 링크 해소, 통제 데이터 요청 미게시, 재개한 초안의 PDF 업로드, PDF 아닌 파일 거부.
- 관련 Python test(정리본·늦은 저장·보고서 PDF·설정·요청 묶음·정책) 통과, Node test 전부 통과, `scripts/check_public.sh` 통과.
- 이 PC의 Edge로 한 번 직접 돌려 정리본에 실제 PDF(그림 포함 약 40 KB)가 들어가는 것을 확인했습니다.

## 남은 것

- 정리본의 그림 한도(파일당 1 MB, 합계 5 MB)는 그대로라서, 그보다 큰 그림은 정리본과 PDF 모두에서 빠지고 "그림 생략"으로 남습니다. 한도를 PDF용으로 따로 둘지는 PI 결정입니다.
- 원격(http) 그림 링크는 묶음 경로가 아니라서 그대로 둡니다.

## 결론

Refs #586. PR #602의 봇 지적(P2, 인라인 4275011097)을 고칩니다. #602가 topic별 리뷰어 판정을 `(id, topic)`으로 보관했지만, 실제 리뷰 문맥(`plan_review_context`)은 공유 id를 `- batch:` 한 줄로만 싣고 규칙은 "나열된 id 그대로" 답하게 했습니다. 정상 리뷰어는 bare `batch` 한 줄만 돌려주고, 그 판정이 모든 topic 칸에 복사되어 한 topic의 `not_done`이 완료된 다른 topic의 단계에도 P2로 달렸습니다. 이제 리뷰어에게 topic별 id를 보여주고 그 id로 답하게 합니다.

## 바뀐 것

- **리뷰 문맥** (`plan_review_context`, 일반 리뷰·연구 리뷰·단독 처리·단독 리뷰 네 호출부가 같은 함수): 계획이 공유 id를 topic별로 답했으면 topic마다 한 줄입니다.
  `- bulk_rna_seq.batch: <그 topic의 점검 내용> Why: <이유> — 계획 답: step:S5`
  `- single_cell_rna_seq.batch: <그 topic의 점검 내용> Why: <이유> — 계획 답: step:S4`
  합쳐 이은 점검 내용(` / `) 대신 topic 자신의 내용과 이유를 씁니다. 새 함수 `topic_checklists.review_entries`가 줄을 만들고, 읽는 쪽(`check_slots`)과 같은 칸 규칙을 씁니다.
- **bare id 유지**: 공유하지 않는 점검, 한 topic만 선언한 점검, 계획이 bare id 하나로 답한 공유 id, 선행 기준(`precedent.<n>`)은 예전과 같은 `- <id>: <점검> Why: <이유>` 한 줄입니다.
- **리뷰 규칙** (`CHECKLIST_REVIEW_RULE`, 일반·연구 리뷰 프롬프트 공통): "나열된 id 그대로, `topic.id`로 나열된 점검은 나열된 id마다 한 줄씩" 답하게 한 문장을 더했습니다.
- `docs/manual.md` 점검표 절의 해당 문장을 고쳤습니다.
- **프롬프트 해시 pin**: 갱신할 것이 없었습니다. pin된 것은 계획 프롬프트(`test_output_types.py`), 연구 계획 프롬프트(`test_output_types_research.py`), 단계 프롬프트(`test_step_prompt_rules.py`, `test_general_evidence.py`)이고 리뷰 프롬프트는 pin되어 있지 않습니다. 이 PR은 계획·단계 프롬프트를 건드리지 않으며, 그 test들이 그대로 통과합니다.

## 확인

- 새 test 6건(`tests/test_checklist_review.py`):
  - 문맥이 공유 id를 topic별로 싣고 topic 자신의 점검 내용과 계획 답을 붙이는지(`step`+`step`, `step`+`assumption`)
  - 공유하지 않는 점검과 bare 답은 bare id 한 줄인지
  - 두 리뷰 프롬프트가 규칙 문장을 담는지
  - 프롬프트에 나열된 id만 정확히 답하는 가짜 리뷰어가 topic별 판정을 얻고 한 topic의 `not_done`이 다른 topic 단계에 P2를 만들지 않는지(10-13 시운전의 `batch` S4·S5 모양)
  - 같은 가짜 리뷰어를 팀 경로 `run_request` 끝까지 돌려 프롬프트 조립, 파싱, P2, 실행 기록 점검표를 확인하는 end-to-end test. 이 test는 `S4`에만 P2가 달리고 표가 `bulk_rna_seq/batch | S5 | 했다`, `single_cell_rna_seq/batch | S4 | 안 했다`인지 봅니다.
  main의 소스로 돌리면 5건이 실패하고(나머지 1건은 바뀌지 않은 bare 동작의 보호용) 이 branch에서는 통과합니다. 기존 test는 고치지 않았습니다.
- 관련 pytest(cso·research·solo·report·review·checklist·output_types·strict schema·bundle·semantics·step prompt·general evidence 54개 파일) 2218 passed, 4 skipped. `for f in tests/*.cjs; do node --test "$f"; done` 58개 파일 모두 통과. 전체 pytest는 CI에 맡깁니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 리뷰어가 나열된 `topic.id`를 무시하고 bare `batch` 한 줄만 돌려주면 #602대로 그 줄을 자기 줄이 없는 topic 전체의 한 판정으로 보고, miss 한 건에 두 단계를 모두 적습니다. topic을 가를 근거가 없어서 그렇게 두었습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 결론

Refs #586. 병합된 PR #600의 봇 지적 둘(P2)을 고칩니다. 단독 처리 결과의 리뷰 판정이 실행되지 않은 계획 단계 A에 붙던 것과, 같은 점검 id를 topic별로 다르게 답했을 때 리뷰어 판정이 bare id 하나로 합쳐지던 것입니다.

## 바뀐 것

- **단독 처리(`solo_review: true`)**: 선택은 "단독 결과에도 P2를 달되 `단독 처리`에 귀속"입니다. 단독 경로에서는 계획 단계가 실행되지 않고 직접 처리 결과 하나가 리뷰 대상이라, 점검 miss의 P2는 `step_id`가 `단독 처리`이고 요청 문구도 "단독 처리 결과에서 …를 끝내거나 한계에 남기세요"입니다. 실행 기록 점검표의 담당 단계 칸도 `단독 처리`입니다(`assumption`은 `가정`, `not_applicable`은 `해당 없음` 그대로). 리뷰어 없이 단독으로 끝난 요청도 같은 표시입니다. 팀 경로가 단독에서 넘어온 경우(`fallback`)는 단계가 실제로 실행됐으므로 단계 id를 그대로 씁니다.
  - P2를 남긴 이유: P2는 verdict를 바꾸지 않고 `리뷰 참고`에 한 줄로만 실려서 팀 경로와 PI가 보는 양이 같습니다. 판정만 표에 두면 단독 경로에서는 `못 했다`가 `리뷰 참고`에 안 보입니다. `단독 처리`는 계획 단계 id가 아니라서 웹의 단계 카드(`state.js`가 `issue.step_id === step.id`로 붙임)에도 달리지 않습니다.
- **topic별 답을 가진 공유 id**: 리뷰어 줄을 `(id, topic)`으로 따로 보관합니다. `topic.id`·`topic/id` 줄은 그 topic의 답에만, bare `id` 줄은 자기 줄이 없는 topic 전체에 하나의 판정(단계는 모두 적고 첫 단계에 답)으로 적용합니다. 계획이 bare id 하나로 답했으면 답이 하나이므로 예전처럼 가장 나쁜 판정을 씁니다.
  - miss는 같은 topic의 `step:` 답에만 연결됩니다. `bulk_rna_seq.batch=step:S5`, `single_cell_rna_seq.batch=step:S4`(10-13 시운전 모양)에서 `single_cell_rna_seq.batch=not_done`이면 S4에만 P2가 달리고 S5는 영향이 없습니다. 한쪽이 `assumption:`이면 그 판정은 `step:` 답에 miss를 만들지 않습니다.
  - 점검표는 topic 답마다 한 줄입니다(`bulk_rna_seq/batch | S5 | 했다`, `single_cell_rna_seq/batch | S4 | 안 했다`).
  - 같은 부류라 함께 닫았습니다: 한 topic의 `topic/id`를 이름으로 든 P1·P2 지적이 다른 topic의 같은 id miss를 대신해 지적을 막던 것(`_names_check`). 이제 지적이 가리키는 topic의 miss만 대신합니다.
- `docs/manual.md` 점검표 절에 두 동작을 적었습니다.

## 확인

- 새 test 9건(`tests/test_checklist_review.py`): topic별 판정(둘 다 step, step과 assumption, bare 줄과 topic 줄 혼합, bare 답은 여전히 합침), 지적 대체의 topic 경계, 표의 alias별 줄, 단독 단위 test, 단독 end-to-end(리뷰 있음·없음). main의 소스로 돌리면 8건이 실패하고(나머지 1건은 바뀌지 않은 동작의 보호용) 이 branch에서는 모두 통과합니다. #600이 남긴 "단독 경로 end-to-end test 없음"도 이걸로 채웠습니다.
- 기존 test는 고치거나 약하게 만들지 않았습니다(`test_a_shared_id_names_every_owning_step_and_files_the_issue_on_the_first` 포함).
- 관련 pytest(cso·research·solo·report·review·checklist·output_types·strict schema·bundle·semantics 52개 파일) 2187 passed, 4 skipped. `for f in tests/*.cjs; do node --test "$f"; done` 58개 파일 모두 통과. 전체 pytest는 CI에 맡깁니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 단독 처리 보고서의 실행 기록 "Step status and output paths"는 여전히 실행되지 않은 계획 단계를 `A: not run`으로 적습니다. 점검표와 다른 절이라 이 PR에서는 손대지 않았습니다.
- 단독 리뷰어에게 주는 점검 문맥(`plan_review_context`)의 "Answers"는 계획의 `step:A` 형태 그대로입니다. 리뷰어 프롬프트는 바꾸지 않았습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 결론

Refs #546. 병합된 PR #599에 봇이 남긴 P2 네 건을 고쳤습니다. 모두 화면 비교 test 도구와 데모 움짤 스크립트의 오류이고 앱 동작은 바꾸지 않았습니다. 지적마다 커밋 하나와 그 지적이 실패하는 test가 있습니다.

## 바뀐 것

| 지적 | 커밋 | 고친 내용 |
|---|---|---|
| `layout_snapshot.cjs` 색 비교 | 8007211 | 기준선의 색은 `#rrggbb`인데 `sameColor`가 10진수 덩어리를 뽑아 비교했습니다. 두 자리씩 한 채널(`#rrggbbaa`의 alpha는 바이트)로 읽습니다. `#1d6b40`과 `#1e7b40`은 다르고 `#505b67`과 `#515b67`은 같습니다. `rgb()` 글자는 이전처럼 읽습니다. |
| `update_visual_baseline.cjs` | 68846ee | Chrome이 없으면 자식 test가 아무것도 쓰지 않고 0으로 끝나는데도 "커밋하라"고 안내했습니다. 건너뛰어질 상황이면 시작하지 않고, 끝난 뒤 기준선 내용이 그대로면 `Baseline NOT updated`와 이유를 내고 1로 끝납니다. 줄바꿈(CRLF/LF)만 다른 것은 바뀐 것으로 치지 않습니다(Windows checkout에서 첫 실행이 거짓으로 성공하던 것을 실제 실행에서 확인해 막았습니다). |
| `demo_driver.cjs` 가상 timer | 103d3cf | callback 예외를 잡아 `console.error`로만 남겨서 `Runtime.exceptionThrown`이 나지 않았고 `assert.deepEqual(d.errors, [])`가 보지 못했습니다. 실제 브라우저처럼 `reportError`로 uncaught 예외로 올립니다. 다른 timer는 계속 돌고, `reportError`가 없는 브라우저에서는 이전처럼 로그를 남깁니다. `record_demo_gif.cjs`도 같은 `d.errors`를 봅니다. |
| `gif_encode.py` 크기 제한 | 7e9c535 | `write_gif`가 output을 먼저 덮어쓴 뒤에야 `--max-mb`를 확인했습니다. 인코딩한 뒤 크기를 먼저 확인하고 통과한 것만 임시 파일을 거쳐 옮깁니다. 넘기거나 쓰기가 실패하면 이전 파일이 그대로입니다. |

- 새 `tests/web_visual_tooling.cjs`(node test 14건)와 `tests/test_gif_encode.py`(5건)가 지적마다 test를 가집니다. 색 비교(`#1d6b40` 대 `#1e7b40`, `#505b67` 대 `#515b67`, 경계 2·3, alpha), 기준선 갱신 종료 코드(건너뜀·변화 없음·줄바꿈만 다름·성공·실패·신호), 가상 timer(vm으로 페이지 시계를 직접 돌려 setTimeout·setInterval 예외 기록, Chrome에서 `d.errors`에 실제로 들어오는지), GIF(제한 초과 시 기존 파일 유지·새 파일 없음·제한 안이면 교체·쓰기 실패 시 임시 파일 정리).
- main의 코드로 돌리면 색 비교는 위 두 쌍을 거꾸로 판정하고, 가상 timer와 GIF test 일곱 건이 실패합니다. 기준선 갱신은 새 인자 없이는 시험할 수 없어서 `updateBaseline`에 인자를 주입할 수 있게 했습니다.
- `tests/test_web_3d.py`의 node 제한 시간 표에 `web_visual_tooling.cjs`(170초, Chrome 시작 예산)를 더했고, `tests/visual/README.md`에 종료 코드를 적었습니다.

## 확인

- `for f in tests/*.cjs; do node --test "$f"; done` 58개 파일 모두 통과(새 파일 포함). 새 파일 14건은 연속 7회 모두 통과했습니다.
- 시각 test(`web_visual_demo.cjs`)는 더 엄격해진 색 비교와 timer 예외 기록에서도 32개 화면이 기준선과 맞습니다. `node scripts/update_visual_baseline.cjs`를 실제로 돌리면 기준선이 그대로라 1로 끝납니다.
- `node scripts/record_demo_gif.cjs`를 새 인코더로 다시 돌려 `office-25d.gif`와 `.png`가 커밋된 파일과 바이트까지 같은 것을 두 번 확인했습니다(한 번은 Chrome 스크린샷 차이로 달랐고 다시 돌리니 같았습니다. 인코더가 아니라 스크린샷의 차이입니다).
- 관련 pytest(`test_gif_encode`·`test_integrations`·`test_lab3d`와 `test_web_3d`의 node 래퍼) 통과. 전체 pytest는 CI에 맡깁니다. 이 PC의 `node`는 Codex 번들을 찾는 shim이라 pytest가 HOME을 바꾸면 못 찾고 `web_visual_*` 래퍼가 실패합니다(main에서도 같음). 실제 `node.exe`를 PATH 앞에 두면 통과합니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 없음. PR #599 인라인 네 건에 각각 `고침:` 답글을 달았습니다.

## 결론

Refs #586 (3번). 10-13 PI 시운전에서 리뷰어 두 명이 점검표와 계획의 답을 받고도 점검 항목을 근거로 든 지적이 0건이었고, 보고서에는 점검 항목 이름이 나오지 않았습니다. 이제 리뷰어가 항목마다 했다·일부·안 했다와 근거를 답하고, 계획이 단계로 답한 항목을 못 했다고 판정하면 P2 지적이 생기며, 실행 기록에 점검표가 붙습니다. #586의 1·2번은 이 PR 범위가 아닙니다.

## 바뀐 것

- **리뷰 프롬프트** (`REVIEW_PROMPT`, `RESEARCH_REVIEW_PROMPT`): 선언한 점검 항목과 필수 선행 기준(`precedent.<n>`)마다 `checklist` 한 줄(`id`, `status` done·partly·not_done, `evidence` 단계 id나 산출 경로)을 답하게 합니다. 계획의 답이 아니라 결과를 보고 판정하게 합니다.
- **schema**: 일반 리뷰(`REVIEW_SCHEMA`)와 연구 리뷰(`RESEARCH_LANE_REVIEW_SCHEMA`)에 선택 필드 `checklist`만 더했습니다. 없는 리뷰와 옛 저장 리뷰는 그대로 유효하고, 읽을 수 없는 줄은 리뷰 전체가 아니라 그 필드만 버립니다(`lenient_review`). 연구 lane은 재시작 때 저장 리뷰를 다시 읽을 때도 이 줄을 유지합니다.
- **P2 지적**: 계획이 `step:<id>`로 답한 항목을 partly·not_done으로 판정하면 `review_with_checklist`가 그 단계에 P2 지적을 더합니다. 그 id를 이름으로 든 P1·P2 지적이 이미 있으면 더하지 않고, P3는 대신하지 못합니다. `assumption`·`not_applicable` 답은 지적을 더하지 않습니다. P2라 verdict는 바뀌지 않고 보고서 `리뷰 참고`에 한 줄로 실립니다. 여러 topic이 같은 id를 쓰면 담당 단계를 모두 적고 첫 단계에 답니다.
- **점검표**: 실행 기록 부록(`## 부록: 실행 기록`)에 항목·담당 단계·리뷰 판정·근거 표를 붙입니다. `_finish` 한 곳에서 만들어 일반·단독·연구 lane이 같은 표를 얻습니다. 요청 묶음은 `report_appendix`를 그대로 옮기므로 따로 고칠 곳이 없었습니다. `assumption` 답은 `가정`, `not_applicable`은 `해당 없음`(판정 `-`)이고, 리뷰어 답이 없으면 판정은 `-`입니다.
- **프롬프트 해시 pin** (`tests/test_output_types*.py`): 바뀌지 않았습니다. 이 PR은 계획 프롬프트와 계획 schema를 건드리지 않아서 pin된 해시가 그대로 통과합니다. 일부러 갱신할 변화가 없어 주석도 달지 않았습니다.
- **기존 test 두 개를 고쳤습니다**: `test_already_strict_schema_is_unchanged`와 `test_research_review_schema_is_strict`는 연구 리뷰 schema의 모든 필드가 필수라고 가정했습니다. 선택 필드 하나를 예외로 두도록 바꾸고(필수 목록은 그 필드만 빼고 같아야 함), 전송용 복사본이 그 필드만 nullable로 만드는지 확인하는 test를 더했습니다.
- `docs/manual.md`의 점검표 절에 리뷰어 답·P2·표를 한 단락 적었습니다.

## 확인

- 새 `tests/test_checklist_review.py` 19건(프롬프트, 두 schema, P2 규칙의 경계, 표, 일반·연구 end-to-end와 재시작). main의 소스로 돌리면 19건이 모두 실패하고 이 branch에서는 통과합니다.
- 관련 pytest(cso·research·solo·report·review·checklist·output_types·strict schema·bundle·semantics) 2154 passed, 4 skipped. `for f in tests/*.cjs; do node --test "$f"; done` 57개 파일 모두 통과. 전체 pytest는 CI에 맡깁니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과(PR #600).
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 단독 처리(solo) 경로의 end-to-end test는 없습니다. 세 리뷰 호출부가 같은 함수를 쓰고 기존 solo test가 그 경로를 지나가며, 단위 test가 함수를 덮습니다.
- id가 영어 단어와 겹치면(`batch`) 그 단어를 쓴 P1·P2 지적을 대신으로 보고 지적을 더하지 않습니다.

## 결론

Refs #546. 디자인 리뉴얼 5번째 PR입니다. 데모 화면의 레이아웃을 Chrome으로 비교하는 test와 README 데모 움짤을 새로 만들었고, #590에서 넘어온 편집자(비버) 소품·구역 누락을 고쳤습니다. 앱 동작과 3D는 바꾸지 않았습니다.

## 바뀐 것

- **화면 비교 test** `tests/web_visual_demo.cjs`: 데모(`?demo=1`)를 데스크톱 1440×900과 폰 390×844 headless Chrome으로 열어 시작 · 요청 진행 · 결정 대기 · 보고서 검토 네 장면을 찍습니다. 각 장면에서 결정·작업판·메신저 탭과 직원 줄을 펼친 모습까지 모두 32개 화면의 탭·결정/검토 카드·작업판·메신저·직원 카드·사무실 위치와 크기, 보임 여부, 주요 색, 상태 글자를 `tests/visual/demo-layout.baseline.json`과 비교합니다. 픽셀은 비교하지 않습니다. 허용 오차는 4px 또는 화면의 1%, 색은 채널 2입니다.
- **OS가 달라도 같은 값**: 시험 전용 글꼴(글자 폭만 있는 작은 TrueType, 파일 없이 메모리에서 생성)과 가상 페이지 시계(Date·setTimeout을 대신함)를 씁니다. 단계를 "화면이 이 상태가 될 때까지 시계를 돌린다"로 기다리므로 고정 sleep이 없습니다. 다른 로케일·시간대·글꼴 힌팅 옵션으로 돌려도 같은 기준선과 일치했습니다.
- Chrome이 없으면 이유를 적고 건너뜁니다. Node 22 미만에는 전역 `WebSocket`이 없어 `tests/support/mini_ws.cjs`의 작은 클라이언트로 대신합니다. 새 `tests/web_*.cjs`라서 pytest(`test_web_3d.py`)가 그대로 모아 돌립니다(제한 시간만 260초로).
- **기준선을 일부러 바꾸려면** `node scripts/update_visual_baseline.cjs`. 이전 기준선과 달라진 항목을 출력하니 의도한 변화인지 보고 `demo-layout.baseline.json`을 커밋합니다. 설명은 `tests/visual/README.md`.
- **README 데모 움짤** `docs/media/office-25d.gif`: `node scripts/record_demo_gif.cjs`가 같은 데모를 시작 → 요청 → 직원 작업 → 결정 카드 → 승인 → 보고서 순서로 녹화(11장면)합니다. 960×600, 13.1초, 0.62MB. 같은 코드로 다시 녹화하면 바이트까지 같습니다. GIF는 표준 라이브러리만 쓰는 `scripts/gif_encode.py`(PNG 읽기, median cut 팔레트, LZW)가 조립하며 새 의존성은 없습니다. README 한국어·영어의 그림과 대체 텍스트, 정지 화면(`office-25d.png`)을 바꾸고 옛 webp는 지웠습니다.
- **편집자(비버)**: 소품 표에 없어 파견직 배지로, 구역 표에 없어 연구 구역으로 떨어지던 것을 고쳤습니다. 운영과 파견직 구역에 펜과 종이 소품으로 앉습니다. `agents/core/*.yaml`의 핵심 직원이 모두 구역과 소품 표에 있는지 확인하는 node test를 더했고, main의 화면 파일로 돌리면 실패합니다.

## 확인

- 새 화면 test 로컬 10회 연속 통과(회당 약 4.5초). 일부러 탭 높이·강조색·대기 개수 배지를 바꾼 복사본으로 돌리면 실패해 어디가 달라졌는지 출력합니다.
- `for f in tests/*.cjs; do node --test "$f"; done` 57개 파일, 198건 통과. 관련 pytest(`test_web_3d`·`test_gif_encode`·`test_integrations`·`test_lab3d`) 135 passed, 1 skipped. 전체 pytest는 CI에 맡깁니다.
- GIF 인코더는 test 안의 독립 GIF/LZW 디코더로 왕복 검증합니다(필터 5종 PNG 읽기, LZW 표 넘침·clear 코드 포함).
- `python scripts/patch_notes.py check`, `bash scripts/check_public.sh` 통과.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 정지 화면

![결정 카드 대기 장면](../media/office-25d.png)

## 남은 지적

- 기준선은 Windows Chrome에서 만들었습니다. Linux CI에서 처음 도는 것이라 그쪽 결과는 CI로 확인합니다.
- 데모 roster에는 편집자가 없어 화면 기준선은 편집자 변경에 영향받지 않습니다. 그 변경은 node test가 확인합니다.

## 결론

최근 3일(10-11~14) `test` workflow에서 같은 test가 2번 이상 흔들린 원인은 대부분 1~2초짜리 실제 시계 경계였습니다. 그 test들이 사건을 기다리게 바꾸고, 반복 실행 중 만난 제품 결함 하나(Windows 파일 교체)를 고쳤습니다.

## 바뀐 것

- login/quota wait test: 기본 대기 창을 1초에서 600초·1000초로 늘리고, 만료를 보는 test는 20초씩 흐르는 가짜 시계로 바꿨습니다.
- stall watch, topic checklist, round records, followup e2e test: 1~30초 상한을 hang 감지용 60~180초로 늘리고, sleep 폴링 5곳은 조건 대기로 바꿨습니다.
- `web_office_browser.cjs`: Chrome 시작을 10초 고정 반복 대신 60초 deadline으로 기다리고, Chrome이 먼저 끝나면 바로 실패합니다.
- 제품: `cli._process_created`가 종료 중인 프로세스의 `/proc/<pid>/stat` 읽기 오류(ESRCH)를 "신원을 읽을 수 없음"이 아니라 "이미 없음"으로 봅니다. 이 PR의 CI에서 같은 코드가 한 번은 통과하고 한 번은 `test_the_watcher_reports_a_gateway_that_ended_by_itself`(exit 2)로 실패했습니다.
- 제품: `util.replace_file`이 Windows에서 스캐너가 잠깐 붙든 파일의 `os.replace` 거부(PermissionError)를 최대 약 1초 기다립니다. `RoundRecorder.write`와 `atomic_write_text`가 씁니다.
- 지우거나 skip한 test는 없고 검사 내용은 같습니다.

## 확인

- 지연 주입(저장소 쓰기마다 event loop을 150~300ms 막음): login 12건과 quota·stall watch 7건이 수정 전 실패, 수정 후 통과.
- 반복 실행: login·quota·stall·topic·round records·followup·`web_office_browser` wrapper 각 20/20, `.cjs` 직접 10/10.
- 수정하지 않은 test(이미 고쳐졌거나 PR 한정 실패): semantics 2파일, kill 경로 2건, github reporter, bench 20/20.
- 이 PC에서만 본 것: semantics 반복 중 2번 `Windows fatal exception: access violation`(`runner/versions.py` probe 스레드의 CreateProcess), 전체 실행에서 `test_staff_control_steps`가 한국어 `tasklist` 출력을 UTF-8로 읽다 실패(`PYTHONUTF8=1` 없이는 통과). 둘 다 CI 로그에는 없어 고치지 않았습니다.
- 전체 pytest 6618 passed, 1 failed(위 `tasklist` 인코딩), 66 skipped.

## 남은 지적

- 같은 `os.replace` 거부에 취약한 곳이 더 있습니다(`request_bundle.py`, `doctor.py`, `runner/codex_sandbox.py`, `hooks/control.py` 등). 이번에는 이 PR이 본 두 곳만 고쳤습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 결론

PI의 gateway·runner가 하루에 여러 번 종료 기록 없이 함께 죽던 원인인 PID 재사용 tree-kill을 막았습니다. 종료 대상은 이제 "부모 PID가 같고 부모보다 늦게 만들어진" 프로세스뿐이고, 죽이기 직전에 생성 시각을 다시 확인합니다. Refs #589.

## 원인

`labhq up`이 끝나면 detached launcher가 `up`의 PID를 부모 PID로 계속 갖습니다. Windows가 그 PID를 짧게 사는 다른 프로세스(예: 버전 조사 probe)에 다시 주고 labhq가 그 프로세스를 `taskkill /T`나 부모 PID 수집으로 종료하면, 트리에 PI의 gateway·runner가 자식으로 섞여 같이 죽었습니다. runner는 버전 조사를 주기적으로 돌려서 밤에도 재현됐습니다.

## 바뀐 것

- 새 `labhq/proc_tree.py`: 후보(부모 PID로 닿는 모든 프로세스)마다 `OpenProcess`(조회·종료·SYNCHRONIZE)로 handle을 열어 종료 결정이 끝날 때까지 쥡니다. 열린 handle이 프로세스 객체를 붙들어 PID가 재사용될 수 없습니다. 모든 handle을 연 뒤 프로세스 표를 한 번 더 읽어, 부모 PID가 첫 조회와 같은 후보만 남깁니다(첫 조회와 open 사이에 PID가 넘어간 경우를 걸러냄). 생성 시각은 쥔 handle에서 읽고, 부모보다 이른 프로세스는 PID 재사용 orphan으로 보고 그 부분 트리째 뺍니다. 검증된 집합은 말단부터 쥔 handle로 `TerminateProcess`하며(`/T` 없음, PID를 다시 열지 않음) 끝나면 handle을 모두 닫습니다. 열 수 없어 검증할 수 없는 프로세스(elevated·보호된 자식 등)는 건드리지 않습니다. 다만 두 조회에서 부모 PID가 같고 그 부모가 루트나 검증된 후손이면 살아남은 자식일 수 있으므로 종료 실패와 똑같이 `failed`에 이유와 함께 기록하고 조상을 보존합니다. 열기 전에 이미 사라진 프로세스, 우리 트리가 아닌 부모 밑의 프로세스는 실패가 아닙니다.
- `adapters/base.py` `_kill`(staff CLI 시간 초과·취소), `runner/versions.py` `_kill_tree`(버전 조사 시간 초과), `cli.py` `_terminate_tree`(`down`·웹 종료 감시)가 모두 이 helper를 씁니다. `down`은 기록된 `created`로 루트 신원을 확인하고, 읽을 수 없는 형식이면 종료하지 않습니다. 직접 PID는 여전히 종료합니다. POSIX 경로는 그대로입니다.
- `down`은 루트뿐 아니라 어느 자식이든 하나라도 못 끝내면(예: launcher 밑 venv interpreter의 `TerminateProcess`가 거부됨) PID와 이유를 적어 exit 2로 실패하고 PID 기록을 남깁니다. 실패한 프로세스의 조상 전체(루트까지)는 종료하지 않고 둡니다. 그래야 살아남은 자식이 살아 있는 부모를 통해 다시 닿고, 중간 조상만 죽어 부모 PID가 죽은 PID를 가리키는 일이 없습니다. 실패와 무관한 가지는 그대로 종료합니다(봇 P1 2건).
- `status`·`doctor`의 "heartbeat 기록 없음": 기록된 PID는 venv launcher인데 진단 행은 그 자식(실제 interpreter)이 씁니다. 살아 있을 때 helper가 검증한 launcher의 직계 자식 PID 행도 읽습니다.
- `docs/manual.md`: `down` 단락의 taskkill 문장을 안전한 tree-kill 설명 한 줄로 바꿨습니다.

## 확인

- 새 test 46건(기존 taskkill 출력 디코딩 test 2건은 taskkill이 사라져 helper 실패 경로 test로 교체): `tests/test_proc_tree.py` 40건, `tests/test_cli_launch.py` 5건, `tests/test_process_diagnostics.py` 1건.
- 가짜 프로세스 표(실제 PID와 겹치지 않는 값)로 확인한 것: stale-PPID orphan과 그 하위는 수집·종료되지 않고 진짜 자식은 말단부터 종료됨, 버전 probe·adapter 시간 초과·`down`은 `taskkill`을 시작하지 않고 helper를 씀, `down`이 기록된 신원으로 재사용된 PID를 건드리지 않음, 자식 종료 실패는 `down` 실패와 PID 기록 유지로 이어지고 전부 성공하면 기록을 지움, snapshot과 open 사이에 PID가 넘어간 프로세스는 부모 PID가 달라 제외되고 이미 쥔 handle은 PID가 넘어가도 원래 프로세스만 종료함, 실패한 프로세스의 조상 전체를 남겨 두었다가 재시도로 트리를 끝냄(실패와 무관한 가지는 첫 호출에서 종료), 열 수 없는(Access denied) 후손은 `down` 실패와 조상 보존으로 이어지고 열기 전에 사라진 후손은 실패가 아님, snapshot과 kill 사이에 재사용된 PID는 건너뜀.
- 실제 Windows test 1건: 자기가 띄운 부모→자식 python 한 쌍만 종료되고 형제 프로세스는 남음(5회 반복 통과).
- 수정 전 소스로 돌리면 사이트 test 12건이 실패합니다(helper 파일만 둔 상태 기준). 봇 P1 수정의 새 test는 수정 전 head에서 실패하거나 setup 단계에서 실패합니다(수정 전 head로 두 시나리오를 따로 돌려 재현도 확인).
- 관련 pytest 265 passed, 2 skipped(`test_proc_tree`·`test_process_diagnostics`·`test_cli_launch`·`test_probe_timeout`·`test_bench`·`test_exit_grace`·`test_stall_watch`·`test_web_shutdown*`·`test_doctor`). `tests/*.cjs` 55개 통과. `scripts/check_public.sh` 통과. 전체 pytest는 CI에 맡깁니다.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 프로세스가 죽은 뒤의 `status`("비정상 종료 의심")는 launcher PID 기준이라 자식의 exit 기록을 아직 못 찾습니다. 살아 있을 때만 자식 행을 읽습니다.
- `_idle_process_tree`(무응답 경고용, 읽기 전용)도 부모 PID만 봅니다. 종료에는 쓰지 않아 이번에 건드리지 않았습니다.
- `tests/test_bench.py`의 taskkill `MemoryError` 주입은 더는 걸리는 경로가 없지만 그대로 뒀습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 결론

#584를 구현했습니다. 보고서에 핵심 그림 링크를 넣게 하고 묶음·verify가 그 그림을 확인하며, 필요할 때 그림이 든 PDF 보고서를 만듭니다. Refs #584.

## 바뀐 것

| 항목 | 내용 |
|---|---|
| 보고서의 그림 | 보고서 작성 prompt가 결과 목록의 그림만 최대 6개 마크다운 이미지로 넣게 함 |
| 요청 묶음 | `<workdir_id>/outputs/<파일>`을 `steps/<단계>/outputs/<파일>`로 고쳐 쓰고, 링크된 그림은 누적 상한이 차도 복사 |
| `labhq verify` | 묶음 문서의 로컬 그림 링크가 없거나 비었거나 묶음 밖이면 `보고서 그림` 문제(exit 1) |
| PDF 만들기 | 그림·한글 글꼴·인쇄 CSS가 든 HTML 한 장을 Edge·Chrome headless로 인쇄. 브라우저가 없거나 실패하면 HTML과 이유 |
| 진입점 | `labhq report <id> --pdf`, `GET /api/requests/{id}/report-pdf`, 2.5D·3D의 PDF로 받기 버튼 |
| 통제 데이터 | 결과 저장(#558)과 같은 판정(`controlled_request`)과 task의 `restricted_inputs`로 만들지 않음(409) |
| 문서 | manual의 "보고서 PDF" 절, CLI·REST·설정(`gateway.report_browser`) 항목 |

## 확인

- 새 test 22개(Python)와 3개(Node). origin/main 코드에서 behavior test 16개가 실패하고 이 branch에서 통과.
- 관련 기존 test 통과, Node test 56개 파일 통과. 실제 Edge로 A4 PDF를 만들어 한글 글꼴·그림·쪽 번호 확인.
- 공개 저장소·패치노트 검사 통과. 별도 clone venv 사용. 전체 pytest는 CI에 맡깁니다.

## 리뷰 지적 고침

| 항목 | 내용 |
|---|---|
| 목록 | 항목 아래 들여쓴 코드 블록·표·문단을 항목 안에서 렌더링, 순서 목록은 `<ol start>`로 번호를 잇고 1이 아닌 번호는 문단을 끊지 않음 |
| 강조 | `*TP53*의`처럼 한글 조사 앞에서도 기울임(ASCII만 단어 경계로 봄) |
| PDF 캐시 | 표지 값(상태·검토 단계·요청 시각)과 묶음 문서의 digest를 key에 넣음 |
| 그림 링크 | 괄호·꺾쇠 경로를 온전히 파싱, 파일 이름의 `#`·`?`는 파일이 있으면 유지 |
| 묶음 | 그림 링크 검사를 절대경로 rewrite 뒤에 실행 |

## 남은 지적

- 결과 저장(#558) 편집자 정리본에 PDF를 선택적으로 포함하는 일은 이 PR에 없음.
- 브라우저 육안 확인은 UNVERIFIED. 로컬 pytest의 `test_office_in_node`는 node shim 환경 문제로만 실패(Node test는 직접 통과).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 결론

통역 표시 작업이 semantics 비교용 연구 기록에 비결정적으로 섞이던 main CI flake를 고쳤습니다. Refs #557.

## 바뀐 것

- `pi_interpretation` task와 lifecycle event, `messenger.translated`를 round 연구 기록에서 제외했습니다.
- raw ledger·메신저 전달·요청 비용 집계·종료 대기는 유지했습니다.
- reconnect snapshot의 작업 목록에서도 표시 전용 task/event를 제외했습니다.

## 확인

- 수정 전 `confirm` 2회째 재현: `messenger.translated.data.message_seq` 84/87 불일치.
- 실패 parameter 3개를 10회 반복: 30 passed, 0 failed.
- 관련 pytest: 152 passed + round/environment 139 passed.
- `web_office_browser.cjs`와 pytest wrapper 통과. CI의 기존 실패는 Chrome 시작 10초 timeout이었고 제품 assertion은 재현되지 않았습니다.
- `scripts/check_public.sh` 통과. 실제 모델·GitHub API test 호출, PI/trial/HPC 실행은 없습니다.

## 남은 지적

- CI 3.10·3.12·Windows 확인 대기.

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 결론

#588의 남은 지적 6건을 수정했습니다. Refs #557.

## 바뀐 것

| 항목 | 수정 |
|---|---|
| 1 · P1 통제 데이터 | 요청 문장·경로·참고 자료를 policy로 판정하고 오류·불명확 시 모델 호출 차단 |
| 2 · P1 비용 | 통역 비용·timeout 미집계 비용을 실행 중 예산에 한 번만 반영하고 소진 시 호출 차단 |
| 3 · P2 한국어 | 영문 기술 용어를 허용하고 코드·경로는 거부 |
| 4 · P2 이어 묻기 | 2.5D·3D에서 번역된 오류도 실패 문구·스타일 유지 |
| 5 · P2 진행 | 내부 통역 task를 요청 단계·진행·snapshot의 running_tasks에서 제외 |
| 6 · P2 종료 | 진행 중 통역을 shutdown_readiness의 finishing에 포함 |

## 확인

- main `4476228`: 새 Python 회귀 77개 실패, Node 렌더링 회귀 2개 실패.
- 수정 후 관련 pytest 235 passed, Node 5개 파일 통과. 실제 모델·GitHub 호출은 mock만 사용했습니다.
- 공개 저장소·패치노트·기록 목차 검사 통과.
- 별도 clone venv 사용. 전체 pytest는 CI에 맡깁니다.

## 남은 지적

- CI·자동 봇 리뷰 확인은 총괄 인계. 브라우저 육안 확인은 UNVERIFIED.

🤖 Generated with Codex (gpt-6-astra) for the labhq dev lead

## 결론

PR #593에서 완료된 채용 단계의 파견직 runner를 재개 대기에서 제외했습니다.

## 바뀐 것

- `recruit.step_ids` 가운데 미완료 단계가 있는 active receipt만 기다립니다.
- 파견직 단계가 끝났으면 정규직·리뷰 단계를 재개하며, 미완료라면 기존처럼 기다립니다.

## 확인

- main 코드 회귀: 1 failed, 1 passed. 수정 뒤 같은 회귀 2 passed.
- 관련 pytest 99 passed. `scripts/check_public.sh` 통과. 전체 pytest는 CI에 맡겼습니다.

## 남은 지적

- P2 4266513167과 4266592247은 이번 범위에서 고치지 않았습니다.
- 봇 리뷰 대기·지적 대응·병합은 총괄이 이어받습니다.

Refs #591

## 결론

PR #592에서 공유 점검 ID의 분야별 답을 허용하고 bulk deconvolution topic을 추가했습니다. Refs #586.

## 바뀐 것

- `bulk_rna_seq.batch=step:S5`, `single_cell_rna_seq.batch=step:S4`처럼 공유 ID를 분야별 유효 답으로 모두 채우면 검증을 통과합니다.
- 계획 prompt에 공유 ID는 bare ID 하나 또는 모든 `topic.id`로 답한다는 규칙을 넣었습니다.
- `bulk_deconvolution` topic과 참조 프로필, 혼합물 검증, 희귀 세포 한계, 코호트 교차검증, 정규화 일치 점검을 추가했습니다.

## 확인

- 변경 전 회귀 2건 실패, 변경 뒤 2건 통과.
- 관련 pytest: 237 passed, 기존 SyntaxWarning 2건.
- vocab 문서·EDAM subset 검사와 공개 저장소 검사 통과.

## 남은 지적

- #586의 3번(리뷰·보고서 연결)은 지시 범위 밖이라 다루지 않았습니다.
- 전체 pytest, CI, 봇 리뷰, 병합은 후속 단계에 맡깁니다.

## 결론

PR #590 · Refs #546. 시안 E와 캐릭터 1을 2D 사무실에 적용했습니다.

## 바뀐 것

- roster 동물 전신과 직무 소품, 시설팀 햄스터·파견직 배지·중립 fallback.
- 내용에 맞는 2열 구역. 폰 사무실은 분석 구역·지휘부부터 표시합니다. 결정 우선 화면 순서는 유지합니다.
- 상태 점+글자, 결정 대기 강조색, OS 움직임 줄이기. 기존 외부 폰트 링크 제거.
- shared state·3D·API 변경 없음.

## 확인

- 기준 main `7c1686d`: 새 Node 회귀 6 실패·대비 1 통과, Chrome 회귀 1 실패 → branch 모두 통과.
- Node 전체 52개 파일, 174건 통과. Windows 전체 pytest: **1 failed, 6300 passed, 66 skipped, 1382 warnings in 772.02s (0:12:52)**. 실패한 Node 등록 누락 수정 후 관련 Windows **88 passed, 1 skipped**. 전체 재실행은 다른 pytest 프로세스 감지로 차단돼 관련 검사만 실행했습니다.
- PC 1440×900·폰 390×844: 그림 72/64px, 핵심 직원 가림·가로 넘침 없음.
- 실제 글자 대비 light/dark 최저 6.50:1, 그림 윤곽 3:1 이상. reduced-motion animation 0, 외부 이미지·폰트 요청 0.
- 공개·패치노트·기록 목차 검사 통과. main `5a97108`과 충돌(`CONFLICTING`). CI: changes·patch-notes·pytest 3.10/3.12/Windows/slow·clean-install·ro-crate-validator·readme 모두 미생성.

[전후 화면 4장](../design/2026-10-13/office-comparison/README.md) · [검사 근거](../design/2026-10-13/docs/reference/office-verification.md)

## 남은 지적

main 충돌 해소·CI 확인은 총괄에게 넘긴다. 전체 Windows 재실행은 동시 pytest 감지로 생략했다. 관련 검사 TEMP 삭제는 자동 승인 검토가 `blocked by policy`로 거부해 보존했다. 봇 판단·병합은 총괄에게 넘긴다.

PR #581 · `recruit-before-plan` · Refs #549

- 4266319195: 내부 변환 task를 요청 예산에 ID별 한 번만 집계. 다음 dispatch 전 예산 카드 확인.
- 4266319201: 변환 입력을 계약에 저장·비교. repo·focus 변경 시 별도 인재로 재변환.
- 4266319202: 제안 필드·단계 존재·중복·기술자형 조건을 한 함수에서 검사하고 일반·연구 계획 교정으로 반환.
- 4266319205: 남은 단계 계약을 재개 전에 확인. 확정 전 fallback, 확정 후 terminal failure와 기존 resume 카드 종료.
- PR 원래 head `87cc431`: 새 회귀 35 failed / 7 passed. 수정 뒤 42건 통과.
- 관련 pytest 424 passed + Windows 파일 교체 WinError 5 한 건; 해당 test 재실행 1 passed. Node 51개 파일 통과.
- CI에서 발견한 mock 제안의 누락 step_ids와 roster 없는 재개 경로를 보완. 기존 회귀 5 failed → 관련 163 passed, shadow 제거 검사 6 passed.
- 이후 main의 웹 테스트 자동 수집을 병합. 채용·웹 174 passed / 1 skipped, Node 52개 파일 통과.
- 다른 레인 전체 pytest와 겹쳐 로컬 전체 검사는 생략. 최종 CI 결과는 PR 본문에 기록.
- 공개·패치노트·목차 검사 후 push. PI/trial 실행·리뷰 요청·인라인 답글·병합 없음.

PR #588 · `plain-language-2`

## 바뀐 것

- 펭귄 비서실장 설명과 영어 메시지 한국어 표시. 원문은 자세히에 보존하며 기존 쉬운 설명은 유지합니다.
- 메시지 번호별 영구 캐시, 호출 1회, 모델·활성화·요청별 비용 상한. 실패해도 전달하며 번역 비용은 요청에 합산합니다.
- 최종 보고서의 설치된 humanizer 표현 3종 점검과 부록 한 줄. 전체 40개 패턴 진단은 아닙니다.
- 생성 명령 승인 오탐 회귀 확인, 매뉴얼·예시 설정 갱신.

## 확인

- origin/main 기준 새 Python 22건·웹 6건 실패. 생성 명령 테스트는 main에서 이미 통과했습니다.
- Windows 관련 Python 281 passed. Node 전체 51개 파일 최종 수정까지 통과.
- 배포 예시 동기화·CI 등록 검증: Python 372 passed/7 skipped, 격리 HOME의 Node PATH 보정 후 웹 51 passed.
- 다른 레인의 전체 pytest 실행을 확인해 로컬 전체 pytest는 생략했습니다. 전체 결과는 PR CI에 남깁니다.
- 모델은 mock만 사용했습니다. PI/trial 접속·실행, 실제 모델 호출, 리뷰 요청, 병합은 하지 않았습니다.

Refs #557

## 남은 지적

- 로컬 전체 pytest는 레인 중복 실행 금지에 따른 미실행입니다. 테스트 임시 폴더 삭제는 자동 승인 검토가 blocked by policy로 거부했습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #587: 큰 연구 라운드도 사람용 본문을 GitHub issue로 게시하고 원본은 로컬 기록에 보존한다.

## 바뀐 것

- 한도를 넘으면 JSON을 빼고 로컬 `rounds/<rid>.json`을 안내한다.
- 그래도 길면 결과 보고서를 Markdown 줄 단위로 줄이고 로컬 전문 경로를 남긴다.
- 같은 marker 이슈는 수정하며 sha256 중복 방지를 유지한다.
- 과거 크기 초과 `failed` delivery는 재시작 때 다시 시도한다. 게시 기록이 있으면 건드리지 않는다.

## 확인

- 새 회귀 4건은 main에서 실패하고 branch에서 통과했다. 관련 pytest 42 passed.
- 최신 main 병합 뒤 focused 33 passed.
- Windows 전체 pytest: **6,278 passed, 66 skipped** (733.86초). Node CJS 48개 파일 통과.
- 공개·diff 검사 통과. `%TEMP%`의 작업용 pytest 폴더 삭제 완료.
- CI는 기록 커밋 push 뒤 확인한다.

## 남은 지적

- PI/trial 실행·설정·접속, 실제 라운드 저장소 쓰기, 리뷰 요청·인라인 답글·병합은 하지 않았다.

Refs #69

🤖 Generated with Codex (gpt-5.6-sol) for the labhq dev lead

## 결론

PR #583의 늦은 저장 선택과 main #580·#585의 수정 사항을 함께 보존했습니다. Refs #558.

## 바뀐 것

- 검토 뒤 실행, 카드 재사용·재시작 복원, 취소된 부분 결과, 기한 없는 선택을 유지했습니다.
- 편집자 실패 시 기본 정리본, 저장 선택 approval 처리 순서, 게시 파일 단일 commit을 유지했습니다.
- 검토할 수 없는 `completed` 상태는 API·웹의 늦은 저장 허용 목록에서 뺐습니다(리뷰 4266335778).
- Node 자동 수집 51개에 main 명시 목록 50개가 모두 포함됩니다.

## 확인

- Windows 관련 pytest: 141 passed, 1 skipped. GitHub 호출은 mock만 사용했습니다.
- `for f in tests/*.cjs; do node --test "$f"; done`: 51개 파일 통과.
- 로컬 전체 pytest: ROUNDSIZE 전체 suite 실행 중이라 지시된 예외에 따라 미실행했습니다.
- push 뒤 CI 결과는 PR 본문에 기록합니다.

## 남은 지적

- 로컬 전체 pytest 미실행. PI/trial 실행·재시작, 리뷰 요청·답글, PR 병합은 하지 않았습니다.

## 결론

PR #583의 2차 지적 2건을 수정했다. 검토 뒤 저장 선택은 남고, 선택 기록 뒤 종료되어 남은 카드는 재시작 때 해결한다.

## 바뀐 것

- 4266158617(P1): 2.5D·3D 결정 목록에 저장 위치 미정 카드를 유지한다. 검토 완료 버튼은 다시 표시하지 않는다.
- 4266158629(P2): 저장 선택과 카드 해결 이벤트를 재생한 뒤 남은 카드를 삭제한다. 이력에는 저장 선택 복구로 표시한다.

## 확인

- 수정 전 `306282c`: UI 3건·재시작 2건 실패. 수정 뒤 관련 Python 69건·전체 Node 49개 파일 통과.
- 공개 검사·diff 검사 통과. 다른 레인의 전체 pytest 실행으로 Windows 전체는 생략했다.

## 남은 지적

- push 지시와 GitHub 쓰기 금지의 적용 범위 확인 대기. 수정 head의 CI는 미실행이다.
- 임시 폴더 삭제는 자동 승인 검토가 거절했다(`blocked by policy`).

Refs #583

## 결론

PR #581의 봇 2차 지적 P1·P2를 수정했다. Refs #549.

## 바뀐 것

- 일반 요청은 새 계획·재개 모두 범위 승인 뒤 채용한다. 거절하면 채용하지 않는다.
- receipt의 계약 만료·해제를 확인해 정규직으로 재배정한다. 유효한 계약의 러너 대기는 유지한다.
- 최신 main을 병합하고 merge commit도 패치노트에 기록했다.

## 확인

- PR head `bb7261a`: 새 회귀 20 failed, 기존 대기 4 passed. 수정 후 관련 pytest 390 passed.
- 모든 `tests/*.cjs`를 `node --test`로 실행해 통과했다.
- 다른 레인의 전체 pytest가 실행 중이어서 로컬 전체 suite는 생략했다. 전체 검증은 현재 head의 CI 결과로 확인한다.

## 남은 지적

- PI/trial 실행·재시작, 리뷰 요청·인라인 답글·병합은 하지 않았다.

## 결론

PR #585: 시안 E를 작업판·메신저·직원 시트·요청 입력에 적용했다. Refs #546.

## 바뀐 것

- 빈 단계 목록 숨김·완료 접기, 요청 문장·진행 단계 중복 제거.
- 전체 제목·상태·검토 대기가 있는 요청 목록과 48px 선택 영역.
- 한국어 이름·시각·말풍선, 전체 기록·자세히 유지.
- 같은 직원 카드로 그림·역할·정규직/파견직·상태·현재 업무 표시.
- 48px 보내기와 첨부·참고·옵션 분리. API·reducer·3D·그림 생성 코드는 보존.

## 확인

- 새 Node: main에서 6 실패·대비 1 통과 → branch 7 통과. 전체 Node 49개 파일 통과.
- 관련 Windows pytest: 85 passed, 1 skipped. 전체: **6,275 passed, 66 skipped**(759.59초). 전체 검사 임시 폴더 삭제 확인.
- 1440×900·390×844 가로 넘침 없음. 빈 목록 0px·완료 접기 44px, 전환·검토 표시 확인.
- 밝은/어두운 테마 56개 글자 대비 조합: 최저 5.10:1. 공개 검사 통과.
- 코드 head `cbee072`의 [CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38502191583): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 생략.
- 관련 검사 임시 폴더 삭제는 자동 승인 검토가 `blocked by policy`로 거부해 미정리다. 리뷰 요청·지적 대응·병합은 하지 않았다.

[전후 화면 18장](../design/2026-10-13/board-comparison/README.md) · [검증 근거](../design/2026-10-13/docs/reference/board-verification.md)

﻿## 결론

PR #583의 봇 지적 3건을 고치고 main b55138e를 병합했다.

## 바뀐 것

- Node web_*.cjs 자동 수집. 기존 48개 성공 표시 보존, 현재 49개 수집.
- 취소된 요청의 부분 보고서 저장·검토 후 게시 허용. 보고서가 없으면 이유 반환.
- 늦은 저장 선택의 기한 제거. 기존 카드도 재시작·replay에서 기한 없이 복원.
- 결정 카드 디자인과 저장 버튼 보존. 디자인 test의 state·CSS 순서 정합성 수정.

## 확인

- 수정 전: P1 수집 1 failed, 취소·기한 Python 10 failed, 취소 Node 1 failed.
- 수정 후: Windows 관련 pytest 159 passed, 1 skipped. Node 49개 파일 전부 통과.
- 기존 표시 48개 비교·새 파일 자동 수집 probe 통과. 공개 검사 통과.

## 남은 지적

- 다른 레인의 전체 pytest가 실행 중이라 로컬 전체 suite는 생략했다. 전체 검증은 CI 결과를 확인한다.
- 실제 저장소 게시·PI/trial 실행·리뷰 댓글·PR 병합은 하지 않았다.

## 결론

논문 재현 파견직을 CP1 전에 채용·배정합니다. 최신 main을 병합하고 봇 P1 세 건을 수정했습니다. Refs #549.

## 바뀐 것

- 충돌 난 웹 테스트 목록은 양쪽을 보존하고 종료 창·공통 디자인 CSS의 로드 순서를 맞췄습니다.
- 채용 task 복구는 batch·논문별로 구분합니다. 다른 논문의 완료 receipt를 재사용하지 않습니다.
- 새 일반·연구 계획은 `step_ids`가 필수이며 최소 한 단계가 필요합니다. 저장된 연구 계획의 호환성은 유지합니다.
- 재시작하면 현재 batch의 active 파견직 러너를 기다립니다. 기한이 지나면 receipt를 보존하고 요청을 중단합니다.
- 승인·수습·인재풀 재고용·실패 시 정규직 재배정은 기존 요청별 기록을 따릅니다.

## 확인

- 병합 후 수정 전 `7329af9`: P1 회귀 **9 failed** → 수정 후 채용 **32 passed**.
- 관련 **273 passed/1 skipped**, 기존 복구 **100 passed**. Node **48개 파일 통과**.
- Windows 전체 pytest **6,308 passed/66 skipped**(738.52초).
- 공개 검사·패치노트·기록 목차 검사 통과. merge commit을 포함해 패치노트 행을 남겼습니다.

## 남은 지적

- 전체 pytest에 subprocess 출력의 `UnicodeDecodeError` thread warning이 남습니다. 실패한 테스트는 없습니다.
- 실제 Paper2Agent 채용·PI/trial 실행 검증과 PR 병합은 수행하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

저장 위치 질문 없이 끝난 요청도 API·CLI·웹에서 기존 카드를 열어 GitHub 정리본을 남깁니다. Refs #558.

## 바뀐 것

- `POST /api/requests/{rid}/storage`, `labhq storage <id>`, 검토 대기 카드의 **저장 위치 정하기**. 이미 선택·게시한 요청은 거부하고 열린 카드는 재사용합니다.
- 검토 전 선택은 검토 완료와 묶음 갱신을 기다립니다. 검토 후 선택은 바로 편집자 → 게시 승인 → 업로드로 이어집니다.
- README 문서 링크, 진행 기록의 수정 회차, 재현 방법의 환경·명령·입력 출처, 참고 자료의 DOI·PMID·문헌을 보강했습니다.
- 3D 검토 완료에 CP4 revision·hash를 전달합니다. 기존 CSS 검사도 현재 로드 순서 세 파일을 모두 확인합니다.

## 확인

- 기준 main `4a7c03b`: 새 Python 회귀 10 failed, 웹 회귀 2 failed. 관련 Python 35 passed, CP4 포함 43 passed.
- Node 48개 파일 모두 통과. GitHub 생성·업로드는 mock transport로만 검증했습니다.
- Windows 전체 pytest는 다른 레인의 실행이 이어져 15분 대기 후 미실행했습니다. main 충돌로 CI run이 생성되지 않았습니다. changes, pytest (3.10·3.12), pytest-windows, clean-install, ro-crate-validator, pytest-slow, readme, patch-notes 모두 미실행입니다.

## 남은 지적

- main 충돌 해소와 로컬 전체 pytest·CI 최종 검증은 총괄에게 넘깁니다.

- 실제 저장소 생성·게시, PI/trial 접속·재시작, 설정 변경, 리뷰 요청·인라인 답글·병합은 하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #580에 main `4a7c03b`를 병합했다(merge `7c4752d`). P2·design-token·shutdown 테스트를 모두 보존했다.

## 바뀐 것

- `test_web_3d.py`의 Node 목록은 양쪽의 정확한 합집합 48개다.
- shutdown CSS를 `<head>`의 token CSS 앞에 배치해 main에서 들어온 로드 순서 충돌을 해결했다. 테스트 조건은 유지했다.

## 확인

- 관련 pytest: 138 passed, 1 skipped. Node: 48개 파일 통과.
- Windows 전체 pytest: 6,298 passed, 66 skipped(808.62초).
- 공개 검사·diff 검사 통과. push 뒤 CI 결과는 PR 본문에 기록한다.

## 남은 지적

- 임시 폴더 삭제는 자동 승인 검사에서 거부돼 총괄에게 경로를 넘긴다.
- PR 병합과 리뷰 요청은 하지 않았다.

Refs #580

🤖 Generated with Codex (gpt-6) for the labhq dev lead

PR #582 · `design-decisions` · Refs #546

- 결정 배지(0건 숨김), 질문·사실·설명·48px 승인·거절·메모 순서, 원문 펼침을 2D에 적용했다.
- 검토·지난 결정은 같은 부품을 쓰며 시각·보고서·× 숨김을 유지한다. 로그인 대기는 점선 운영 카드로 구분한다.
- 새 Node 8건 main 실패→branch 통과, 전체 Node 48개 파일 통과. 밝은/어두운 주요 토큰 대비 최저 5.10:1/6.39:1, 브라우저 실제 20쌍 최저 5.85:1.
- 1440×900·390×844 전후 화면 첨부. 폰 첫 카드 1,390→163px, 가로 넘침 없음.
- Windows 전체: **6,272 passed·66 skipped·2 failed**. 화면 밖 manifest 교체 접근 거부·shadow 제거 검사 실패. 임시 폴더 삭제 확인. 다른 레인 pytest 실행으로 추가 전체 검사는 하지 않았다.
- CI: head 1d9a24d의 [run 38497285439](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38497285439)에서 changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 건너뜀. 로컬 Windows 전체 PASS는 미충족이다. 봇 리뷰·병합은 총괄에게 넘긴다.

[화면·회귀 근거](../design/2026-10-13/docs/reference/decisions-verification.md)

## 결론

논문 분석 재현에 필요한 파견직을 CP1 전에 채용하고 이번 요청의 해당 단계에 배정합니다. Refs #549 (B·PI 선호).

## 바뀐 것

- 연구·일반 계획에 재현=기술자형/full, 기존·신규 병렬 비교, 공동 Nextflow 순서를 명시합니다.
- 채용을 요청당 한 장으로 승인하고 수습 통과 뒤 바로 투입합니다. 같은 논문 인재풀은 hash·수습 기록 확인 후 재고용합니다.
- 거절·실패·시간 초과는 초안의 정규직에게 재배정합니다. Paper2Agent doctor 실패는 생략 사유를 계획·CP1에 남깁니다.
- 중복 승인·재변환을 막는 요청별 기록, 기존 채용 API와 분리한 실행 집계, 매뉴얼·모의 회귀 테스트를 추가했습니다.

## 확인

- main `d56253a`: 새 회귀 23 failed → branch 23 passed.
- 관련 368 passed, 채용·기존 계약·모의 e2e 106 passed. 수정 후 계약·웹·채용 148 passed, 1 skipped. 표준 Node 46개 파일 통과.
- 채용 카드 추가로 드러난 승인 기록 순서 문제를 고쳤습니다. 순서 회귀 2개는 main에서 실패하며 수정 후 채용·semantics 53개가 통과했습니다.
- 공개·diff 검사 통과. 전체 pytest와 최종 CI 결과는 PR #581에 기록합니다.

## 남은 지적

- 봇 리뷰 대기·대응과 병합은 총괄에게 넘깁니다.
- 실제 Paper2Agent 변환·채용과 PI/trial 실행·설정 변경은 수행하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

기준 main `d538058`의 P2 34건: **22건 수정, 6건 이미 해결, 6건 보류**. #575에는 P2 인라인이 없습니다.

## 확인

- 수정 항목: main에서 새 Python 24건·Node 5건 실패 → branch 통과. 별도 보류 probe 6건도 main에서 재현.
- 이미 해결: main의 기존 pytest 9건과 한국어 표시명·status assertion 통과.
- 관련 pytest 232 passed, 후속 확인 121 passed. Node 46개 파일 및 pytest의 Node 실행 검사 47건 통과.
- Windows 전체 pytest: 6263 passed, 66 skipped, 1232 warnings in 751.58s (0:12:31). mock e2e 포함, basetemp 삭제 완료. 실서비스 실행 없음.
- 공개 검사·diff·patch-notes·notes-index 검사 통과.
- manual: 128,086 → 128,160자(+74). 기존 절만 수정.

## 항목별 결과

| PR | 인라인 id | 요지 | 결과 |
|---|---|---|---|
| #563 | 4257405284 | timed_out 결정 합계 | 고침 `6fdb37d` |
| #563 | 4257405297 | waiting 상태 표시 | 고침 `6fdb37d` |
| #562 | 4256725269 | 이전 follow-up 작업 폴더 누적 | 고침 `6fdb37d` |
| #566 | 4257278189 | 편집자 실패 시 기본 정리본 | 고침 `6fdb37d` |
| #566 | 4257278194 | terminal 뒤 편집자 비용 정산 | 보류 — terminal event·round·후처리 비용 모델을 함께 정해야 함 |
| #566 | 4257278201 | 한국어 표시명·status 손상 | 이미 해결(b123195) |
| #566 | 4257405941 | 질문 대기 off에서도 로컬 선택 | 고침 `6fdb37d` |
| #566 | 4257405952 | 편집 뒤 기본 문서 링크 | 고침 `6fdb37d` |
| #566 | 4257405959 | 입력 오류 뒤 3D 카드 재활성화 | 고침 `6fdb37d` |
| #566 | 4257405974 | 특수문자 산출 경로 인코딩 | 이미 해결(b123195) |
| #566 | 4257499944 | 취소 요청 부분 정리본 | 고침 `6fdb37d` |
| #566 | 4257697760 | 저장 선택 approval 원자성 | 보류 — 승인 선점·실패 복구·timeout 경합의 보안 경계 변경 |
| #566 | 4257697768 | 서버의 게시 전 검토 확인 | 이미 해결(b123195) |
| #566 | 4257806565 | 게시 파일 단일 commit | 보류 — Contents API를 Git tree/ref 트랜잭션으로 교체해야 함 |
| #566 | 4257806570 | approval snapshot 이미지 분리 | 보류 — 인증된 지연 preview endpoint와 두 UI 변경 필요 |
| #566 | 4258023541 | 재시작 시 편집 초안 재사용 | 이미 해결(b123195) |
| #566 | 4258098593 | 공개 게시 금지 프로젝트 상속 | 이미 해결(b123195) |
| #566 | 4258637591 | MANIFEST hash와 게시 bytes 검증 | 보류 — 열린 파일·재시작 초안의 신뢰 경계를 함께 설계해야 함 |
| #566 | 4258715865 | 정리 작업 중 정상 종료 차단 | 고침 `6fdb37d` |
| #566 | 4258715874 | 게시 timeout 초안 보존 | 고침 `6fdb37d` |
| #556 | 4256437484 | roster 없는 시작 경고 | 고침 `6fdb37d` |
| #553 | 4256843193 | 실제 Codex recruiter roster | 이미 해결(3ce6f7a) |
| #553 | 4257446960 | staff 경로 guard 선행 | 고침 `6fdb37d` |
| #565 | 4257144371 | 3D chip의 검토 대기 합계 | 고침 `6fdb37d` |
| #567 | 4257413691 | 시설팀을 일반 plan에서 제외 | 고침 `6fdb37d` |
| #567 | 4257548500 | 시설팀 오류 로그 tail | 고침 `6fdb37d` |
| #571 | 4258059679 | 자동 진단을 blocking ask에서 제외 | 고침 `6fdb37d` |
| #571 | 4258059689 | runner 재접속 전 진단 회수 | 보류 — consult 채택·runner 대기·재시작 ask 순서 변경 필요 |
| #572 | 4258159093 | 메신저 단계 위임 투영·보관 | 고침 `6fdb37d` |
| #572 | 4258232335 | 테스트 요청 purge의 보관 사본 | 고침 `6fdb37d` |
| #572 | 4258605478 | REST follow-up 담당자 복원 | 고침 `6fdb37d` |
| #574 | 4258701773 | 정책 제외 후보 뒤 검색 | 고침 `6fdb37d` |
| #577 | 4259145664 | force로 손상 설정 복구 | 고침 `6fdb37d` |
| #577 | 4265468202 | force의 hard-stop 기본값 복구 | 고침 `6fdb37d` |

## 남은 지적

- 보류 6건은 위 표의 설계·보안 경계를 총괄이 결정한 뒤 별도 변경으로 진행합니다.
- PI·trial 실행, 설정·본 checkout 수정, 리뷰 요청·인라인 답글·병합은 하지 않았습니다.
- 회귀 근거: `tests/test_p2_followups_1013.py`, `tests/test_init_wizard.py`, `tests/web_p2_followups_1013.cjs`, `tests/web_live.cjs`.

## CI

코드 head `e4bb941` · [run 38494585446](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38494585446). 이후 기록 전용 커밋은 이 코드 검사 결과를 재사용합니다.

| Job | 결과 |
|---|---|
| patch-notes | success |
| changes | success |
| clean-install | success |
| pytest-slow | success |
| pytest-windows | success |
| pytest (3.12) | success |
| pytest (3.10) | success |
| ro-crate-validator | success |
| readme | skipped |

## 결론

PR #579: PI가 고른 E + 캐릭터 1의 첫 구현. 63개 토큰(기존 셸 별칭 8개 별도)과 2D 공통 부품을 적용했다.

## 바뀐 것

- `ui/tokens.css`: 색 역할·글자 6단·간격·반경·그림자·상태색·터치 높이·다크 테마.
- `ui/components.css`: 카드·버튼·칩·탭·시트·토스트·목록 줄. 기존 버튼·칩·탭에 연결하고 긴 참고 칩의 줄바꿈을 보존했다.
- [선택 기록·시안·캐릭터·비평 보관](../design/2026-10-13/README.md). Claude 캡처 16장과 [데스크톱·폰 전후](../design/2026-10-13/comparison/README.md) 포함.

## 확인

- Node 46개 파일 통과. 밝은/어두운 테마 각각 19개 글자 대비 조합: 최저 5.10:1 / 6.39:1.
- 관련 pytest 82 passed, 1 skipped. CSS 서빙·패키지 포함·Node CI 등록 확인.
- Windows 전체 pytest: **6,191 passed, 66 skipped** (775.62초). 전체 검사의 임시 폴더 삭제 확인.
- 1440×900·390×844에서 가로 넘침 없음. 폰의 긴 URL·48px 버튼, 직원 시트·다크 테마·3D 데모 표시 확인.
- 공개·패치노트·목차·diff 검사 통과.
- [코드 head `b55eb41` CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38492308495): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 건너뜀.

## 남은 지적

- 결정 카드 위계·탭 개수 연결·폰 순서·입력창 배치·캐릭터 1은 후속 PR 범위다. 대비 확인은 이번 공통 부품의 주요 글자/배경 조합에 한정한다.
- PI/trial 실행·설정·배포, 3D 코드 변경, 리뷰 요청·인라인 답글·병합은 하지 않았다.

Refs #546

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #573에 최신 main(`d56253a`)을 병합했습니다. 충돌은 `labhq/gateway/server.py` 한 곳이었고 두 쪽 의도를 모두 남겼습니다.

## 바뀐 것

- 종료 처리: CP4 기한 타이머 취소와 편집자 저장소 close를 함께 실행합니다.
- 검토 대기 초안은 편집자 게시·GitHub 전송·검색 색인을 시작하지 않습니다. 최종 결정이 색인 알림과 편집자 게시를 예약합니다. bundle을 다시 만드는 요청은 갱신된 bundle 기록이 게시를 예약합니다.
- 검색 색인은 `review_pending` 초안을 건너뜁니다. 시작 시 과거 요청 재처리에서도 같습니다.
- 매뉴얼 CP4 절에 수용 뒤 시작하는 항목을 적었습니다.
- 앞선 봇 지적(P1 GitHub 전송 보류, P2 workdir 없는 요청의 bundle 재생성)의 수정 `bbcf48d`가 로컬에만 있어 PR head에 없었습니다. 그대로 보존해 이 병합과 함께 push합니다.

## 확인

- 회귀: 수용 뒤 색인 알림·편집자 게시를 확인하는 통합 test를 추가했습니다(`test_cp4_accept_wakes_recall_and_publishes_editor_after_review`).
- Windows 전체 pytest 6222개 통과. 처음 한 번은 `PYTHONUTF8=1` 때문에 `tasklist` 한국어 출력 해석이 실패해 1개가 떨어졌고, 그 변수를 빼고 다시 통과했습니다.
- Node test 45개 파일을 하나씩 통과했습니다.

## 남은 지적

- 코드 리뷰(Codex)는 한도 소진으로 돌리지 못했습니다.
- PI/trial 실행·재시작, 리뷰 요청, 병합은 하지 않았습니다.

Refs #90

🤖 Generated with Claude Code for the labhq dev lead

## 결론
웹 종료가 gateway 자신의 종료를 기다리다 10초 뒤 강제 종료되던 교착(봇 P1)을 고쳤습니다. 이제 gateway는 종료 hook을 실행하며 정상 종료합니다.

## 바뀐 것
- runner만 `down`으로 끈 뒤, 분리된 감시 프로세스를 띄우고 gateway가 SIGTERM과 같은 정상 종료 경로를 스스로 시작합니다. 요청 task는 바로 끝납니다. 감시 프로세스는 30초 안에 끝나지 않을 때만 강제로 끕니다(`state/logs/gateway-exit-watch.log`). Windows·POSIX 공통.
- "그래도 끄기"도 같은 경로입니다. runner를 끈 뒤 gateway hook이 중단된 요청을 기록하고, 요청 기록은 이미 저장돼 있습니다.
- 같은 테스트에서 드러난 별도 결함: Windows venv의 `python.exe`는 실제 인터프리터를 자식으로 띄워 `up`이 기록한 PID가 gateway의 `os.getpid()`와 달라, 웹 종료가 항상 "PID 기록을 확인할 수 없어" 거부됐습니다. 기록 PID가 부모 launcher이면 인정합니다.
- main(`d56253a`)을 병합해 충돌을 풀었습니다(index.html import, Node 검사 목록).

## 확인
- 재현: 옛 `run()`(`down` 직접 호출)에 같은 테스트를 걸면 gateway가 강제 종료돼 `Application shutdown complete`가 없어 실패. 고친 코드에서 통과.
- 새 실제 프로세스 테스트 `tests/test_web_shutdown_process.py`(빈 포트의 실제 gateway·가짜 runner·웹 소켓 연결): 정상 종료, 강제 종료(진행 중 요청), 감시 프로세스의 강제 경로·신원 불명 거부·분리 실행 옵션.
- Windows 전체 `pytest -q`: **6224 passed·66 skipped·0 failed**(실제 node.exe를 PATH 앞에 둠, basetemp 삭제함). POSIX 실제 프로세스 테스트는 이 PC에서 돌리지 못했고 CI의 Ubuntu가 돌립니다.
- 실제 PI·trial 인스턴스와 8787 포트는 건드리지 않았습니다.

## 남은 지적
- P2(2.5D 데모에서 종료 버튼 숨김)는 이 변경 범위 밖이라 남겨 둡니다.
- 완료 표시는 종료 접수 뒤 서버 연결 끊김을 기준으로 하므로 네트워크 단절과 구별하지 못합니다(manual에 명시).

Refs #560

새 설정의 `labhq init`에서 pipeline 코드를 공개 upstream에 PR로 기여할지 묻습니다. `y`(대소문자 무관)만 동의이며 Enter·`--yes`·불분명한 답은 끔으로 저장합니다.

- 기존 동의값과 upstream 대상은 `--force`에서도 보존합니다.
- dry-run은 설정·token·직원 홈을 만들지 않으며 새 instance마다 동의를 따로 받습니다.
- 거절 시 내용 수집·GitHub 호출을 막는 기존 경로를 검증했습니다. fork 자동 생성은 후속 작업입니다.
- 설치와 bioinfo-agent 연결 매뉴얼을 갱신했습니다.

검증: 기준 main `2ffb06d`에서 새 회귀 10 failed·3 passed → 수정 후 관련 129 passed. Windows 전체는 6,147 passed·66 skipped·45 failed: 격리된 홈에서 Node shim이 실행 파일을 찾지 못한 웹 테스트 45건입니다. 테스트 PATH에 Node 실행 파일을 직접 지정하자 해당 45건이 모두 통과했습니다. 보정 후 전체 재실행은 다른 레인의 전체 suite 때문에 미실행이며 로컬 전체 통과 조건은 아직 충족하지 않았습니다.

공개·diff·패치노트·목차 검사 통과. 코드 head `bbb5dd9`의 CI는 pytest 3.10·3.12·Windows·slow, clean-install, ro-crate-validator, changes 성공·readme skipped였습니다. 누락 기록을 넣은 `428dc3c`는 changes·patch-notes 성공이며 나머지는 기록 전용 변경으로 skipped입니다. 생략된 job을 실행 통과로 세지 않습니다.

Refs #300

남은 지적: 보정한 테스트 환경에서 Windows 전체 재실행이 필요합니다. fork 지원은 A의 main 반영 뒤 B 레인에서 진행합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

PR #573의 CP4 GitHub 전송 시점(P1)과 기존 bundle 갱신 예약(P2)을 `bbcf48d`에서 수정했습니다.

## 바뀐 것

- 검토 대기 초안은 GitHub terminal delivery를 만들지 않습니다. 옛 대기 이벤트도 게시하지 않습니다.
- 최종 결정과 전송 기록을 함께 저장합니다. 수용 뒤 보고서 commit·issue 종료, 거부·만료 뒤 실패 알림·issue 유지로 처리합니다. 재시작 시 남은 전송을 복구합니다.
- `workdir_id`가 없어도 기존 bundle이나 미완료 생성 기록이 있으면 receipt 갱신을 예약합니다.
- main `363cdd4`를 병합했습니다. 패치노트는 기존 들여쓰기와 따옴표로 감싼 7자리 SHA를 유지합니다.

## 확인

- `3392480`에서 새 회귀 5개 실패 → 수정 후 관련 68개 통과. GitHub는 mock transport만 사용했습니다.
- Windows 전체 pytest: 미실행. 다른 레인의 전체 실행이 연속돼 약 40분 대기 후 중단했습니다. 동시 전체 실행 금지를 지켰습니다.
- 공개 검사 통과. 패치노트·목차·diff 검사는 기록 커밋 뒤 실행합니다.

## 남은 지적

- P2는 가져온 report-only bundle의 재생성 예약·receipt 검증을 mock으로 확인했습니다. 현재 원본 탐색기는 모든 `workdir_id`가 없으면 bundle을 직접 복원하지 못합니다. 원본 복구 기능은 이번 수정에 추가하지 않았습니다.
- Windows 전체 통과 조건이 남아 push·인라인 답글·현재 수정 head의 CI 확인을 보류했습니다. 총괄이 전체 pytest 실행 순서를 확보한 뒤 이어갑니다.
- PI/trial 실행·재시작, 실제 보고서 게시, 리뷰 요청, 병합은 하지 않았습니다.

Refs #90

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론
웹의 **종료하기**에서 현재 일을 확인한 뒤 labhq를 끄거나 탭만 닫습니다. 2.5D·3D는 같은 확인 창을 씁니다.

## 바뀐 것
- 요청 제목·건수, PI 결정 대기, 이어 묻기·채용·마무리 표시.
- 진행 중 일반 종료 거부. 강제 종료는 중단·재개 안내 뒤 추가 확인. Enter 차단.
- PI 토큰 전용 API. 현재 gateway·runner의 PID 기록 확인 후 기존 `down`의 재확인·잠금·종료 순서 재사용.
- 원격 접속 시 서버 PC의 인스턴스를 끈다고 표시. 종료 중 새 쓰기 요청 차단.

## 확인
- base `363cdd4`: 새 Python 21 failed, Node 파일 실패. branch 관련 Python 66 passed·1 skipped, 새 Node 8 passed.
- Windows 전체: **6120 passed·66 skipped·45 failed**. Node shim의 격리 홈 경로 44건과 새 Node 파일 CI 등록 누락 1건. 등록 보완 후 실제 node.exe를 PATH에 둔 관련 파일은 **70 passed·1 skipped**. 로컬 전체 통과로 판정하지 않음.
- Node 전체 45개 파일 통과. 합성 미리보기에서 2.5D·3D, Enter 차단, 탭만 닫기 확인.
- 공개 검사·diff 검사 통과. PID·종료는 가짜 프로세스로 검증. 실제 PI/trial 인스턴스 변경 없음.

## 남은 지적
- PR 충돌로 CI가 생성되지 않았습니다. 충돌 해소·CI 실행은 총괄에게 넘깁니다.
- basetemp 삭제는 자동 승인 검토가 `blocked by policy`로 거부해 미완료입니다.
- 완료 표시는 종료 접수 뒤 서버 연결 끊김을 기준으로 하므로 네트워크 단절과 구별하지 못합니다(manual에 명시).

Refs #560

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

선행 연구에서 고른 논문을 CP1 전에 자문형 스킬로 준비하고 지정한 정규직의 해당 단계에만 장착합니다. Refs #549 (C).

## 바뀐 것

- `paper_skills`: DOI/PMID·방법 적합성·담당 단계, 최대 2편. 코드·데이터가 있는 논문 우선.
- recruiter 변환·인재풀 hash 재사용. 계약·수습 없이 실행하며 변환 실패·미설치는 경고 후 계속.
- Claude 임시 plugin·Codex 작업 폴더 스킬. 매 turn 뒤 제거하고 논문 ID·Paper2Agent commit·hash·장착 단계를 부록과 묶음에 기록.
- 스킬이 선택된 일반 요청은 계획의 담당 단계대로 실행합니다. 단독 경로가 담당 직원을 바꾸는 일을 막기 위한 처리입니다.

## 확인

- 기준 main `0cf446a`: 새 회귀 14 failed → branch 14 passed.
- 관련 pytest 275 passed, 2 skipped. Node 43개 파일 통과.
- 공개 검사·diff 검사 통과. 계약 고정값·runner 호출명 갱신 후 관련 182 passed, 1 skipped.
- Windows 전체 첫 실행: 6,100 passed, 66 skipped, 53 failed. 계약 고정값/호출명과 Node shim 환경 원인을 처리했고 전체 재검증은 순차 대기 중입니다.

## 남은 지적

- 스킬은 변환한 러너의 로컬 인재풀을 사용합니다. 담당 직원이 다른 러너에 있으면 장착 경고를 남기며 원격 전송은 구현하지 않았습니다.
- 실제 Paper2Agent 변환·PI/trial 실행·설정 변경·리뷰 요청·병합은 수행하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

완료 요청을 현재 instance에서 키워드로 검색한다. `labhq recall "유전자"`와 client token으로 인증한 `GET /api/knowledge/search`는 최대 5건·본문 합계 2,000자를 돌려준다.

## 바뀐 것

- gateway 전용 SQLite 색인과 terminal 트랜잭션의 재처리 표식. 단일 worker가 checkpoint부터 25건씩 backfill한다.
- 한국어 3자 이상 FTS5 trigram, 2자·미지원 환경은 최근 색인 2,000건의 literal LIKE 검색.
- 쓰기·검색 시 restricted marker, private reference·비밀값 가림. 현재 정책으로 재검사하고 판정 불가 자료는 제외한다.
- 요청 요약·step 상태·오류 분류·출처만 사용한다. 원본 로그·산출 파일·reference_original을 읽지 않는다.
- doctor에 색인 수·마지막 성공·fallback을 표시하고 매뉴얼에 사용법을 추가했다.

## 확인

- 기준 main `f6d4fdf`: 새 회귀 **11 failed**. branch: **11 passed**, 라운드 기록 포함 focused **39 passed**.
- Windows 전체 pytest: **6,082 passed, 66 skipped** (719.26초).
- 공개 검사·diff 검사 통과. PR #574 패치노트·상태 원본 검사는 기록 커밋에서 수행한다.

## 남은 지적

- prompt 자동 주입·reflection·registry는 후속 범위다. 자료 전체를 읽거나 실패 원인을 새로 추론하지 않으며 오류 메타데이터가 없으면 `unknown`으로 남긴다.
- PI/trial 실행·설정·배포, 리뷰 요청·인라인 답글·병합은 하지 않았다.

Refs #60

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

opt-in 연구 요청의 기존 **검토 완료**를 CP4 수용으로 연결합니다. 별도 승인 카드는 만들지 않습니다.

## 바뀐 것

- 생성 시 설정·계약 version 고정. 리뷰 accept·보고서 검사 뒤 초안 revision과 본문·감사·claim hash를 저장합니다.
- 유효한 PI receipt 전에는 `review_pending`. 거부·만료·취소·hash 변경은 완료하지 않습니다. 재시작은 저장 초안을 재사용합니다.
- 웹·CLI 결정에 revision/hash를 묶고 bundle·verify에 초안과 receipt를 연결합니다. 수용은 게시·전송·HPC 권한이 아닙니다.
- 기준 `1b41209`. #565·#556·#567·#571 포함. 총괄의 순서 해제 지시에 따라 착수했습니다.

## 확인

- 새 회귀: 기준 main에서 1 failed → branch 통과. 관련 Python 330 passed·4 skipped, 최종 집중 56 passed. Node 43개 파일 통과.
- Windows 전체 pytest: **UNVERIFIED**. 다른 레인의 전체 실행이 연속으로 이어져 동시 전체 실행 금지를 지켰습니다. CI 결과와 구분합니다.
- 공개·diff 검사 통과. 패치노트·상태 문서를 기록하고 patch-notes·notes-index를 검사했습니다.
- 매뉴얼 122,610 → 123,414자.

## 남은 지적

- 레인 임시 폴더 삭제가 자동 승인 검토의 `blocked by policy`로 차단돼 정리를 인계합니다.
- 로컬 Windows 전체 통과 조건은 미충족입니다. PI/trial live 검증·배포·CP3·CP4 수정 재실행은 범위 밖입니다.
- 봇 리뷰 요청·대응·병합은 총괄에게 넘깁니다.

Refs #90

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #566의 지정 지적 5건을 수정했다. 정리본은 확정된 요청 묶음 옆에 저장하며 PI 검토 완료 뒤 게시한다. Refs #558.

## 바뀐 것

- 새 정리본: `requests/<id>-editor/`. MANIFEST·RO-Crate를 다시 만들지 않아 원래 묶음을 그대로 검증한다.
- HTTP·WebSocket 승인과 업로드 진입점에서 PI 검토를 확인한다. 자동 게시도 검토 완료 뒤 진행한다.
- 재시작하면 저장된 문서·이미지로 게시 승인만 복구한다. 편집자는 다시 실행하지 않는다.
- 공개 게시를 금지한 공개 프로젝트는 local로 상속한다.
- Contents API 경로와 Markdown 링크에 한글·공백·특수문자를 인코딩한다.

## 확인

- 수정 전 회귀 8건 모두 실패. 수정 후 관련 Python 155 passed·2 skipped, Node 44개 파일 통과.
- CRLF 초안 복구도 수정 전 실패를 재현했고 바이트 읽기로 보완한 뒤 관련 회귀 10건이 통과했다.
- 묶음 생성 전후 바이트 동일·verify exit 0, 저장된 초안의 내용·mtime 보존, 검토 전 승인 거부를 확인했다.
- Windows 전체: 6,123 passed·66 skipped (12분 36초). 게시 카드 수명·semantics 재확인 55건도 통과했다.
- 코드 포함 head `ec66bf8`의 [CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38398260303): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 생략.
- 공개·패치노트·목차 검사 통과. 임시 테스트 폴더 삭제는 자동 승인 검토에서 차단되어 경로를 로컬 보고에 남겼다.
- 실제 결과 저장소 생성·업로드, PI/trial 실행·재시작, 리뷰 요청, 병합은 수행하지 않았다.

## 남은 지적

- 4257278189: 편집자 호출 예외 때 기본 정리본 보존.
- 4257278194: terminal 이후 편집자 비용과 완료 기록 불일치.
- 4257405941: 질문 대기 비활성화 시 기본 저장 선택 누락.
- 4257405952: 편집자가 빠뜨린 기본 문서 링크 보존.
- 4257405959: 잘못된 저장소 입력 뒤 3D 카드 재활성화.
- 4257499944: 취소된 요청의 정리본 생성.
- 4257697760: 저장 선택 approval의 원자적 선점.
- 4257806565: 여러 파일의 원자적 게시·부분 실패 복구.
- 4257806570: approval snapshot의 image base64 분리.

기존 한국어 placeholder 지적 4257278201은 현재 브랜치에 이미 반영되어 이번 수정에서 제외했다.

## 변경

PR #553의 Paper2Agent 직원 설치 기능을 main `1b3b0cb`와 병합했다. 충돌한 doctor 인자에는 Paper2Agent 검사·수정 옵션과 GitHub 연결 검사 옵션을 모두 남겼다.

- 직원 전용 설치, plugin·skill 읽기 예외, 설치 commit pin, Codex recruiter 조건부 필드를 보존했다.
- 사용자 홈 접근 guard와 main의 기여 동의·기타 변경을 보존했다.
- 병합 커밋 `f912336`의 패치노트 SHA는 따옴표로 감싼 7자리 문자열이다.

## 검증

- 관련 테스트: 427 passed, 1 skipped.
- Windows 전체 pytest: 6097 passed, 66 skipped (751.84초).
- pytest 임시 폴더 삭제 완료.
- 공개·diff·패치노트·목차 검사를 수행하며, 원격 CI 결과는 PR checks에 기록한다.

## 남은 지적

- 전체 테스트는 통과했지만 subprocess 출력 디코딩의 thread warning이 1건 있었다. 이번 충돌 해소 범위에서는 수정하지 않았다.
- 실제 직원 CLI·PI·trial 실행, 리뷰 요청 댓글, PR 병합은 하지 않았다.

Refs #549

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #572 · #560의 15번: 2.5D 메신저에서 위로 스크롤하면 저장된 첫 기록까지 조회합니다.

## 바뀐 것

- PI 전용 `GET /api/messenger`: 전체·요청별 조회, 이전 방향 seq 커서, 기본 40·최대 200건.
- SQLite `messenger_events`에 질문·답·직원 메시지·보고 알림을 보관합니다. 재전송 버퍼와 직원 log/feed 상한은 유지합니다.
- 불러오는 중·재시도·처음 표시, 페이지 중복 제거, 스크롤 위치 유지. 3D는 변경하지 않았습니다.
- `docs/manual.md`의 웹 사무실·API 설명을 갱신했습니다.

## 확인

- CI(`902d96a`): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 생략. [실행 기록](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38393753895).

- 새 회귀 테스트: origin/main에서 Python 4건·Node 3건 실패, branch에서 통과.
- 관련 Python 73건, 공통 웹 실행 검사 69 passed·1 skipped, Node 전체 44개 파일 통과. Windows 전체 pytest: 6,065 passed·66 skipped(12분 13초).
- Chrome 합성 기록 125건: 40→80→120→125건, 첫 메시지·끝 표시·새로고침 후 재조회 확인.
- 공개 검사·diff 검사 통과. 패치노트·목차 검사도 통과했습니다.

## CSO가 보는 범위

메신저 전체가 자동으로 전달되지는 않습니다. 직원 질문은 질문·시도·선택지·참조와 해당 요청(4,000자)·계획(6,000자), PI 이어 묻기는 해당 요청·보고서(6,000자)·최근 완료 답변 3건을 받으며 재개한 해당 직원 세션의 맥락도 사용합니다.
근거: `labhq/orchestrator/cso.py`의 `answer_ask`, `run_followup`; 질문·답 저장은 gateway SQLite `state(kind='ask')`, PI 이어 묻기는 `state(kind='request').followups`입니다.

## 남은 지적

- 업그레이드 전에 이벤트 상한으로 이미 삭제된 메시지는 복구되지 않습니다. 기존 DB에는 남아 있는 이벤트만 처음 한 번 옮깁니다.
- 임시 basetemp 삭제는 자동 승인 검토의 `blocked by policy`로 차단됐습니다. 삭제 완료로 판정하지 않습니다.
- 봇 판단과 병합은 개발 총괄에게 넘깁니다.

Refs #560

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

승인 복구 후보가 없는 environment 실패를 시설팀이 요청·논리 단계당 한 번 진단합니다. 원 실패와 hint는 유지합니다.

## 바뀐 것

- 기존 allowlist 복구 우선. consult·복구 실패·거절·timeout·재실패에는 진단을 추가하지 않습니다.
- ask를 저장해 재시작·재연결 중복을 막고 기존 상한·예산·read-only 제한과 저장된 기한을 적용합니다.
- 서명·오류 발췌·doctor 요약만 사용합니다. 원 요청·계획·세션을 넘기거나 LLM 답변을 실행하지 않습니다.
- 기준 `60ffb1b`, 선행 FACILITY35 #567 포함. CP490 순서는 총괄 결정으로 해제됐습니다.

## 확인

- 새 회귀: 기준 코드에서 1 failed → 구현 후 통과. 관련 테스트 305 passed. 최종 코드 커밋의 새 테스트 31 passed.
- Windows 전체 pytest: 미실행(UNVERIFIED). 다른 레인의 전체 실행 때문에 10분 대기 후 중단했습니다. 동시 전체 실행 금지를 지켜 총괄에게 인계합니다.
- 공개 검사·diff 검사 통과. 레인 전용 임시 폴더 삭제는 자동 승인 검토의 blocked by policy로 차단돼 정리를 인계합니다. manual 121,424→121,751자.

## 남은 지적

- 실제 설치·모델 호출·PI/trial 시운전은 수행하지 않았습니다. 봇 리뷰 요청·판단·병합은 총괄에게 넘깁니다.

Refs #35

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #568의 P1(4257865735)을 `03c3415`에서 수정했다. 준비 중 정지했다 재개하면 CLI 실행 직전에 입력 hash를 갱신한다.

## 바뀐 것

- spawn 직전 pause gate를 유지하고 project·upstream 입력을 다시 스캔한다. gate를 앞으로 옮길 때 생기는 준비 중 pause 누락을 피한다.
- 스캔 중 새 pause가 들어오면 다시 기다린 뒤 스캔한다. 실행 중 입력 변경은 갱신한 hash를 보존하고 `changed_during_step`으로 표시한다.
- PR의 기존 세션 id·재개 안내·steer 재전송·빠른 재개 보존 수정도 유지한다.

## 확인

- 회귀 4건: `7363c30`에서 모두 실패, 수정 후 통과. 실제 합성 CLI가 읽은 내용과 manifest hash를 비교한다.
- 관련 pytest: **61 passed**. Windows 전체: **5,993 passed, 66 skipped**(11분 53초).
- 공개 검사 통과. 패치노트·목차 원본·diff 검사 후 push한다. CI는 최종 head의 각 job을 확인한다.

## 미해결

- basetemp 한 폴더의 삭제가 자동 승인 검토에서 `blocked by policy`로 차단됐다. 경로는 로컬 보고에 남긴다.
- 리뷰 요청·병합·PI/trial 실행과 재시작은 하지 않는다.

Refs #59

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

Windows CI에서 진행 중인 데모 요청을 30초 `wait_for`가 취소하던 테스트를 완료 신호로 동기화했습니다. 제품 코드 변경은 없습니다.

## 바뀐 것

- 같은 파일의 서버 준비·runner 등록 폴링을 실제 startup·등록 이벤트로 대체했습니다.
- CLI에만 clock/sleep 대역을 주고 요청 task를 기다립니다. 실제 서버·runner·URL 경고·done 검증은 유지합니다.
- 비종료 상태로 끝난 요청은 즉시 실패하며 교착 검출용 바깥 상한은 120초입니다.

## 검증

- CPU 점유 프로세스 4개와 pytest를 동일 코어에 고정: 수정 전 실패 0/5(로컬 미재현), 최종 수정 후 실패 0/30.
- 관련 파일 14 passed. CI 실패 근거: runs 38363193704·38383002690의 첫 실행 로그.
- Windows 전체: **6015 passed, 66 skipped**. 첫 실행의 Node shim 오류 42건은 기존 node.exe를 PATH에 지정해 해소했고 전체를 다시 실행했습니다.
- 포트 8787·55921 접근 차단. TEMP 아래 basetemp 한 폴더 사용 후 삭제 확인.
- 공개 검사·patch-notes·notes-index·diff 검사 통과.
- [CI run 38390129364](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38390129364) (`f068eea`): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 skip.

## 미해결

- 자동 봇 지적 판단과 병합은 개발 총괄이 이어받습니다.

Refs #539

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #566에 main `1b3b0cb`를 병합했다. 두 화면의 저장 위치 카드와 검토 흐름을 보존하고 PI 검토·확인 완료 뒤 게시 버튼을 활성화한다. 시설팀과 편집자를 함께 등록하고 배지·직원 표를 갱신했다.

## 확인

- 최신 main 재병합 뒤 관련 Python 307 passed, 3 skipped. Node 44개 파일 통과.
- 정규직 13명·3D 등록 14개 검증 갱신 뒤 관련 112 passed, 1 skipped.
- CI에서 편집자 task를 고르던 기존 회귀를 direct task 선택으로 수정. 관련 29 passed, 2 skipped; 검증 조건은 유지.
- Windows 전체 pytest: 6,026 passed, 66 skipped (11분, 첫 merge 직후).
- Node 실행 파일을 PATH 앞에 두어 격리 홈에서 실행 경로가 끊기지 않게 했다.
- 공개·패치노트·목차·diff 검사 통과. 현재 head CI 결과는 PR checks에 기록한다.

## 미해결

- 임시 폴더 삭제는 자동 승인 검토의 `blocked by policy`로 차단됐다.
- 리뷰 요청·PR 병합·PI/trial 실행은 하지 않는다.

Refs #558

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #569에서 PI 글 규칙, 승인 설명 세 필드, 원문 펼침을 추가했다. PowerShell here-string의 삭제 예문이 승인 경고를 만드는 오탐을 수정했다. Refs #557.

## 바뀐 것

- 직원 공통 지침·CSO 카드·비서실장·승인 MCP에 쉬운 한국어와 ‘무엇 → 왜 → 결과’ 순서를 명시했다.
- 도구·HPC·예산·채용 카드에 `pi_summary`, `pi_reason`, `pi_outcome`을 채운다. 원문과 승인 동작은 보존한다.
- 단순 파일·폴더 명령은 규칙으로 설명한다. 복합 명령은 직원의 한국어 설명을 쓰거나 확인이 필요하다고 알린다. 안전 범위를 추측하지 않는다.
- 메신저·카드의 제목·굵게·표 기호를 제거하고 코드를 접는다. 영어 원문은 한국어 안내 뒤 펼침에 보존한다.
- #525·#535의 보수적인 구문 판독을 재사용한다. 실행 문자열·치환·알 수 없는 프로그램은 기존처럼 원문을 검사한다.

## 확인

- main `a709945`: 새 회귀 Python 8건·웹 7건 실패. 기존 판정 유지 사례 3건 통과.
- 관련 Python 383 passed, 7 skipped. Node CJS 43개 파일 통과.
- Chrome 합성 화면: 기본 상태에서 원문 숨김, ‘자세히’에서 명령·코드 원문 확인. PI·trial 인스턴스는 실행하지 않았다.
- Windows 전체 pytest: 6017 passed, 66 skipped(11분 42초, 모의 e2e 포함). 전체 실행은 동시에 하나만 돌렸다.
- 코드 head `fc9ee27`의 [CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38385563298): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 생략.
- 첫 전체 실행은 47 failed, 5969 passed, 66 skipped였다. 프롬프트 기준값·질문 규칙·새 Node 테스트 등록을 고쳤다. Node 42건은 격리 홈을 참조한 사용자 shim 문제여서 테스트 세션의 PATH에 실제 실행 파일 경로를 지정했다.

## 오탐 판단

- `New-Item -ItemType Directory -Force .tmp` 단독은 기존에도 허용된다.
- here-string 안의 `Remove-Item results -Recurse`를 파일에 쓰는 합성 사례는 기존에 재귀 삭제로 잘못 판정됐다. 수정 뒤 허용된다.
- 뒤에 실제 삭제가 있거나 here-string을 실행하는 경우는 계속 승인을 요구한다.
- #557에는 시운전 명령의 앞부분만 있다. 실제 카드 뒤에 삭제가 있었는지는 이 자료로 확정하지 못했다.

## humanizer 검토

| 방식 | 비용·지연 | 품질·권고 |
|---|---|---|
| 매 메시지에 외부 humanizer | 메시지마다 진단·재작성 모델 호출 추가 | 문체 점검에는 유용하나 명령 의미를 검증하지 못한다. 상시 적용하지 않는다. |
| 공통 글 규칙 + 규칙 설명 | 추가 모델 호출 없음 | 숫자·원문을 보존하고 같은 입력을 일관되게 표시한다. 이 PR의 기본 방식. |
| 비서실장 추가 요약 | 부족한 설명마다 모델 호출·응답 대기 추가 | 복합 명령의 의도를 풀기 쉽지만 추측 위험이 있다. 현재 직원은 Sonnet 사전 브리핑 담당이며 별도 번역 호출은 넣지 않았다. |

권고: 규칙과 직원의 기존 답변에서 설명을 만들고, humanizer는 최종 보고서 표본 점검에만 쓴다. 호출 비용·지연 수치는 측정하지 않았다.

## 남은 지적

- 복합 명령의 비서실장 별도 요약과 임의 영어 답변의 자동 번역은 미구현이다. 한국어 안내를 번역 결과로 표시하지 않는다.
- 시운전 원문 전체를 받으면 해당 카드의 오탐 여부를 따로 확정해야 한다.
- 임시 폴더 삭제가 자동 승인 검토에서 `blocked by policy`로 거부됐다. 후속 검증은 같은 임시 루트의 새 하위 경로에서 실행하며 정리 완료로 보고하지 않는다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #532의 미확인 지적 네 건을 main `3359c6e`에서 재현하고 수정했다.

## 바뀐 것

| 지적 | 수정 | main → branch |
|---|---|---|
| P1 4244965036 | CLI 준비 뒤 spawn 직전에 pause/halt를 다시 확인 | 2 실패 → 통과 |
| P1 4244965042 | CLI 초기 이벤트의 session id 수신. 없으면 결과·로그에 같은 세션 재개 불가 안내 | 2 실패 → 통과 |
| P2 4244965047 | dispatch 알림과 task frame 사이에 온 steer도 frame 뒤 재전송 | 1 실패 → 통과 |
| P2 4255659855 | halted 결과 공개 후 waiter 등록 전 resume을 저장하고 대기 진입 시 반영 | 1 실패 → 통과 |

세션 id가 없을 때 원래 지시·이전 결과를 재개 프롬프트로 넘기는 기존 동작은 main에서도 통과했다. 해당 경로는 유지했다. 기존 수락 시 재전송 테스트는 frame 직후 재전송까지 정확히 검사하도록 갱신했다.

## 확인

- 새 회귀: main 6 failed / 1 passed, 수정 후 관련 테스트 60 passed.
- Node: 41개 CJS 파일 통과. 공개 검사·diff 검사 통과.
- Windows 전체 pytest: **5,989 passed, 66 skipped**. basetemp 한 폴더를 사용하고 삭제했다.
- 첫 전체 실행의 웹 테스트 41건은 개인 Node shim이 격리된 홈에서 실행 파일을 찾지 못해 실패했다. 설치된 `node.exe`를 테스트 프로세스 PATH 앞에 둔 뒤 41건과 전체 suite가 통과했다. 코드·홈 격리는 그대로다.
- 코드 검증 CI(`718b59e`): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 skip. [실행 기록](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38383182304). 최종 기록 커밋의 CI 결과는 PR checks에서 확인한다.
- 매뉴얼 직원 제어 절과 브랜치 패치노트·상태 기록 갱신.

## 남은 지적

- 실제 직원 CLI 실측은 하지 않았다. PR #532에서 수용한 gateway 재시작 시 halt park 소실 등의 한계는 그대로다.

Refs #59

🤖 Generated with Codex (gpt-6) for the labhq dev lead

# PR #553 main 충돌 해소

- main `6150223`을 merge commit `cb564bf`로 반영했다.
- 직원 전용 설치·open_reads·pin 검사·Codex recruiter 조건부 필드와 main의 홈 격리 guard를 보존했다. 설정 경로와 명시한 설치 대상도 I/O 전에 검사한다.
- 관련 165 passed·3 skipped, 추가 홈 보호 회귀 6건 포함. Windows 전체 재실행은 5977 passed·66 skipped(704.45초), 공개·diff 검사 통과.
- 첫 전체 실행의 41건 실패는 홈 격리 후 로컬 Node shim이 실행 파일을 찾지 못한 경우다. 기존 node.exe 경로를 테스트 PATH 앞에 넣어 웹 66 passed·1 skipped와 전체 통과를 확인했다. guard는 유지했다.
- CI 최종 결과는 PR #553 본문에 기록한다. 리뷰 요청·PR 병합·실제 직원 실행은 하지 않았다.
- pytest 임시 폴더 정리는 완료하지 못했다. 남은 경로는 로컬 인계 기록에 남겼다.

## 결론

`labhq_ask(to="facilities")`를 시설팀 직원이 받습니다. Codex gpt-6-sol·read-only로 진단만 답하고 요청 안에서 자기 상담 session을 이어 씁니다.

## 바뀐 것

- doctor 검사명·상태와 검증된 오류 파일 발췌를 전달합니다. 셸·쓰기·설치·HPC·plugin·외부 MCP는 열지 않습니다.
- 기존 엔진 준비 검사, 미등록·미접속 시 CSO fallback, 제한된 refs·force_engine 경계를 유지합니다.
- 메신저 이름은 시설팀이며 3D roster와 기존 설치 적용 절차를 갱신했습니다. 자동 실패 진단·canary는 범위 밖입니다.
- 기준 `9eeed31`: 선행 #564·#552와 #562 변경을 보존했습니다.

## 확인

- 새 회귀 6건: 기준 코드에서 실패 → branch에서 통과. fallback·refs 검증을 포함한 관련 테스트 129 passed.
- 배지 직원 수 고정값을 갱신한 뒤 관련 테스트 43 passed.
- Node 메신저·직원 표시 검사 2개와 Chrome 아트랩 확인, 공개·integrations·diff 검사 통과.
- 로컬 Windows 전체 pytest는 실행 슬롯을 확보하지 못해 미실행입니다. 로컬 전체 검증과 임시 폴더 정리를 총괄에게 인계합니다. CI 결과는 PR checks에서 별도로 확인합니다.
- manual 119,658→120,345자; 한·영 README는 직원 배지 갱신으로 각각 22자 증가.

## 남은 지적

- 실제 Codex 상담과 기존 설치 적용은 PI·총괄의 후속 단계입니다. 봇 리뷰 판단·병합은 총괄에게 넘깁니다.

Refs #35

🤖 Generated with Codex (gpt-6) for the labhq dev lead

연구 요청의 저장 위치를 CP1 전에 고르고, 편집자가 만든 로컬 정리본을 웹에서 확인한 뒤 GitHub에 게시합니다. 확인 질문이 있으면 저장 위치를 같은 카드에 넣습니다.

## 변경
- 로컬 기본 / 새 저장소(공개·비공개) / 기존 저장소. 프로젝트 지정·재개 시 중복 질문 없음.
- editor 정규직과 README·진행 기록·재현 방법·출처·그림·표 정리본.
- 기존 redaction 재사용. 통제 입력 요청은 로컬 유지. 웹 검토 후 게시 또는 자동 게시 설정.
- 두 웹 화면·편집자 3D 표시·매뉴얼. Notion은 다음 단계.

## 검증
- Windows 전체: **5,951 passed, 66 skipped** (11분 16초). 모의 요청부터 가짜 GitHub 게시까지 포함.
- 관련 회귀: 163 passed, 1 skipped. 웹 Node 42개 파일 모두 실행.
- 신규 테스트는 main 기준 모듈 부재로 실패했고 구현 후 10 passed.
- 공개 검사·문서 생성 검사·patch-notes·notes-index·diff 검사 통과.

## CI와 인계
현재 PR은 main과 충돌(DIRTY)합니다. 현재 head의 CI는 생성되지 않았으며 이전 head의 결과를 통과 근거로 쓰지 않습니다.

| job | 현재 head 결과 |
|---|---|
| changes | 미생성 |
| patch-notes | 미생성 |
| readme | 미생성 |
| pytest (3.10) | 미생성 |
| pytest (3.12) | 미생성 |
| pytest-windows | 미생성 |
| clean-install | 미생성 |
| ro-crate-validator | 미생성 |
| pytest-slow | 미생성 |

## 남은 지적
- 임시 테스트 폴더 `pt-store558`·`pt-store558-full` 삭제는 자동 승인 검토의 `blocked by policy`로 차단되어 남아 있습니다.
- 지시서의 역할 분담에 따라 main 충돌 해소와 그 뒤 CI 확인은 총괄에게 넘깁니다. 완료 조건 중 현재 head CI는 미충족입니다.
- 그림 안의 개인정보는 PI가 검토합니다. 원본 입력·미보고 산출은 게시 대상에서 제외합니다.
- 실제 GitHub 저장소 생성·게시, PI/trial 실행, Notion, 리뷰 요청·지적 대응·병합은 수행하지 않았습니다.

Refs #558

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

요청은 보고서 작성 뒤 PI 검토·확인으로 마무리합니다. 검토 대기 요청은 자동 타이틀 선택과 목록 정렬에서 미완료로 취급합니다.

## 바뀐 것

- 화이트보드 완료 표시·보고서 열기, 알림 한 번, 직원 완료 표시와 결정 탭 검토 카드를 연결했습니다.
- 웹·`labhq review-done <id>`가 PI 확인 시각·감사 기록·이벤트를 한 번만 저장합니다. 실행 결과는 보존합니다.
- 실패·취소·거부는 결과 확인 카드로 표시합니다. 카드에는 기한·자동 승인이 없고 실행 중 목록과 분리해 다른 작업을 막지 않습니다. 기존 종료 요청도 PI 확인 기록이 없으면 카드가 나타납니다.
- 질문 대기와 실제 PI 승인 대기를 구분하고 시설팀·CSO 등 메신저 이름을 표시합니다.
- 늦은 roster에도 카드 그림을 갱신합니다. 직원 id 대소문자를 맞추고 모르는 직원은 중립 그림, `c_` 파견직은 병아리로 표시합니다.
- 예산 카드를 문장으로 풀고 2.5D·3D·CLI 비용을 `미집계된 비용`으로 통일했습니다. 매뉴얼에 요청 수명을 추가했습니다.

## 실행한 것

- 새 회귀: main에서 Node 10건·pytest 5건 실패, 수정본에서 모두 통과.
- 전체 Node 42개 파일 통과. 수정 확인 Python 143 passed·1 skipped.
- Windows 전체 pytest: 5,916 passed, 66 skipped.
- 공개 검사·diff 검사 통과. 테스트 state·TEMP 분리, 기존 서비스 포트 차단. PI·trial 인스턴스와 store는 건드리지 않았습니다.

## 미해결

자동 봇 리뷰 판단과 병합은 개발 총괄이 이어받습니다.

Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

#549 D: Paper2Agent를 직원 설정에 설치하고 doctor가 설치·가시성·고정 commit을 검사한다. PR #553의 2차 지적 P1·P2를 수정했다.

## 바뀐 것

- Claude는 recruiter에만 plugin을 연결하고 Codex는 실제 recruiter 엔진과 직원 CODEX_HOME을 따른다. 상위 설정 폴더 보호를 유지한다.
- `94deac0`: 설정한 pin과 설치본의 `.labhq-source.json` commit이 다르면 doctor fail. `--fix`가 설정한 commit을 설치하고 재검사한다. pin이 없으면 기존 동작을 유지한다.
- recruiter YAML의 `allow_skills`를 제거하고 기존 Claude adapter 처리를 사용한다. 엔진을 codex로 바꿔도 roster를 읽는다.
- `3695d43`: 기존 plugin 테스트도 recruiter의 adapter 허용을 반영한다. 격리·plugin·skill 활성화 검사는 유지한다.

## 실행한 것

- `7b38dd9`에서 새 테스트 9건 중 7건 실패·2건 통과. 수정 후 관련 테스트 162 passed·3 skipped.
- Windows 전체 pytest 최종: **5648 passed·66 skipped**, 692 warnings. 첫 실행의 plugin 기대값 실패를 수정했고 semantics 기록 실패는 재실행에서 통과했다.
- 공개·diff·패치노트·목차 검사 통과.
- push 후 PR은 `CONFLICTING`, 현재 head의 CI checks는 0건이다. changes·patch-notes·pytest 3.10/3.12·pytest-windows·pytest-slow·clean-install·ro-crate-validator·readme 모두 미실행이다. 이전 head의 CI 성공은 이번 결과에 포함하지 않는다.

## 남은 지적

- main 충돌 해소와 CI 확인은 총괄에게 넘긴다. 이번 위임 범위에서는 충돌을 풀지 않았다.
- 이번 pytest 임시 폴더 삭제는 실행 정책이 차단했다.
- 실제 직원 CLI·PI·trial은 실행하지 않았다. 임시 홈과 모의 설치로 검증했다.

Refs #549

🤖 Generated with Codex (model slug unavailable) for the labhq dev lead

## 결론

dev_log 저장소의 private 여부를 doctor·gateway 시작 시 확인한다. CI에서 실패한 direct 요청의 기록 경로 설명을 복원했다.

## 바뀐 것

- GitHubClient·token_env·api_url로 공개 여부를 조회한다(전체 5초 제한). public·unknown은 경고하며 gateway PI 로그에는 인스턴스마다 한 번 알린다.
- 쓰기 직전 visibility guard·allow_public·#69 종료 조건은 유지한다.
- manual 라운드 기록 절에 direct 요청의 `outputs/` 산출 경로와 최대 200개 상한을 명시했다. 기존 dev_log 설명과 테스트는 유지했다(118,501→118,533자).

## 실행한 것

- 기존 기능 관련 테스트: 104 passed.
- 문서 수정 후 `tests/test_direct_outputs.py`: 19 passed·2 skipped.
- Windows 전체 `pytest -q`: 5,902 passed·66 skipped. 다른 전체 suite가 끝난 뒤 단독 실행했다.
- 공개 검사·diff·패치노트·기록 목차 검사 통과.

## 미해결

- 전용 임시 폴더 삭제는 경로 확인 후에도 자동 승인 검토가 `blocked by policy`로 거부했다. 정리 미완료.
- 실제 dev_log 저장소 조회·PI/trial 인스턴스 실행은 하지 않았다.
- CI 최종 결과 확인 뒤 리뷰 판단·병합은 총괄이 이어받는다.

Refs #69

## 남은 지적

없음.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

앞 450자가 같고 끝이 다른 도구 호출 8개가 같은 preview로 잘려 폭주 경고를 내던 오탐을 고쳤다(#59 ②).

## 바뀐 것

- Claude·Codex·Antigravity의 여섯 tool 경로가 preview 전에 도구 이름과 canonical 입력 앞 4,096자를 SHA-256으로 계산한다.
- JSON 키 순서를 정렬하고 문자열 공백을 보존한다. 추가 원본 전송은 없으며 입력 미보고·잘린 legacy 이벤트는 반복 횟수를 끊는다.
- 단일 trip·연속 오류 판정·shadow 동작을 유지한다. manual 안전 장치·한계 절을 갱신했다(118,441 → 118,899자).
- 기준 `afbcd2e`: 선행 #532 직원 제어·#548 실행 상태 표시를 보존했다.

## 실행한 것

- 새 오탐 회귀 6건: 기준 main에서 6 failed, 수정 후 통과.
- 관련 pytest: 114 passed, 1 skipped. JSON 키 순서·공백·입력 미보고·4,096자 경계·multi-line error·runner shadow 유지·실측 스트림 포함.
- Windows 전체 pytest 1회: 5,948 passed, 66 skipped, 2 failed. Codex fixture는 새 hash 필드 기대값을 추가한 뒤 위 관련 검사에서 통과. 나머지는 semantics 내부 테스트의 `disabled.json` PermissionError이며 단독 재검사에서 1 passed로 재현되지 않았다.
- 공개 검사·diff 검사 통과.

## 미해결

- canonical 입력 앞 4,096자 이후 차이는 기존 정책대로 구별하지 못한다.
- 최종 Windows 전체 통과 판정은 CI 결과를 확인한다. 로컬 전체 1회 결과를 통과로 세지 않았다.
- 레인 임시 폴더 삭제는 실행 환경의 정책으로 차단돼 정리를 인계한다.
- 리뷰 판단·병합은 개발 총괄에게 인계한다.

Refs #59

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

요청별 그림자 비교 보기를 추가하고, 다른 설정 파일로 실행 중인 서버를 `up`이 정상으로 받아들이던 결함을 고쳤다.

## 바뀐 것

- `semantics report --request <id>`: 저장된 그림자와 현재 단계·보고 산출·승인 결정을 한국어 평문/JSON으로 비교한다. 없거나 손상된 기록은 알리고 재계산하지 않는다.
- 일반 요청도 ab 기록 대상이다. 재시작·재개도 같은 종료 hook을 쓴다. 실제 누락은 다른 checkout의 off 설정으로 실행한 정황이며, 오늘 병합된 보고 집계 수정과는 무관하다.
- `up`은 서버가 응답하더라도 실행 설정 불일치를 검사한다. 살아 있는 다른 설정의 PID 기록도 보존한다.

## 실행한 것

- main 11845c0에서 최초 새 회귀 7건 실패. 수정 후 관련 327 passed, 1 skipped; 기능 제거·최종 요청별 회귀 15 passed.
- CI c637146 ([run 38374699725](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38374699725)): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 생략. Windows 전체 5,952 passed·34 skipped.
- 공개·패치노트·목차 검사 통과. manual 의미 모델 절 갱신(118,283 → 118,701자).

## 미해결

- 로컬 전체 pytest는 다른 레인의 전체 검사가 연속 실행돼 시작하지 못했다. 직렬 실행 대기를 종료하고 draft로 인계한다.
- 올바른 ab 설정의 기록 생성은 main에서도 정상이다. 'main에서 기록 생성 자체가 실패'하는 결함은 재현하지 못했다. 설정 충돌과 요청별 보기 부재를 재현했다.
- 임시 테스트 폴더 삭제가 실행 환경의 자동 승인 검토에서 거부됐다(blocked by policy). 우회 삭제하지 않았다.
- PI 설정·상태·인스턴스를 바꾸지 않았다. 실행 설정 선택은 총괄이 확인해야 한다. 과거 미관측 그림자는 복원하지 않는다. 웹 칸은 추가하지 않았다.

Refs #559

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

읽기 전용 직원도 자기 task의 `outputs/`·`.tmp/`에 파일을 저장합니다. 검토 대상과 앞 단계 산출물은 계속 보호합니다.

## 바뀐 것

- Claude: Write·Edit를 승인 게이트로 보내 자기 산출·임시 파일만 승인 없이 허용합니다. 셸과 직원 MCP는 열지 않습니다.
- Codex: 읽기 기본 권한에 두 쓰기 루트만 추가한 workspace-write profile을 씁니다. 프로젝트 `--add-dir`와 승인 승격은 끕니다.
- 리뷰·상담·이어 묻기와 역할별 읽기 제한에 같은 정책을 적용합니다. 이어 묻기는 세션을 유지하고 새 작업 폴더에 씁니다.
- 리뷰는 `outputs/review.json`과 최종 JSON 응답을 함께 남깁니다. 기존 산출 수집과 모의 리뷰어도 맞췄습니다.
- 매뉴얼 권한 표·안전 장치 설명을 갱신했습니다(118,283 → 118,352자).

## 검증

- 새 회귀 17건: `origin/main` 11845c0에서 17 failed, branch에서 전부 통과.
- 관련 pytest: 146 passed·1 skipped. 입력·앞 단계·다른 task·프로젝트·통제 구역과 링크·하드링크 쓰기 거부, 검토 대상 변경 감지, 새 폴더·기존 세션 유지 확인.
- Windows 전체 pytest: 5,896 passed·66 skipped (모의 e2e 포함, 10분 9초).
- `scripts/check_public.sh`, `git diff --check`: 통과.
- Codex는 sandbox 인자·쓰기 루트 구성, Claude는 실제 승인 함수까지 검사했습니다. 실제 직원 CLI·PI/trial instance는 실행하지 않았습니다.

## 남은 지적

- Gemini·Antigravity·CLI는 읽기 전용 task의 쓰기 경계를 강제하지 못해 기존 실행 거부를 유지합니다.

Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

홈·state 격리와 dry-run 정리 도구를 추가한 PR #554의 P1(4256352456)을 `d38535f`에서 수정했다. 실제 사용자 `.labhq` 설정을 파일 조회 전에 차단한다.

## 바뀐 것

- `Settings.load`의 명시 경로·`LABHQ_CONFIG`, staff 설정 복사.
- instance 경로·포트 탐색, init·bench 설정 확인.
- Paper2Agent cache·skill·설치 출처, 자식 CLI 설정 경로.
- doctor의 roster·설정 소유자 조회와 Codex 설정·marker 읽기. 기존 store guard도 유지한다.

## 확인

- 합성 홈 회귀 17건: 이전 head `590a41d`에서 모두 실패, 수정 후 통과. 격리 파일 전체 23 passed.
- 관련 테스트 187 passed. 이후 추가한 자식 설정 경로는 격리 테스트와 전체 suite로 확인한다.
- Windows 전체: **5646 passed, 66 skipped, 1 failed**(11분 40초). 실패는 `test_semantics_shadow_breaker.py::test_a_stuck_worker_turns_it_off_and_its_late_result_is_dropped`의 `disabled.json` 읽기 PermissionError다. 해당 파일만 재실행해 **50 passed**. 전체 통과로 판정하지 않는다.
- 공개 검사·patch-notes·notes-index·diff 검사를 수행했다. basetemp는 `%TEMP%/pt-testisofix` 한 폴더를 사용했다.

## 미해결

- 전체 suite의 Windows PermissionError 1건은 범위 밖이므로 수정하지 않았다. CI는 push 뒤 현재 head의 PR checks를 기준으로 총괄이 확인한다.
- basetemp 삭제가 자동 승인 검토의 `blocked by policy`로 차단됐다. 리뷰 요청·병합은 하지 않는다.

Refs #545, #221

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

웹을 다시 열면 마지막으로 보던 요청을 복원합니다. 저장된 선택이 없으면 끝나지 않은 요청 중 최근 활동한 것을 엽니다.

## 바뀐 것

- 요청 선택을 데모·라이브·instance별로 기억하며 2.5D·3D가 같은 reducer 값을 씁니다.
- 진행·대기 요청을 완료·실패 요청보다 위에 놓고 상태를 표시합니다. 직접 고른 요청은 끝난 요청의 유입이나 snapshot 재생으로 바뀌지 않습니다.
- 화이트보드에 재개 이벤트 또는 접속 당시 실행 상태를 근거로 `이어서 진행 중`을 표시합니다.
- snapshot에 `updated_at`을 포함하고 웹 사무실 매뉴얼을 갱신했습니다.

## 실행한 것

- 새 Node 회귀 6건: origin/main에서 6 failed, 수정본에서 6 passed.
- Node 전체 41개 파일 통과. 관련 Python 77 passed·1 skipped.
- Windows 전체 pytest: 5,637 passed·66 skipped. 임시 state·TEMP를 분리하고 기존 서비스 포트 접속을 차단했습니다.
- 공개 검사·diff 검사 통과.

## 미해결

- 자동 봇 리뷰 판단과 병합은 개발 총괄이 이어받습니다.

Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

PR #553 보안 P1: plugin·recruiter skill을 열어도 상위 설정 폴더는 계속 보호한다.

## 바뀐 것

- runner·doctor의 plugin 경로를 `keep`에서 `open_reads`로 옮겼다.
- `CODEX_HOME` 전체 예외를 없앴다. Codex elevated profile은 홈을 deny하고 skill만 read로 연다.
- Claude의 작업별 저장 출력 읽기는 유지한다. 형제 로그인·history·다른 task·settings·todos는 Read 거부, Bash 승인 대상이다.
- MCP 경로는 `keep`에 추가하지 않는다. 계약 skill은 작업 폴더로 복사하므로 상위 보호 폴더를 열지 않는다.

## 검증

- 새 회귀 10건: `0257229`에서 모두 실패, 수정 뒤 모두 통과. Read/Bash·두 엔진·일반 plugin·recruiter를 검사했다.
- 관련 pytest: 317 passed·1 skipped. skill 읽기 허용과 쓰기 거부도 확인했다.
- Windows 전체 pytest: **5639 passed·66 skipped**, 692 warnings.
- Codex profile의 하위 경로 예외는 [공식 permissions 규칙](https://developers.openai.com/codex/permissions)에 맞췄다.
- 공개 검사·diff 검사 통과. 최종 CI는 PR checks에서 확인한다.

## 미해결

- 전용 pytest 임시 폴더 삭제는 실행 정책에 막혀 남았다.
- 실제 직원 CLI·PI·trial은 실행하지 않았다. Codex 검증은 생성한 permission profile까지다.

Refs #549

🤖 Generated with Codex (model slug unavailable) for the labhq dev lead

## 결론

PR #554: pytest가 사용자 gateway에 요청을 남기지 않도록 홈·state를 격리했다. 정리 도구는 실제 store에서 실행하지 않았다.

## 바뀐 것

- 수집 전·test별 HOME/USERPROFILE/AppData/XDG 격리와 사용자 `.labhq` 접근 guard. subprocess에도 적용.
- `test_direct_outputs.py`의 gateway tmp 누락 수정. 현재 demo test의 tmp 경로 확인 추가.
- `scripts/purge_test_requests.py`: dry-run 기본. 정확한 문구·agent·mode가 일치한 요청과 이벤트만 선택하며 apply는 gateway 종료 확인과 새 SQLite 백업을 요구한다.

## 확인

- 합성 사용자 홈으로 재현한 guard 회귀: 수정 전 2 failed → 수정 후 통과.
- Windows 전체: **5,630 passed, 66 skipped**. 실제 Node 실행 파일을 테스트 프로세스 PATH에 넣어 사용자 AppData에 의존하는 래퍼를 피했다.
- 읽기 전용 SQLite 전후: 요청 **47→47**, test 문구 **38→38·4→4·4→4**. 증가 모두 0.
- mtime 변경 1건은 state 아래에 있었다. PI가 실행 중이므로 파일 무변경을 주장하지 않고 요청 건수로 판정했다.
- 공개 검사·Python 3.10 문법 검사 통과. CI 결과는 PR checks에 기록한다.

## 미해결

- 데모 문구 8건의 과거 유입 출처는 미확정. 현재 demo test는 tmp gateway를 사용한다.
- 검증된 basetemp 삭제는 자동 승인 검토의 `blocked by policy`로 차단돼 폴더가 남았다.
- 실제 purge·PI/trial 제어·설정 변경·리뷰 요청·병합은 하지 않았다.

Refs #545, #221

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

dev_log가 켜져 있고 repo가 있으면 doctor·gateway 시작 시 private 여부를 확인한다. public·unknown은 경고하며 게시 권한은 바꾸지 않는다.

## 바뀐 것

- GitHubClient·token_env·api_url을 쓰는 GET 조회에 전체 5초 제한을 둔다.
- gateway는 event loop를 막지 않고 실행 인스턴스마다 PI 로그에 한 번 경고한다.
- 쓰기 직전 visibility guard·allow_public·#69 종료 조건을 유지한다.
- 기준 11845c0: triage 뒤 #547·#532·#551·#552·#548·#550 반영. manual 해당 문단 갱신(118,283→118,501자).

## 실행한 것

- 새 테스트를 main 코드에 적용: 17 failed·2 passed. 구현 뒤 관련 테스트: 104 passed.
- 공개 검사·git diff --check·patch-notes·notes-index 검사 통과.

## 미해결

- 로컬 Windows 전체 pytest는 다른 레인의 전체 suite가 연이어 실행 중이어서 중복 실행 금지 규칙에 따라 미실행이다. 완료 조건 미충족.
- 레인 전용 임시 폴더 삭제는 도구 정책 거부로 미완료다.
- 실제 dev_log 저장소 조회·PI/trial 인스턴스 실행은 하지 않았다.
- 봇 리뷰 판단·병합은 총괄이 이어받는다.

Refs #69

## 남은 지적

없음.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

실패 MCP probe가 exit 1을 반환한다. 실제 Codex 90초 지연 호출도 재시도 없이 한 번 완료돼 #276의 남은 실측을 채웠다.

## 바뀐 것

- summary·진단을 보존하고 `ok`·정확한 marker·서버/도구 호출 각 1회·tool error 0을 모두 검사한다.
- Claude inbound는 sender 성공, refuse의 거부 통지·로그, accept/unset의 큐 기록을 별도로 판정한다. `--no-sender`는 MCP만 검사한다.
- task timeout을 summary에 기록하고 매뉴얼의 MCP·inbound 설명을 갱신했다.
- 개발 기준 `f0cbb32`: triage `70f5b72` 이후 #547·#532·#551·#552 반영. 해당 probe 파일에 선행 변경은 없다.

## 실행한 것

| 검사 | 결과 |
|---|---|
| 새 회귀 29건 | 기준 코드 29 failed → branch 통과 |
| 관련 pytest | 37 passed, 1 skipped |
| Windows 전체 pytest | 5,648 passed, 66 skipped (554.79초) |
| 실제 Codex | 0.162.0-alpha.17.2 · 기존 직원 모델 gpt-5.6-sol · 105.4초 · exit 0 |
| 실제 호출 | 지연 90초 · 승인 60초 · MCP 1,320초 · task 1,800초 · 서버/도구 각 1회 · error 0 |
| 공개 검사 | 통과 |

전용 설정·임시 작업 폴더와 기존 직원 인증으로 한 번 실행했다. workspace-write/elevated 격리를 유지했고 PI·trial gateway/runner에는 연결하지 않았다. raw stream·개인 경로는 로컬에만 보관한다. Claude 실제 inbound는 #286의 기존 근거이며 이번 실행은 Codex다.

## 남은 지적

- #286의 sender launcher 지원 범위(P2)는 그대로다. 봇 리뷰 판단과 병합은 총괄이 이어받는다.

Fixes #276

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

#549 D: Paper2Agent를 직원 설정에 설치하고 독립 `doctor`의 필수 검사로 바꿉니다.

## 바뀐 것

- Claude: 직원 `CLAUDE_CONFIG_DIR/labhq-paper2agent/skills/paper2agent`에 설치하고 recruiter에만 plugin을 연결합니다. 미설정 시 `~/.labhq/claude-staff`를 쓰며 개인 설정 격리를 유지합니다.
- Codex: recruiter의 실제 엔진과 직원 `CODEX_HOME/skills/paper2agent`를 따릅니다. 계약할 직원의 엔진과 구분합니다.
- 미설치·가시성 오류는 fail. `labhq doctor --fix`는 setup과 같은 함수로 설치 후 재검사하며 다운로드 실패 이유를 표시합니다. commit 고정 규칙은 유지합니다.
- `init`은 설치 검사를 미룹니다. mock·데모 doctor는 skip하므로 clean-install의 초기화 순서를 유지합니다.
- 옛 개인 경로의 사본은 `.labhq-source.json`에 source·유효한 commit이 있을 때만 정리합니다. 사용자 사본과 링크는 보존합니다.

## 검증

- 새 회귀 테스트 11개: origin/main `e219c68`에서 실패, branch에서 통과. init 예외 테스트 1개 추가.
- Windows 전체 pytest: **5629 passed, 66 skipped**. `%TEMP%/pt-p2asetup` 삭제 확인.
- 관련 테스트 436 passed·4 skipped, init 수정 후 관련 테스트 120 passed.
- `scripts/check_public.sh`, `integrations.py --check`, `notes_index.py --validate` 통과. PR 패치노트 검사 후 push.
- CI는 PR checks에서 확인합니다. PI·trial 실행·설정과 실제 사용자 skill 폴더는 변경하지 않았습니다.

## 남은 지적

- Codex는 기존 로그인·Windows sandbox의 CODEX_HOME을 공유하므로 같은 홈의 다른 Codex 직원도 skill을 봅니다.
- 실제 직원 CLI의 skill 로딩은 실행하지 않았습니다. 임시 홈 설치·plugin preflight·실행 명령으로 검증했습니다.

Refs #549

🤖 Generated with Codex (model slug unavailable) for the labhq dev lead

# runner capability 브리핑

- 권고: 브리핑이 계획과 같은 정제 요약을 받아 현재 계산 자원의 한계부터 적는다.
- PR: [#552](https://github.com/ehojune/bioinfo-team-3d/pull/552), branch: `briefing-runner-capabilities`.

## 변경

- `scheduler=none`·`labhq_hpc=no`·도구 `no`는 사용 불가, `unknown`·보고 부재는 미확인으로 안내한다.
- raw capability manifest 대신 기존 `format_capabilities` 결과만 브리핑에 넣는다. 별도 doctor·원격 조회·모델 호출은 없다.
- 비서실장이 없으면 기존 계획 경로를 그대로 탄다.

## 검증

- 새 회귀 2건: 기준 코드에서 2 failed, branch에서 2 passed.
- `tests/test_cso.py`: 203 passed.
- Windows 전체 pytest: 5619 passed, 66 skipped.
- 공개 검사와 diff 검사 통과. 전용 TEMP·state·basetemp는 검사 뒤 삭제했다.
- 개발 기준 `e219c68`; triage 기준 `70f5b72` 이후 관련 경로 선행 변경 `a428dba`·`1903c15`를 확인했다.

## 미해결

- 없음. CI·자동 리뷰 판정과 병합은 총괄에게 넘긴다.

Refs #36

🤖 Generated with Codex (gpt-5.6-sol) for the labhq dev lead

## 결론

참고 자료 17개를 한·영 문서로 모으고 README의 더 읽기 표에서 연결한다.

## 바뀐 것

- `docs/references.md`·`docs/references.en.md`: 같은 17행 표, 공식 URL과 저장소 근거 링크, 채택·검토 구분.
- topic 논문 1,286편은 기존 목록으로 연결한다.
- README가 이미 길어 표를 분리했다: 한국어 15,199 → 15,272자(+73), 영어 22,754 → 22,874자(+120); 새 표 문서는 각각 2,997자·4,369자다.
- 문서만 변경했다.

## 실행한 것

- `pytest -q tests/test_integrations.py tests/test_patch_notes.py tests/test_notes_index.py`: 56 passed.
- 새 문서의 내부 파일·앵커와 한·영 17행 일치를 확인했다.
- `scripts/integrations.py --check`, `scripts/notes_index.py --validate`, `scripts/check_public.sh`, `git diff --check` 통과.
- PR #551의 문서 커밋을 패치노트에 기록하고 `scripts/patch_notes.py check --base origin/main --head HEAD`를 통과했다(기록 전용 커밋은 저장소 규칙상 행 제외).
- 전체 pytest는 CI에서 실행하며 job 결과는 작업 완료 보고에 남긴다.

## 미해결

없음. 리뷰 판단·병합은 개발 총괄이 이어받는다.

Refs #316, Refs #64, Refs #41

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

긴 명령을 실행하는 직원이 쉬는 중으로 보이던 결함을 고쳤습니다. 최근 200개 이벤트에서 상태가 빠지면 작업판만 running task를 복원하고 직원은 idle로 남던 것이 원인입니다.

## 바뀐 것

- 작업 원장에 도구 시작·종료·대기·경고를 보존합니다. 공유 reducer가 순서 대기·후속 답변까지 복원합니다.
- 직원 시트·카드·2.5D·3D에 명령 실행 시간, 생각 중, PI 결정·HPC·질문·차례 대기를 표시합니다. stall·runner 연결 경고가 우선합니다.
- 활동 줄은 명령·파일 경로를 한 줄로 표시하고 원문은 기존 펼침에 남깁니다. main의 직원 제어와 일시 정지·멈춤 표시도 보존했습니다.
- manual 웹 사무실 절을 갱신했습니다.

## 실행한 것

- main `e219c68`에서 새 pytest 15건·Node 17건 실패. 수정본은 pytest 15건·Node 18건 모두 통과.
- Windows 전체 pytest: **5,633 passed · 66 skipped**. 전체 Node: **40개 파일 통과**.
- 합성 snapshot을 띄운 Chrome에서 직원 시트·명령 펼침·카드·3D 상태 문구 확인. main 통합 뒤 직원 시트 재확인.
- 공개 검사·diff 검사·패치노트·목차 검사 통과.

## 미해결

- 로컬 pytest 임시 폴더 정리는 자동 승인 검토가 차단해 남았습니다.
- 자동 봇 리뷰 판단과 병합은 개발 총괄이 이어받습니다.

Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

손상된 중첩 count가 있어도 semantics report는 정상 요청을 요약하고 제외한 행을 `broken_lines`에 센다.

## 바뀐 것

- `types`·`declarations`와 report가 더하는 count의 구조·0 이상 정수를 읽기 경계에서 검사한다. 손상 값을 0으로 바꾸지 않는다.
- 이전 선택 필드 부재, worker의 `declarations: null`, 소수 지연·비용을 보존한다. JSON·텍스트 CLI와 arm·판본·input-fit 합계를 검증했다.
- 매뉴얼 해당 절 갱신(117,845 → 118,032자). selector·worker·A/B 배정·breaker·저장 로그·활성 설정 변경 없음.
- 착수 기준 `e219c68`: triage 기준 이후 #547·#532와 생성 목차 반영.

## 실행한 것

- 합성 JSONL의 손상 count 두 종류: 새 CLI 회귀 4건이 main에서 실패하고 수정 후 통과.
- report pytest: 275 passed. Windows 전체 pytest: 5861 passed, 66 skipped, 692 warnings(8분 47초). skipped는 통과에 포함하지 않았다.
- 레인 전용 임시 폴더로 TEMP·TMP·TMPDIR·state·basetemp 격리. 공개 검사·diff 검사 통과.

## 미해결

- CI는 PR head 결과로 확인한다. 리뷰 요청·병합은 총괄에게 인계한다.
- 로컬 임시 폴더 삭제가 도구 정책으로 차단돼 정리를 인계한다.
- 합성 재현이며 운영 로그 손상을 확인한 결과는 아니다.

Refs #150

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

PR #532의 3차 검증에서 새 결함 3건을 고쳤다. main(#540~#545 포함)을 병합했다. 로컬 전체 검증은 통과했으며 ready로 전환한다. 병합은 총괄이 현재 head의 [CI 결과](https://github.com/ehojune/bioinfo-team-3d/pull/532/checks)를 보고 판단한다.

## 바뀐 것

| 관점 | 결함과 수정 |
|---|---|
| 수명 | 연결 중단 중 보낸 재개가 runner의 정지를 풀지 못했다. 재연결 때 정지 해제도 보낸다. |
| 동시성 | 제어 전송 뒤 오래된 상태를 저장해 새 방향·정지·결과·단계 종료를 덮어썼다. 전송마다 최신 상태를 읽고 저장을 전송 전에 끝낸다. |
| 경계 | 종료 요청도 작업 결과가 아직 없으면 제어를 받았다. 네 제어 모두 요청·단계의 생존 여부를 먼저 확인한다. |

- merge `bd2a3f5`: 웹 reducer 충돌 두 곳을 풀고 직원 제어·demo 상태·전체 도구 로그를 함께 보존했다.
- 수정 `99bf4bc`: 회귀 테스트 21개와 매뉴얼 한 줄을 추가했다. 3D 디자인 변경은 없다.

## 실행한 것

- 새 회귀 21개: 이전 PR head `85f100e`의 gateway 코드에서 모두 실패, 수정 후 통과.
- 기존 제어 테스트 포함: 74 passed.
- Windows 전체 `pytest -q`: 5608 passed, 66 skipped(10분 52초). `%TEMP%` 아래 basetemp 한 폴더를 썼고 종료 후 삭제했다.
- `for f in tests/*.cjs; do node --test "$f"; done`: 39개 파일 모두 통과(2.5D·3D 공유 상태 포함).
- 공개 검사·patch-notes check·notes-index validate·diff 검사 통과. merge와 수정 커밋의 패치노트 행을 기록했다.
- PI·trial 실행·재시작·설정 변경, 본 checkout 쓰기, 리뷰 요청·인라인 답글, PR 병합은 하지 않았다.

## 미해결

- 기존 수용 한계는 유지: 실제 Claude halt/resume 실측 없음, hook 실행 비용·정지 중 무응답 경고, gateway 재시작 시 PI 대기 중단, 불확실한 dispatch의 방향 중복 가능성.
- 전체 suite의 `test_packaged_resource_works_outside_repository`에서 UTF-8 subprocess reader thread 경고 1건이 나왔다. 테스트는 통과했고 직원 제어 범위 밖이라 수정하지 않았다.

Refs #59

🤖 Generated with Codex (gpt-6) for the labhq dev lead

# gateway 종료 진단

- 권고: 종료 원인은 미확인. 진단 장치를 배포한 뒤 다음 종료의 PID·시각·fault·exit를 맞춰 본다.
- PR: [#547](https://github.com/ehojune/bioinfo-team-3d/pull/547), branch: `gateway-exit-diagnostics`. 기존 인스턴스·설정·시스템은 변경하지 않았다.

## 조사 결과

읽은 자료: instance `gateway.log`·`runner.log`, 실행 wrapper와 trial 로그, Windows Application·System(10-12 18:00~10-13 09:30), 운영 보조 스크립트. 원본 이벤트와 로컬 경로는 공개 저장소에 넣지 않는다.

| 가능성 순 후보 | 근거와 한계 |
|---|---|
| 1. 외부 강제 종료 또는 Windows job 수명 영향(추측) | gateway 로그에 종료 문구가 없고 runner는 02:59:48 연결 단절을 기록. 운영 재시작 스크립트에는 명령줄을 골라 강제 종료하는 코드가 있지만 해당 시각 실행 증거는 없다. 자식은 detached/new process group이며 job breakaway는 지정하지 않는다. WMI 부모 영향도 확인되지 않았다. |
| 2. native crash(추측) | python.exe/combase.dll/c0000005 WER가 21:25·01:25·05:35~05:36에 있다. Application Error 1000은 없고 gateway PID·02:59와 연결되지 않는다. 다른 Python 또는 지연 보고일 가능성을 배제하지 못한다. |
| 3. Python 예외·SystemExit의 관측 누락(추측) | 자식 stdout/stderr는 이미 파일로 향한다. 다만 stdout은 버퍼링되고 별도 exit/fault 기록이 없어 마지막 줄만으로 예외를 배제하지 못한다. 확인된 예외는 없다. |
| 4. 메모리·handle 고갈(근거 부족) | 조사 시간대 Resource Exhaustion 기록이 없고 자원 추세도 없다. 다음 heartbeat에서 확인한다. |
| 5. 절전·재부팅·콘솔 정리(근거 부족) | 조사 시간대 Kernel-Power/Power-Troubleshooter 관련 기록이 없다. 02:50~03:10은 BITS 설정 변경·DCOM 10016·IsolatedUserMode·라이선스 서비스 이벤트뿐. trial 생존과 detached 실행도 PC 전체 정리를 뒷받침하지 않는다. |

`up` wrapper는 18:29:05 exit 0으로 끝났다. 이는 자식 gateway의 종료 코드가 아니다. 진단 공백을 고쳤으며 종료 원인을 고쳤다고 주장하지 않는다.

## 변경·검증

- gateway·runner에 faulthandler, sys.excepthook, asyncio 예외 handler, atexit, 지원 signal 기록과 heartbeat 추가. 기존 signal handler와 runner의 두 번 Ctrl+C 규칙을 보존한다.
- `up`은 기존 stdout/stderr 파일 전달을 유지하고 unbuffered·초기 faulthandler를 켠다.
- `status`·`doctor`는 PID 기록을 보존하며 프로세스 없음과 마지막 heartbeat·exit를 표시한다.
- 새 회귀: main `70f5b72`에서 4 failed. branch 관련 검사 107 passed·1 skipped.
- 첫 Windows 전체 검사는 5,538 passed·66 skipped·encoding 검사 1 failed. encoding·바이너리 모드를 고친 뒤 관련 11 passed. 전체 재검사와 CI 결과는 PR 검증 절에 기록한다.
- 다음 종료 때 볼 파일: `gateway.state_dir/logs/{gateway,runner}-diagnostics.jsonl`, `{gateway,runner}-fault.log`, `{gateway,runner}.log`. 상세 사용법은 [manual](../manual.md).

남은 지적: 외부 강제 종료는 프로세스 내부 hook으로 종료 코드를 얻지 못한다. 이번 작업은 자동 재시작·상주 감시자를 추가하지 않는다.

## 결론

PI 시운전에서 데모가 실제 사무실과 같은 port를 쓰던 결함을 고쳤습니다. 웹 데모의 표시·진행 속도·결정 기록도 분리했습니다.

## 바뀐 것

- 원하는 port에 loopback 응답이 있거나 IPv4·IPv6 bind 검사 중 하나라도 실패하면 다른 빈 port를 고르고 한국어로 주소를 안내합니다. runner도 그 데모 서버에 연결합니다.
- 데모 BOOT에 `demo: true`를 넣고 2.5D·3D에 `데모(mock)`와 상단 안내 띠를 표시합니다. 브라우저 저장 키를 라이브와 나눕니다.
- 웹 mock 단계는 2초, 자동 승인은 1초 뒤 진행합니다. 요청 사이에는 남은 시간을 표시하며 CLI·일반 test 지연과 폰 승인 대기는 유지합니다.
- README·manual의 데모 절에 실제 사무실과 함께 여는 법을 안내했습니다.

## 실행한 것

- main helper에서 주소 충돌 회귀 3건 실패, 수정본의 새 Python test 14건 통과.
- Windows 전체 pytest: 5,517 passed · 66 skipped (mock e2e 포함).
- `for f in tests/*.cjs; do node --test "$f"; done`: 34개 파일 통과.
- 빈 port의 실제 웹 데모: 요청 29.10·22.90·29.56초. Chrome에서 2.5D·3D 표시와 대기 안내 확인.
- 공개 검사·diff 검사·패치노트·목차 검사 통과. pytest 임시·상태는 한 TEMP 폴더에 격리하고 8787 socket 통신을 차단했습니다.

## 미해결

- 없음. 자동 봇 리뷰 판단과 병합은 개발 총괄이 이어받습니다.

Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

긴 줄 펼침과 지난 결정 정리를 구현했습니다. 받은 원문과 gateway 결정·감사 기록은 보존합니다.

## 바뀐 것

| 항목 | 결과 |
|---|---|
| 펼쳐 보기 | 활동·생각/디버그·메신저·결정 detail의 공통 펼침. Enter·Space·aria-expanded 지원. 서버 제한은 기존대로 유지하고 잘림 표시 추가 |
| 결정 기록 | 로컬 시각·보고서가 있는 요청의 링크·× 숨김. 2.5D·3D 공유. localStorage를 골라 서버 기록 변경 없이 화면만 정리하며 저장 실패에도 현재 페이지에서 동작 |
| 데모 기록 | 실제 store로 유입되는 코드 경로 없음. CLI 데모 문구이며 임시 store 격리 테스트 통과. 스크린샷의 접속 인스턴스는 미확인 |

## 실행한 것

- origin/main `4268ffa`: 새 Node 회귀 6건 실패, adapter pytest 11건 실패, Node CI wrapper pytest 1건 실패.
- branch: Node 34개 파일 전부 통과. 관련 pytest 190 passed·2 skipped. Windows 전체 pytest 5515 passed·66 skipped(9분 20초, 경고 646건).
- Chrome QA: 명령 끝부분·키보드 펼침/접기·aria-expanded·서버 400자 표시·보고서 이동·새로고침 뒤 숨김 유지 확인.
- public·patch-notes·notes-index·diff 검사 통과. pytest 임시·상태 폴더는 TEMP 아래 한 폴더를 쓰고 삭제.
- CI [run 38349965660](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38349965660), 검증 head `9bf9fa3`: `changes`·`patch-notes`·`pytest (3.10)`·`pytest (3.12)`·`pytest-windows`·`clean-install`·`ro-crate-validator`·`pytest-slow` 성공. `readme` 조건부 skip.

## 미해결

- PI·trial을 조회·실행·재시작하거나 설정을 바꾸지 않았으므로 스크린샷의 접속 주소는 확정하지 않았습니다.
- 전체 pytest에서 subprocess UTF-8 경고 1건이 나왔으나 실패는 없었습니다. 이 웹 변경 범위 밖입니다.

근거: `docs/reference/web-expand-history.md`, `tests/web_expand_history.cjs`, `tests/test_event_preview.py`, `tests/test_demo_phone.py`.

Refs #57, Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead

## 결론

CP1 승인 시 집행 상한을 승인까지 쓴 비용 + 계획 예산으로 정한다. 요청 예산(없으면 정책 상한)을 넘으면 그 상한으로 자른다. 이어 가기 차수도 같은 규칙이다.

## 바뀐 것

- 승인 budget에 `spent_before_plan_usd`를 기록하고 승인 직전에 다시 집계한다.
- CP1 카드·직원 prompt에 계획 예산과 계획까지 쓴 비용을 함께 표시한다.
- 계획 prompt 두 종류도 `budget_usd`를 CP1 이후 추가 집행액으로 정의한다(봇 P1 수정).
- `docs/manual.md` 예산 절과 연구 protocol 설명을 맞췄다. manual 과거 사례 한 문장을 덜었다(112,104 → 112,023자).

## 실행한 것

- 새 회귀 5건: main의 cso 코드에서 모두 실패, 변경 코드에서 관련 pytest 39 passed.
- prompt 설명·fingerprint 포함 관련 pytest: 53 passed.
- 전체 pytest: 5,447 passed·66 skipped·10 failed. 실패는 CLI stream·MCP probe·semantics subprocess 로그 검사이며 예산 관련 실패는 없다. 실패 파일 재실행에서도 일부 재현됐다.
- 공개 검사·diff 검사 통과. 병합은 GitHub CI 결과로 판정한다.

## 미해결

- 로컬 전체 suite의 위 10건은 이번 예산 변경 범위 밖이다.

Refs #435

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead

## 결론

오늘 19:00 PI 시운전 전에 CLI 결함 3건을 고쳤습니다. `labhq down`의 한글 Windows 추적 출력(점검 1·15, 같은 결함), 기록 없는 gateway를 두고 성공으로 끝나던 `down`(2), `send`·`watch <id>`가 요청 묶음을 출력하지 않던 것(12)입니다. 3건 모두 수정 전 코드에서 실패하는 test를 붙였습니다.

## 바뀐 것

- `down`의 `taskkill`을 `encoding="oem", errors="replace"`로 읽습니다. `PYTHONUTF8=1`인 한글 Windows에서 UnicodeDecodeError 추적이 두 번 나오던 것이 사라지고, `taskkill`이 실패해도 stderr·stdout이 None이라 메시지 조립이 깨지지 않습니다.
- `down`은 기록된 프로세스를 멈춘 뒤 gateway가 아직 답하는지 다시 봅니다(종료 직후 최대 3초 기다림). `up` 밖에서 시작해 PID 기록이 없는 gateway·runner가 답하면 "`up`이 아닌 방법으로 시작한 gateway/runner가 아직 실행 중"이라고 알리고 exit 2로 끝냅니다. 전에는 `종료: 기록된 프로세스 없음`과 exit 0이어서 다음 `up`이 그 프로세스를 그대로 썼습니다. gateway가 다른 PC에 있는 runner 전용 PC에서는 이 설정의 runner id가 아직 연결돼 있을 때만 실패로 봅니다.
- `send`와 `watch <id>`는 끝났다는 이벤트 뒤에 오는 `request.bundle`(또는 요청 기록의 묶음 필드)을 최대 60초 기다려 `요청 묶음:`과 `재현 등급:`을 출력하고 끝납니다. gateway는 끝났다는 기록을 먼저 저장하고 묶음을 나중에 만들기 때문입니다. 요청 기록에 `bundle_pending`이 없으면(단계를 돌리지 않은 요청) 기다리지 않고, 60초가 지나면 `labhq status`로 보라고 알립니다.
- `docs/manual.md`의 `down`과 `send`·`watch` 설명을 고쳤습니다.

## 확인

- 봇 P1 후속: bind 주소 대신 URL hostname과 이 PC hostname·인터페이스 주소를 비교합니다. 주소 확인 실패는 로컬 가능성으로 보고 응답 중이면 exit 2입니다. 회귀 5건은 수정 전 실패, 관련 test는 51 passed·1 skipped입니다.
- 새 test 15건: `tests/test_cli_launch.py` 9건(taskkill 2건, Windows 전용 디코딩 1건, `down` 6건), `tests/test_cli_bundle_wait.py` 6건(실제 `Hub.schedule_terminal`이 커밋하는 이벤트 순서로 `send`·`watch`를 구동). 수정 전 `labhq/cli.py`로 돌리면 11건이 실패합니다. 동작 때문에 실패하는 것은 4건(taskkill 인자·None 처리 1, 기록 없는 gateway 1, 묶음 대기 `send`·`watch` 2)이고 나머지 7건은 새 상수가 없어 먼저 실패합니다.
- 전체 pytest(Windows, `--basetemp`, xdist 없음): 5466 passed, 66 skipped, 8분 50초.
- 기존 `test_down_refuses_active_work_and_force_terminates_only_recorded_trees`가 이 PC의 실제 8787 gateway에 닿아 401로 실패해서 `_health`를 막았습니다.
- `scripts/check_public.sh`, `git diff --check`: 통과.

## 미해결

- 없음. 오늘 밤 8787 instance가 `instances.ps1`로 떠 있으면 PID 기록이 없어 `down`이 이제 exit 2로 알리기만 하고 멈추지는 못합니다. `up`으로 시작한 instance에서만 `down`이 멈춥니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 결론

오늘 19:00 PI 시운전 전에 웹 결함 4건을 고쳤다. 새 회귀 test가 네 건 모두 main 코드에서 실패하고 이 branch에서 통과한다.

## 바뀐 것

1. 직접 맡김 표시: 직접 맡긴 요청은 "처리: 직접 맡김"으로 보인다. 경로 결정 없이 끝난 요청(계획 단계 실패 등)은 처리 표시를 뺀다. 3D 요청 행은 영어 phase 대신 2.5D와 같은 한글 단계 이름을 쓰고, 끝난 요청은 상태(완료·실패)만 보인다. 라벨은 `state.js`(`processingLabel`·`phaseLabel`) 한 곳이다.
2. CP1 대기 단계: `request.plan`은 phase를 `plan`으로 두고, `step_id`가 있는 dispatch가 `execute`로 넘긴다. 스냅숏은 리뷰가 없고 시작한 단계도 없으면 `plan`으로 읽는다.
3. 직접 요청 중 보내기: `send()`가 `renderComposerMode`와 같은 `mode !== 'direct'` 검사를 한다. 직접 맡긴 요청을 고른 채 보내기를 누르면 새 요청으로 간다(전에는 메모를 보내 409).
4. 연구 종료 사유: `report_incomplete`, 리뷰 revise·해석 실패, 보고서 작성 실패, 예산 거부, CP2 거절·수정 요청·시간 초과·판독 불가에 사유 한 줄(`cso.py`의 `RESEARCH_END_ERRORS`)을 붙인다. 사유가 비면 `state.js`가 중립 문구를 보인다.
5. `docs/manual.md`의 화이트보드·직접 요청 보내기·연구 종료 사유 설명을 맞췄다. `web_state_hashes.json`은 request.plan 직후 phase가 바뀐 한 항목(인덱스 5)만 갱신했고, `web_solo_route.cjs`는 state.js로 옮겨 간 라벨을 동작으로 확인하게 고쳤다.

## 실행한 것

- `tests/web_trial_ready.cjs`(CI 목록 `tests/test_web_3d.py`에 추가): 네 결함 모두 main 코드에서 실패, 이 branch에서 통과. 3D 요청 행은 live.js를 그대로 불러 DOM stub로, `send()`는 index.html에서 잘라 vm으로 실행한다.
- pytest 단언 3곳 추가(`test_research_report.py` 2곳, `test_research_protocol.py` CP2 표): main의 `cso.py`에서 실패, 이 branch에서 통과.
- Node test 33개 전부 통과. 전체 pytest 통과(exit 0, 8분 42초). `scripts/check_public.sh`, `git diff --check` 통과.
- index.html을 정적 서버로 띄워 demo 모드가 콘솔 오류 없이 그려지는 것까지 확인했다. 요청 흐름은 Node harness로만 확인했다.

## 미해결

- 3D 화이트보드(`lab3d/src/office.js`)의 `status · phase` 줄은 영어 키 그대로다(이번 점검 항목 밖).
- 리뷰 revise 뒤 새 CP1을 기다리는 중에 새로고침하면 스냅숏에 리뷰가 남아 `execute`로 읽는다(실시간 경로는 `plan`).
- 연구가 아닌 `ok=False` 종료 중 사유를 주지 않는 경로(브리핑 실패 등)는 중립 문구가 나온다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 단계
P2 — PI 시운전 전 실행 경로 결함 4건

## 한 일

| 항목 | main 재현 | 수정 |
|---|---|---|
| 7 · 직원 질문 hard stop | 평범한 format·표본 drop·결과 overwrite 질문이 destructive로 PI에게 감 | 모든 직원에 정책 목록을 적용하고 실제 삭제·파괴만 고유 PI gate로 유지 |
| 8 · 환경 설치 오판 | PowerShell 대입 6형태와 문서의 Bash PYTHONPATH 2형태가 거부됨 | installer 없는 대입·환경 접두사는 건너뛰고, 대입에 숨긴 installer는 계속 fail closed |
| 10 · PI 카드 대기 경고 | 열린 MCP 승인 호출에도 중단·재시도 경고가 표시됨 | PI 카드 대기는 debug로만 기록하고 종료·취소 경고를 생략 |
| 11 · 같은 폴더 재실행 산출 | retry·결정 재실행·review revise가 앞 `unreported_outputs`를 잃음 | 같은 workdir의 결과를 합치고 새 정식 outputs와 겹친 항목은 제외 |

`docs/manual.md`는 191,351 → 192,125 bytes(+774)로 네 동작만 보완했습니다.

## 테스트 결과
- [x] 추가 회귀의 main 상태: 23 failed, 6 passed
- [x] 수정 뒤 같은 회귀: 29 passed
- [x] 관련 pytest 5개 파일: 798 passed, 1 skipped
- [x] Windows 전체 `pytest -q`(모의 e2e 포함): 5468 passed, 67 skipped
- [x] Node CJS 32개, `scripts/check_public.sh`, `git diff --check`: 통과
- [ ] GitHub Actions: 기록 commit push 뒤 확인
- [x] UI 변경 없음

## 바꾼 파일
- `labhq/models.py`, `labhq/orchestrator/cso.py`, `labhq/environment_install.py`, `labhq/adapters/base.py`
- `tests/test_ask.py`, `tests/test_bioinfo_pipeline_pr.py`, `tests/test_environment_install.py`, `tests/test_stall_watch.py`, `tests/test_cso.py`
- `docs/manual.md`

## 막힌 점
없음

## PI·Claude에게 물을 것
없음

## 확인
- [x] `scripts/check_public.sh` 통과
- [x] `docs/status/`와 branch patch note 추가
- [x] 체크포인트 아님

Refs #435

## 남은 지적
- P2: 일반 tool call이 멈춰도 PI 카드 대기로 분류돼 stall 경고가 숨겨질 수 있습니다.

## P1 후속
- 원본·입력·불명확한 overwrite는 PI gate로 유지하고 명시한 `outputs/` 재생성만 예외입니다. 회귀 6건 수정 전 실패, 관련 34 passed.

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 결론

같은 SHA 재실행에서 사라진 Windows CI flake 4개를 실제시간 제한 대신 event·task 완료 동기화로 바꿨습니다.

## 바뀐 것

| test | CI 실패 횟수 | OS | 증상 |
|---|---:|---|---|
| `test_restart_recovery_holds_only_the_step_on_the_limited_engine` | 1 | Windows | 바깥 `wait_for(1s)`가 `TimeoutError`; run 38170211095 재실행 통과 |
| `test_temporary_process_snapshot_failure_is_rechecked` | 1 | Windows | 바깥 `wait_for(2s)`가 `TimeoutError`; run 38156419427 재실행 통과 |
| `test_the_lab_records_the_same_with_actions_on[shadow]` | 1 | Windows | terminal status 60초 polling timeout; run 37668212634 재실행 통과 |
| `test_every_cso_and_direct_turn_kind_waits_for_login[briefing]` | 1 | Windows | 바깥 `wait_for(1s)` 안에 retry가 못 끝남; run 37700811619 재실행 통과 |

- quota test는 독립 단계 완료 event와 recovery task를 직접 기다립니다.
- stall test는 mock kill 완료 event 뒤 runner task 결과를 확인합니다.
- mock lab은 request orchestration task를 기다린 뒤 terminal status를 단언합니다.
- login test는 두 번째 dispatch event 뒤 runner task 결과를 확인합니다.
- `test_login_retry_expiry_ends_notice_and_next_failure_notifies_again` 2건은 PR #459에서 이미 수정돼 제외했습니다.
- reused workspace 사례는 단독 25회 실패 0이고 해당 CI 로그가 공유 temp 오염이라 수정하지 않았습니다.

## 실행한 것

- 8개 CPU job 부하, 수정 전: 각 test 30회, 실패 0/30(로컬에서는 미재현)
- 같은 부하, 수정 후: 각 test 30회, 실패 0/30
- 관련 test: 65 passed, 1 skipped
- `tests/test_login_wait.py`: 45 passed
- Windows 전체 `pytest -q`: 5331 passed, 66 skipped(두 번째 격리 run; 첫 run은 다른 lane pytest와 겹쳐 11건 실패, 해당 11건 단독 재실행은 통과)
- `scripts/check_public.sh`: 통과

## 미해결

- current head Windows 전체 pytest와 CI는 기록 commit 뒤 확인합니다.
- 남은 지적: 없음.

## 근거

- `tests/test_quota_wait.py`
- `tests/test_stall_watch.py`
- `tests/semantics_shadow_lab.py`
- `tests/test_login_wait.py`
- `HANDOFF.md`

🤖 Generated with Codex (gpt-5) for the labhq dev lead

# v0.95 깨끗한 설치 리허설

## 결론

PR #536에서 새 Windows clone·venv의 설치부터 mock 요청, 보고서·요청 묶음, `verify`, 종료·정리까지 자동화했습니다. 리허설 차이 3건을 찾아 3건 모두 고쳤고 CI 전체가 통과했습니다.

## 바뀐 것

- `scripts/clean_install_check.py`: 임시 HOME·state·빈 포트·mock 직원으로 전 과정을 검사하고 삭제
- README·manual: 빠진 질문과 격리 설치 검사 명령, v0.95 현재 상태 보정
- `.github/workflows/test.yml`: 기존 suite와 병렬인 Windows `clean-install` job 추가

## 리허설 차이

| 단계 | 문서가 말한 것 | 실제 | 고침 |
|---|---|---|---|
| `labhq init` | HPC·bioinfo-agent 경로를 질문 | 선택형 AlphaGenome 키 질문도 나옴 | README에 Enter로 생략한다고 명시; 검사 `labhq init` 단계가 질문 수를 고정 |
| 설치 완주 | 명령을 따로 실행 | mock으로 요청·묶음·검증까지 반복할 검사 없음 | `clean_install_check.py`의 요청·묶음·`verify`·`down` 단계와 Windows CI 추가 |
| Windows CI 출력 | 실패 단계와 출력을 한 줄로 표시 | cp1252 console에서 한글 단계명 출력이 실패 | stdout·stderr·자식 출력을 UTF-8로 고정하고 encoding 회귀 test 추가 |

## 확인

- 수동 리허설과 자동 검사 통과. `doctor` fail 0·실행 준비 예, `verify` exit 0
- PI 홈과 본 checkout 설정 mtime: 전후 동일
- README: 전체 23,938 → 23,931 bytes, 설치 절 420 → 413 bytes
- 관련 pytest: 114 passed, 1 skipped; README test: 33 passed
- Windows 전체 pytest: 첫 실행 5,325 passed·66 skipped·Windows manifest 교체 flake 1건; 실패 test 재실행 1 passed; 전체 재실행 5,326 passed·66 skipped
- cp1252 강제 clean install·encoding 회귀 test: 통과
- `scripts/check_public.sh`, `git diff --check`: 통과
- CI run 38206716903: changes·patch-notes·clean-install·pytest 3.10·3.12·Windows·slow·ro-crate-validator 성공; README 전용 job 조건부 skip

## 미해결

- 없음

Refs #300

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 결론

gateway가 terminal commit 뒤 묶음을 만들기 전에 강제 종료되거나 crash해도, 다음 시작 때 그 요청의 묶음을 한 번 다시 만듭니다.

## 바뀐 것

- terminal request row에 `bundle_pending`을 같은 commit으로 저장하고 `request.bundle` commit에서 지웁니다.
- 시작 때 표시가 남은 요청만 기존 worker 경로로 복구합니다. 실패도 warning을 한 번 기록한 뒤 표시를 지웁니다.
- 기존 임시·대상 폴더 정리 규칙을 재사용하며, 복구 중 요청은 shutdown readiness의 `finishing`에 남습니다.
- `docs/manual.md` 요청 묶음 절: 110,906자 → 111,021자(+115자).

## 실행한 것

- origin/main + 새 회귀 3건(a·d·e): 3 failed
- 새 회귀 5건: 5 passed
- `tests/test_request_bundle.py`: 98 passed, 2 skipped
- `tests/test_request_lifecycle.py`: 54 passed
- Windows 전체 `pytest -q`: 5,331 passed, 66 skipped
- `scripts/check_public.sh`: 통과

## 미해결

- 없음. CI는 기록 commit push 뒤 확인합니다.

## 근거

- `labhq/gateway/server.py`
- `tests/test_request_bundle.py`
- `docs/manual.md`

Refs #521

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 단계
P1·P2 — 승인 게이트 경로 판정 후속: #533(PR #525 봇 지적 3건), #521의 `~` 쓰기 경로, #178. 봇 리뷰 1회차(P1 1·P2 1)와 오탐 3건 반영.

## 한 일

같은 부류의 더 좁은 변형이 이어지던 곳이라 사례 대신 부류를 닫았습니다. 리터럴로 다 읽을 수 없는 삭제 payload·대상·`~` 표기는 PI에게 묻고, 읽을 수 있으면 같은 `.tmp` 규칙을 적용합니다.

| 항목 | main 재현 | 수정 |
|---|---|---|
| #533 P1 find payload | 재현: `find .tmp -exec rm $'-rf' /outside \;`, `-exec rm -f /outside`, `-exec $CMD {}`, `rm $'-rf' /x` 허용 | `-exec`·`-ok` payload는 리터럴 `rm`·`unlink` 한 명령으로만 읽고 `{}` 밖 대상도 `.tmp` 규칙으로 본다. 변수 명령, `$'..'` 단어, `sh -c 'rm ..'`·`env rm` 같은 감싼 삭제, `{}.bak`, `-execdir`의 `{}` 밖 대상은 묻는다. `rm`의 `--` 앞 단어가 확장으로 시작하면(`$'-rf'`, `"$OPT"`, `-f"$X"`, `{-r,x}`) 재귀 삭제로 본다. |
| #533 P1 PowerShell script block | 재현: `gci .tmp \| % { [IO.DirectoryInfo]::new('C:/outside').Delete($true) }`, `\| % { 'C:/x' } \| Remove-Item`, `\| % { iex $code }` 허용 | pipe 뒤를 문법으로 읽는다. 중간 단계는 항목을 그대로 넘기는 `Where-Object`·`Sort-Object`·`Select-Object -First` 등, 마지막은 경로 없는 `Remove-Item`이거나 `$_`·`$PSItem`만 지우는 `Remove-Item`·`$_.Delete()`. 그 밖은 묻는다. pipe 밖 .NET 삭제(`[IO.Directory]::Delete(..)`, `(Get-Item x).Delete($true)`)도 묻는다. |
| #533 P2 R 판정 범위 | 재현: `python wrapper.py "$INPUT" "install.packages('x')"` 거부 | R 호출 판정은 프로그램 자리 단어에만 건다: 첫 단어, `&` 뒤, wrapper(`nohup`·`sudo -u x`)와 실행기(`uv run`·`conda run`·`pixi run` 등)가 실행하는 단어. Python script·`-c` 뒤는 인자. 모르는 프로그램이면 모든 단어를 본다. |
| #521 `~` 쓰기 대상 | 재현: `cp a ~/x`, `> ~/.bashrc`, `curl -o ~/x`, `tee ~/x`, `Set-Content ~/x` 허용 | 쓰기 대상의 `~`를 Bash는 `HOME`(없으면 `USERPROFILE`·`HOMEDRIVE`+`HOMEPATH`), PowerShell은 Windows에서 `USERPROFILE`·`HOMEDRIVE`+`HOMEPATH`로 펼쳐 모두 허용 폴더 안일 때만 허용. `~user`·`~+`·`~-`, home을 모를 때는 묻는다. 읽기 경로는 그대로. |
| #178 링크의 다른 표기 | 재현: `policy.private_paths: []`이면 통제 구역 링크가 있어도 맨 `Read`·셸 규칙을 미리 허용해, `DATA~1/raw.tsv`는 거부 규칙을 비껴 게이트를 거치지 않음 | 그런 링크가 있는 Claude 단계는 개인 경로 차단처럼 셸 규칙을 빼고 읽기를 작업 폴더 `Read(//폴더/**)`로 좁힌다(실측 `claude_read_scope_alias.json`: 링크로 폴더 밖에 닿는 읽기는 사전 허용되지 않음). 게이트는 실제 경로로 비교한다. Windows 11 실측 test에서 junction의 8.3 이름·대소문자 표기 모두 Read 거부·셸 승인 요청(8.3이 꺼진 볼륨은 skip). |

### 리뷰 1회차 (a7cfd20 → 2002b27)

| 지적 | 수정 |
|---|---|
| P1: `uv --project foo run Rscript -e "install.packages('x')"`에서 `foo`를 subcommand로 읽어 Rscript를 놓침 | 실행기 표(`uv`·`uvx`·`conda`·`mamba`·`micromamba`·`pixi`·`poetry`·`npx`·`pnpm`)로 전역 옵션을 건너뛰어 subcommand와 그 프로그램을 찾는다. 표에 없는 옵션이 subcommand 앞에 있거나 subcommand가 변수면 뒤 단어를 모두 프로그램 자리로 본다. 공유 환경의 변수 프로그램 판정(`uv --project p run "$PIP" "$ACTION"`)도 같은 표를 쓴다. |
| P2: `Write-Output '$_.Delete()'` 같은 출력 문자열을 .NET 삭제로 읽음 | 쓰기 판정과 같은 quote-aware 처리로 data 명령의 따옴표 안을 빼고 찾는다. 판정이 불확실하면(`iex` 등) 원문을 본다. |
| 오탐: `docker rm "$id"`, `git rm "$f"`, `rm -f "$f"`가 승인 카드를 띄움 | 재귀 삭제 규칙은 실제 실행 파일이 rm일 때만 본다(명령 맨 앞, `sudo`·`xargs`·`find`·`sh` 같은 실행기나 변수 프로그램 뒤). `git rm`은 지금 트리 안이면 허용하고 `-C`·`--git-dir`·`--work-tree`·`core.worktree`·`GIT_DIR`·`GIT_WORK_TREE`가 있거나 앞 단어가 변수면 묻는다. 큰따옴표 변수 하나가 유일한 대상인 `rm`은 허용한다. |

## 테스트 결과

- [x] 새 test 119개(파라미터 포함). 1차 59개는 main에서 45개 실패, 리뷰 반영분 60개는 a7cfd20에서 42개 실패, 수정 뒤 모두 통과. 나머지 32개는 guard(허용 형태 유지, 계속 묻는 형태, 8.3·대소문자 실측 2개).
- [x] 기존 허용 test(분석 명령, `.tmp` 정리) 그대로 통과. 약하게 바꾼 test 없음.
- [x] Windows 전체 `pytest -q`(모의 e2e 포함): 5445 passed, 66 skipped (`--basetemp`·`LABHQ_STATE_DIR`는 %TEMP% 아래 한 폴더, 끝나고 지움)
- [x] `scripts/check_public.sh`
- [x] `python scripts/patch_notes.py check --base origin/main --head HEAD`

## 바꾼 파일

- `labhq/policy.py`, `labhq/environment_install.py`, `labhq/adapters/base.py`, `labhq/adapters/claude_code.py`, `labhq/runner/daemon.py`
- `tests/test_policy.py`, `tests/test_environment_install.py`, `tests/test_project_link_spellings.py`(새)
- `docs/manual.md`(승인 게이트와 쓰기 범위, 공유 환경 문단, 알려진 한계의 #178 문단)

## 바뀌는 동작 (main 대비)

- 새로 묻는 것:
  - `rm` 옵션 자리의 읽을 수 없는 단어: `rm $'-rf' x`, `rm -f $f`(따옴표 없음), `rm -f "$a" "$b"`, `rm "$f" x`
  - `.tmp` 밖을 지우거나 읽을 수 없는 `find -exec` 삭제
  - PowerShell에서 pipe 뒤 다른 경로 삭제, pipe 밖 .NET 삭제
  - home이 허용 폴더 밖일 때 `~`로 시작하는 셸 쓰기 대상
  - `git rm`에 `-C`·`--git-dir`·`--work-tree`·`core.worktree`·`GIT_DIR`·`GIT_WORK_TREE`가 있거나 앞 단어가 변수일 때
- 공유 환경에서 새로 거부하는 것: 표에 없는 실행기 옵션이나 모르는 실행기 뒤의 R·pip 설치 호출
- 새로 묻지 않는 것:
  - 지금 트리 안의 `git rm -r x`
  - 다른 프로그램의 `rm` 인자: `docker rm "$id"`, `echo rm -rf x`
  - `python wrapper.py "$IN" "install.packages('x')"`
- 그대로: `rm -f "$f"`(큰따옴표 변수 하나가 유일한 대상), `rm -f -- "$f"`, `.tmp` 정리, 분석 명령
- Claude 단계: 통제 구역 링크가 있는 project 폴더를 열면 셸 명령마다 게이트를 거친다. 개인 경로 차단이 켜진 기본 설정에서는 이미 그렇다.
- 한계: 감싼 본문에 삭제 단어가 없는 `find ... -exec sh -c '...'`는 예전처럼 허용(최상위 `sh -c`와 같은 어휘 판정).

## 막힌 점

- 없음. 독립 코드 리뷰는 돌리지 않음(Codex 일일 상한으로 Claude가 대신 맡은 레인).

## PI·Claude에게 물을 것

- 없음.

## 확인

- [x] `docs/status/2026-10-12-1324-policy-path-followups.md`에 같은 보고 추가
- [x] 실제 labhq 인스턴스 실행·재시작 없음

Closes #533
Closes #178
Refs #521

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 단계
P2 — #527 봇이 남긴 연구 lane 후속 3건

## 한 일

| 항목 | main 재현 | 수정 |
|---|---|---|
| 1. 마지막 보고서 turn이 `pending_asks`·`pending_jobs`를 내면 "final turn; no further work" 기록 뒤 wake 전에 예산 카드가 또 뜸 | 재현(기록 3줄, 첫 줄이 final turn) | 마지막 단계의 끝난 turn은 바로 판정하지 않는다. 기다리는 turn이면 다음 wake turn의 gate가 카드를 띄우고, 면제 기록은 wake·마무리까지 끝난 뒤 `run_step` 끝에서 한 번 남긴다. 질문이 거절돼 끝나는 경로도 같은 끝을 지난다. |
| 2. 차수가 없는 예전 continuation 확인 질문 답이 1차로 세져 2차 이상 재개 때 카드 두 장을 더 열 수 있음 | 재현(`answered`, 카드 열림) | 차수 없는 답은 지금 차수 상한에 센다. 1차 답은 contract 전이라 차수가 안 적혔으므로 research intake면 `research_round: 1`을 적는다. 답한 질문 거르기는 전체 기록 그대로. |
| 3. 새 CP1 뒤 `continuation.verified=True` 저장과 첫 dispatch 사이에 gateway가 멈추면 취소·재개 거절·시간 초과 때 되돌리기를 건너뜀 | 재현(3경로 모두 새 계획이 남음) | gateway는 task ledger에 그 차수(`research_round`)의 단계 task가 있는지로 판정한다. 1차 단계 task는 세지 않는다. 처리 중인 orchestrator 경로는 지금처럼 `verified`를 쓴다. |

## 테스트 결과

- [x] 새 test 9개(파라미터 포함): main에서 6개 실패, 수정 뒤 통과. 나머지 3개는 dispatch된 차수를 되돌리지 않는지 지키는 guard.
- [x] 관련 파일 5개: 131 passed
- [x] Windows 전체 `pytest -q`(모의 e2e 포함): 5134 passed, 64 skipped (`--basetemp`·`LABHQ_STATE_DIR`는 %TEMP% 아래, 끝나고 지움)
- [x] `scripts/check_public.sh`
- [x] `python scripts/patch_notes.py check --base origin/main --head HEAD`

## 바꾼 파일

- `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`
- `tests/test_research_notes_budget.py`, `tests/test_research_continue.py`, `tests/test_request_lifecycle.py`
- `docs/manual.md`(승인·예산, 이어 가기 절)

## 막힌 점

- 없음. CI는 push 뒤 확인합니다.

## PI·Claude에게 물을 것

- 없음.

## 확인

- [x] `docs/status/2026-10-12-0543-research-state-followups-2.md`에 같은 보고 추가
- [x] 실제 labhq 인스턴스 실행·재시작 없음

Refs #521

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## 2026-10-12 · 실행 중 직원 제어: 방향 바꾸기·일시 정지·멈추기 (#59 ①)

- 결론: 오늘 PI 시험(19:00) 뒤, 세 번째 검증을 거쳐 병합한다. 그때까지 draft로 둔다. PI가 실행 중인 직원에게 방향을 보내고, 일시 정지·재개하고, 세션을 남긴 채 멈출 수 있다. 요청은 작업이 아니라 그 작업의 단계에 걸리고, gateway가 쓰일 때까지 보관한다. 계획 단계 작업과 직접 맡긴 작업만 받는다. 쓰기 가능한 Claude 실행은 도구 hook으로 바로, 다른 엔진은 같은 단계의 다음 turn에서 받는다. 멈춘 단계는 실패가 아니라 `halted`로 남아 PI 재개를 기다리고, 재개하면 같은 세션으로 이어 간다. 2절 폭주 차단기는 건드리지 않았다.
- 바뀐 것:
  - gateway(`labhq/gateway/server.py`): `POST /api/tasks/{id}/steer|pause|resume|halt`(PI 토큰, 방향 2,000자). (request, step)마다 방향 대기열(20개, 넘치면 오래된 것부터 버리고 수를 셈), 정지 표시, 작업별 멈춤 요청을 store에 둔다. 기한으로 지우지 않는다. 다음 dispatch에 남은 방향을 싣고(`meta.steers`), 정지 중에는 그 단계의 다음 turn을 보내지 않는다(요청은 `waiting_pi`, `request.step_paused`). 끝난 작업 줄에서 보낸 제어는 같은 단계의 지금 turn에 간다. 재연결 때는 받은 작업에만 보내고, 아직 못 받은 작업은 작업을 먼저 보낸 뒤 보낸다. 멈춤을 보낸 작업이 runner 재시작 등으로 끊기면 `halted`로 바꾼다. 멈춘 단계는 PI 재개(`/resume`)까지 park하고, 대기가 끝나는 모든 경우(재개·기한·요청 취소)에 화면에 알린다. 단계가 끝날 때 쓰이지 않은 방향은 `agent.control`(undelivered)로 알린다.
  - runner(`labhq/runner/control.py`, `daemon.py`): turn 간 상태와 기한 정리가 없다. 맡은 작업(실행 중·실행 순서 대기·시작 전 정지)에만 적용하고, 맡지 않은 작업은 `control.nack`. 제어 파일(`runner.state_dir/control/<task>.json`, 직원 쓰기 범위 밖)의 모든 읽기·쓰기는 파일 잠금 아래이고, `finish()`도 잠금 아래에서 읽고 지운다. 시작 전 turn은 정지가 풀릴 때까지 시작하지 않고, 기다리던 방향을 프롬프트 앞에 붙이며(Claude도 같다. hook으로만 주면 도구를 쓰지 않는 turn에는 닿지 않는다), 멈추면 CLI 없이 `halted`. 작업이 끝나면 전달한 방향과 못 한 방향을 `task.result.control`로 보고한다. 이전 프로세스가 남긴 파일은 합치지 않고 지운다.
  - hook(`labhq/hooks/control.py`, 표준 라이브러리만): PreToolUse는 멈춤이면 `{"continue": false}`, 정지 중이면 최대 5분 붙잡은 뒤 "PI가 일시 정지함"으로 deny. PostToolUse·PostToolUseFailure는 멈춤이면 `continue: false`, 아니면 방향을 `additionalContext`로 한 번 전달한다(9,000자 이하로 나눔).
  - adapter: 멈춤은 hook 실행이면 60초 유예 뒤, 아니면 바로 프로세스를 끝내고 session id를 남긴다. CLI 시작 기록이 실패하면 프로세스를 끝낸다.
  - 범위(2차 검증): 계획 단계 작업(kind step + step id)과 direct 작업만 받는다. 상담·이어 묻기·리뷰·브리핑·선행 연구·채용 등은 API·CLI가 409와 한국어 안내로 거절하고 웹에 줄이 없으며, 그 dispatch는 단계 대기열·정지를 보지 않는다. HPC 잡·질문 대기가 걸린 turn의 멈추기는 409("일시 정지를 쓰세요"). 대기에 들어가며 받은 멈춤은 잡·질문과 대기 뒤 turn을 남긴 채 그 단계의 일시 정지가 된다. 멈춘 뒤 재개하면 멈추기 전에 걸린 일시 정지도 풀고, 멈춘 뒤 다시 건 일시 정지는 남긴다. dispatch보다 먼저 간 방향은 NACK 뒤 작업 수락 때 한 번 더 보낸다. 대기열 상한은 아직 turn에 주지 않은 방향만 센다. 실행 슬롯을 잡은 뒤 받은 정지는 이번 turn은 계속한다고 알린다. 웹은 요청이 끝나면 줄을 닫고, 정지·멈춘 단계가 있으면 waiting_pi를 유지하며, 취소로 닫힌 멈춤은 취소라고 적는다.
  - CLI `labhq task steer|pause|resume|halt <id> [text]`. 웹 직원 상세 시트에 작업마다 제어 줄(방향 입력·일시 정지/재개·멈추기). halted는 재개·만료·취소 이벤트로만 지우고, 끝난 작업 줄은 종료 이벤트에서 지워 새로고침 화면과 같다. 정지된 단계는 마지막 작업 줄에서 재개한다.
  - `docs/manual.md` 직원 제어 절(엔진별 표, 제어가 남는 곳).
  - 기존 test 2곳: `test_observed_outputs`는 PostToolUse 항목을 명령으로 골라 보고, `test_web_3d` 목록에 새 node test를 넣었다.
- 리뷰 경과: 봇 리뷰 1차(P1 1·P2 3)·2차(P1 1·P2 4), 검증 워크플로 16건, 재검증 18건을 반영했다. 재검증 18건은 범위를 좁혀(단계·direct만) 닫았다. 검증 16건은 대부분 runner의 turn 간 carry와 기한 정리에서 나와, carry를 없애고 단계 상태를 gateway로 옮겼다.
- 실행한 것: hook 계약은 공식 문서로 확인했다(https://code.claude.com/docs/en/hooks 의 PreToolUse·PostToolUse decision control, JSON output `continue`/`stopReason`, matcher, timeout 600초; 재개는 https://code.claude.com/docs/en/headless). 실제 `claude -p`는 부르지 않았다. test: `tests/test_staff_control.py`(hook·runner·gateway·CLI 17), `tests/test_staff_control_review.py`(리뷰 1·2차 5), `tests/test_staff_control_steps.py`(검증 항목별 13), `tests/test_staff_control_scope.py`(재검증 18), node `web_staff_control.cjs`·`web_staff_control_tasks.cjs`·`web_staff_control_steps.cjs`·`web_staff_control_scope.cjs`. 검증 항목 test 13개와 `web_staff_control_steps.cjs`는 4fdf454에서, 재검증 test 18개와 `web_staff_control_scope.cjs`는 81bd6da에서 모두 실패한다(경합 test 하나는 API가 바뀌어 실패). Windows 전체 `pytest -q` 5383 passed·66 skipped, `for f in tests/*.cjs; do node --test "$f"; done` 36개 통과, `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.
- 미해결: (1) 실제 Claude에서 `continue: false` 뒤 같은 session id로 resume하는 실측은 하지 않았다(문서 근거만). (2) hook은 도구 호출마다 Python을 두 번(Pre·Post) 띄운다. 이 PC에서 한 번에 약 0.2초. (3) 정지 중 붙잡힌 도구 호출은 무응답 경고를 낼 수 있다. (4) gateway 재시작 때 멈춘 단계의 park와 정지 대기는 다른 PI 대기처럼 사라지고 요청은 `중단됨`이 된다(단계의 방향 대기열은 store에 남는다). (5) 전달이 불확실한 dispatch(runner 연결이 끊긴 채 기한을 넘김)의 방향은 단계로 돌아가 두 번 전달될 수 있다(잃지는 않는다).
- 근거: `labhq/gateway/server.py`, `labhq/runner/control.py`, `labhq/runner/daemon.py`, `labhq/hooks/control.py`, `labhq/adapters/base.py`, `labhq/orchestrator/cso.py`, `labhq/web/state.js`, `labhq/web/ui/control.js`, `tests/test_staff_control.py`, `tests/test_staff_control_review.py`, `tests/test_staff_control_steps.py`, `tests/test_staff_control_scope.py`, `tests/web_staff_control_steps.cjs`, `tests/web_staff_control_scope.cjs`, `docs/manual.md`.

## 단계
P1 — #276 fake MCP 장시간 대기·교차 세션 inbound 마감

## 한 일

- Claude·Codex adapter가 만든 timeout을 fake CLI에서 기록해 둘 다 1,320초인지, 90초 응답을 timeout·재시도 없이 한 번만 받는지 단언했습니다.
- Claude 직원의 모든 profile이 `crossSessionInbound: refuse`와 `SendMessage`·`ListAgents` deny를 만드는 기존 test를 확인했습니다.
- 격리된 전체 test가 찾은 `LABHQ_STATE_DIR` import 시점 캡처를 env-sensitive `default_factory`로 고쳤습니다. MCP 경로 결함은 없었습니다.

## 테스트 결과

- [x] 관련 pytest: 72 passed, 3 skipped
- [x] state env 회귀: 9 passed
- [x] Windows 전체 `pytest -q`: 5030 passed, 64 skipped
- [x] `scripts/check_public.sh`
- Claude Code 2.1.289: `claude --version`, `claude --help`, `claude agents --help`; 설치본 changelog에서 `crossSessionInbound` 추가·거부 동작을 확인했습니다.
- 공개 schema `https://json.schemastore.org/claude-code-settings.json`에는 아직 키가 없습니다. 공식 설정 문서도 schema가 최신 CLI보다 늦을 수 있다고 밝힙니다.
- 공식 문서: https://code.claude.com/docs/en/cross-session-messaging#turn-off-cross-session-messaging

## 바꾼 파일

- `tests/test_long_mcp_call.py`, `tests/fixtures/fake_mcp_client.py`
- `labhq/settings.py`, `docs/manual.md`

## 막힌 점

- 실제 Codex 장시간 probe는 지시대로 실행하지 않았습니다. #276의 남은 일은 이 probe뿐입니다.
- CI는 기록 commit push 뒤 확인합니다.

## PI·Claude에게 물을 것

- 없음.

## 확인

- [x] `scripts/check_public.sh` 통과
- [x] `docs/status/2026-10-12-0451-mcp-long-wait.md`에 같은 보고 추가
- [x] 실제 CLI 모델 호출·장시간 probe를 실행하지 않음

Refs #276

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 2026-10-12 · gateway 수명주기 후속 묶음 (#521)

- 결론: #521 "gateway 수명주기"의 P2 6건(교차 검토 10·11·12·13·14·18)과 #526 리뷰의 채용 task 지적을 고쳤다. 7건 모두 main에서 test로 먼저 재현했다. 같은 묶음의 웹 상태 표시(`state.js:184`)는 이번 범위가 아니다.
- 바뀐 것:
  - 채용 수(11·18): `recruits_running`이 채용을 시작한 러너의 `process`를 기억하고, 다른 `process`가 붙으면 비운다. incarnation은 재시작해도 같아서 지금까지는 `labhq down`이 gateway를 다시 켤 때까지 "진행 중 채용"으로 거부했다. `process`를 보내지 않는 이전 러너는 지금의 incarnation 규칙을 따른다(#526과 같은 호환 규칙).
  - 꺼진 러너에 보낸 채용(18): 보내지 않은 것이 확실하면(`RunnerNotSent`: 연결 없음, 보내기 전에 연결 교체) 세지 않고 409로 답한다. 보내다 실패한 경우는 전처럼 센다.
  - 러너의 채용 turn(#526 리뷰): 채용 변환 turn을 러너의 `tasks`에 넣는다. 끝난 뒤 늦게 온 승인 답이 recruiter를 `working`으로 되돌리지 않는다.
  - 취소한 요청의 이어 묻기(10): gateway가 409로 거절하고 웹은 입력창을 숨긴다. 취소한 요청은 모든 turn을 거절해서 이어 묻기가 늘 실패로 끝났다.
  - 취소 기록 창(12): 요청에 `cancelling`을 두고 terminal record와 같은 commit에서 지운다. 그동안 `/api/shutdown-readiness`의 `finishing`(stage `cancel`)에 올라 `labhq down`이 거부한다. 그 전에 gateway가 멈추면 다음 시작 때 보고서와 terminal record를 마저 쓰고, 그때까지 결과를 보내지 않은 turn은 미집계로 센다.
  - 요청 묶음 복사(14): 끝나지 않은 `terminal_tasks`도 `finishing`(stage `bundle`)에 올린다. CLI는 두 경우를 `마무리 중인 요청:` 줄로 보여 준다. 이 키를 모르는 이전 CLI도 `ready: false`라 거부한다.
  - 러너 대기(13): 러너가 붙거나 명단을 바꾸면 러너를 기다리던 단계가 모두 다시 판정한다. 기다릴 러너가 없으면 24시간을 채우지 않고 "연결된 러너 중 X 직원을 맡은 곳이 없습니다"로 바로 끝난다. 러너가 맡던 직원 없이 다시 붙은 경우와, 저장된 명단이 없는 gateway에서 러너가 없어 기다리던 첫 직원(CSO·direct)을 맡지 않는 러너가 먼저 붙은 경우가 해당한다(뒤엣것은 PR #530 리뷰 P2).
  - `docs/manual.md`: down, 러너 대기, 이어 묻기, 요청 취소, recruit 줄.
- 실행한 것: 새 test 11개(lifecycle 9·CLI 1·web 1)와 readiness 결과를 통째로 비교하는 기존 test 1개(`finishing` 추가)가 main 코드에서 실패하고 이 branch에서 통과했다. main(#522·#527)을 병합한 head에서 전체 `pytest -q`(Windows) 5135 passed·64 skipped. 병합 충돌은 #522와 import 두 줄, #527과 `server.py` helper 자리 하나였고, #522 테스트의 가짜 러너에 `tasks`를 더했다. `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.
- 미해결: 시작할 때 끊긴 요청 묶음을 다시 만드는 것(14의 선택 항목)은 하지 않았다. 묶음 정보가 없는 옛 요청까지 다시 복사하지 않으려면 별도 표시가 필요하다. 강제 종료(`down --force`)나 crash 때는 여전히 묶음이 빠질 수 있다. 처음 켠 gateway에서 다른 직원만 맡은 러너가 먼저 붙으면, 그 직원을 맡을 러너가 뒤에 붙을 예정이어도 첫 요청은 바로 끝난다. 맡은 러너가 없는 직원은 바로 실패한다는 R15 판정과 같다.
- 근거: `labhq/gateway/server.py`, `labhq/models.py`, `labhq/recruit/paper2agent.py`, `labhq/cli.py`, `labhq/web/index.html`, `tests/test_request_lifecycle.py`, `tests/test_cli_launch.py`, `tests/web_followup.cjs`, `docs/manual.md`.

## 2026-10-12 · 단계 없이 끝난 요청의 요청 묶음 경고 제거

- 결론: 단계를 하나도 돌리지 않고 끝난 요청(CP1 수정 요청, 작업 전 거절·취소·실패)은 요청 묶음을 만들지 않고 경고도 남기지 않는다. 묶을 것이 없는데 웹·CLI 목록에 "요청 묶음을 만들지 못했습니다: 단계 작업 폴더를 하나도 찾지 못했습니다"가 뜨던 잡음이다.
- 바뀐 것: `Hub.commit_terminal`이 `results`가 비어 있고 그 요청의 step·direct task가 한 번도 보내지지 않았으면 terminal 기록만 남기고 묶음 시도를 건너뛴다. 실행 중 취소돼 `results`는 비었지만 단계 작업 폴더가 있을 수 있는 요청은 지금처럼 묶음을 시도한다(봇 리뷰). 단계 결과가 하나라도 있으면 지금처럼 묶음을 만들고, 실패하면 경고를 남긴다. manual 요청 묶음 절에 한 문장을 더했다.
- 실행한 것: trial 실측(CP1 수정 요청 req_d13c37a5df → 새 요청 req_881839f8b7, 둘 다 경고가 남음)에서 발견했다. 새 test 2건(완료·실패)이 main 코드에서, 취소 중 단계 test 1건이 첫 커밋에서 실패하고 이 branch에서 통과한다. 빈 `results`로 묶음 실패를 흉내 내던 기존 test는 단계 결과 하나를 넣어 같은 성질(실패는 경고만, 재시작 때 다시 안 만듦)을 계속 본다. 전체 pytest 5033 passed·64 skipped(Windows).
- 미해결: 없음.
- 근거: `labhq/gateway/server.py`, `tests/test_request_bundle.py`.

## 단계
P2 — #521 연구 lane 상태 4~9

## 한 일

- 마지막 연구 보고 turn에서 처음 예산을 넘으면 카드를 띄우지 않고 초과 기록과 보고서를 남깁니다. 끝난 일에 7일짜리 결정을 요구하지 않는 안을 택했습니다.
- 확인 질문 카드 상한을 연구 차수마다 세고, 이미 답한 질문은 전체 기록에서 계속 거릅니다.
- 새 이어 가기 계획 전 재개 카드, 미승인 계획의 취소·재개 거절·만료 복원, 보관된 차수의 Live 출처 재질문을 바로잡았습니다.
- 이어 가기 prompt의 재사용 조건에 가설 field 세 개를 명시했습니다.
- `docs/manual.md`의 예산·재개 카드·리뷰/이어 가기 절을 105,355자에서 105,493자로 고쳤습니다.

## 테스트 결과
- [x] `origin/main` 동작 + 새 회귀: 8 failed, 68 passed
- [x] 최신 `main` 병합 뒤 관련 회귀: 197 passed
- [x] 최신 `main` 병합 뒤 웹 표시 회귀: 11 passed
- [x] 최신 `main` 병합 뒤 Windows 전체 `pytest -q`: 5042 passed, 64 skipped

## 바꾼 파일

- `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `labhq/research/continuation.py`
- `tests/test_research_continue.py`, `tests/test_research_notes_budget.py`, `tests/test_research_report.py`, `tests/test_request_lifecycle.py`
- `docs/manual.md`

## 막힌 점

없음.

## PI·Claude에게 물을 것

없음.

## 확인
- [x] `scripts/check_public.sh` 통과
- [x] `docs/status/2026-10-12-0405-research-state-followups.md`에 같은 보고 추가
- [x] 추가 PI 확인이 필요한 체크포인트 없음

Refs #521

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 결론

#521 웹·CLI 묶음 8건(15·19·20·21·22·23·24·25)을 모두 main에서 재현하고 고쳤습니다. 15와 22는 같은 원인이라 수정 하나로 닫았습니다.

## 바뀐 것

| # | 증상 | 수정 |
|---|---|---|
| 15·22 | 한도·로그인·환경 대기가 풀릴 때 열린 결정 카드가 있어도 웹이 `진행 중`(gateway·CLI는 `PI 결정 대기`) | `state.js` `syncHoldStatus`가 PI 결정 카드(clarify·question·scope·budget·CP1·CP2·이어 가기)가 열린 요청은 `waiting_pi`로 되돌림. gateway `_sync_hold_status` 순위와 같음 |
| 19 | 계획 없는 요청(direct, 계획 전 중단)의 재개 카드 상세가 `남은 단계 없음` | gateway 재개 카드 detail에 `has_plan`. 웹은 `false`면 `요청 전체`로 적고 이 값은 따로 보이지 않음 |
| 20 | `labhq approve`가 선택지 있는 직원 질문을 빈 답으로 승인 | `--note` 없으면 exit 2와 선택지(`a) …`)를 보여 주고 요청은 그대로. `labhq approvals`에도 선택지 표시. `--deny`는 그대로 거절 |
| 21 | gateway가 `runner.offline` 없이 죽으면 `since`로 다시 붙은 페이지가 **러너 꺼짐** 띠를 숨김 | 시작할 때 이전 프로세스가 온라인으로 본 러너와 저장된 roster의 러너 중 마지막 이벤트가 offline이 아닌 것마다 `runner.offline`(`reason: gateway_restarted`)을 남김. 웹 메신저는 이 경우 alert 없이 "게이트웨이가 다시 시작돼 러너 연결을 기다려요" |
| 23 | 시간 초과 카드가 메신저에 `나: 거절했어요` | `state: timed_out`은 "시간 초과로 닫혔어요", `expired`·`cancelled`는 "답 없이 닫혔어요"(시스템 발화) |
| 24 | CLI watch가 CP1 수정 요청을 `거절`로 적음 | `수정 요청`, 시간 초과는 `⏰ 승인 결과: 시간 초과`로 따로 |
| 25 | 첫 스냅샷 전 요청 카드가 "아직 요청이 없어요" | "요청 불러오는 중"(토큰 없으면 "토큰을 넣으면 요청을 불러와요"). key가 연결 상태를 따라 스냅샷·토큰이 오면 다시 그림 |

`docs/manual.md`: 104,383자 → 104,850자(+467자). 러너 꺼짐 띠, 첫 스냅샷 전 표시, 요청 상태·메신저, CLI `approve`·`watch`, 재개 카드 남은 단계를 고쳤습니다.

## 실행한 것

- main + 새 테스트: web node 3파일(`web_request_lifecycle`·`web_decision_detail`·`web_cards_status`) 실패, pytest 5건 실패
- 수정 뒤: web node 31파일 전부 통과(golden hash `web_state.cjs` 포함), `test_web.py`·`test_web_3d.py`·`test_cli_launch.py`·`test_request_lifecycle.py` 127 passed, 2 skipped
- Windows 전체 pytest: 5025 passed, 64 skipped
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과

## 미해결

- gateway `resolve_approval`은 선택지 있는 직원 질문의 빈 승인을 막지 않습니다. 웹·3D·CLI만 막고, API를 직접 부르는 클라이언트용 강제는 넣지 않았습니다.
- `has_plan` 전에 저장된 재개 카드는 상세에 계속 `없음`으로 보입니다(요약 줄은 맞고, 7일 뒤 닫힘).
- 저장된 이벤트 창(2000개)에서 밀려난 러너는 재시작마다 `runner.offline` 한 줄이 더 남을 수 있습니다. 메신저에는 alert 없이 한 줄입니다.

## 근거

- `labhq/web/state.js`, `labhq/web/ui/decide.js`, `labhq/web/index.html`
- `labhq/gateway/server.py`(`Hub.__init__`, `new_resume_approval`)
- `labhq/cli.py`(`_question_options`, `render`, `approvals`·`approve`)
- `tests/web_request_lifecycle.cjs`, `tests/web_decision_detail.cjs`, `tests/web_cards_status.cjs`, `tests/test_request_lifecycle.py`, `tests/test_cli_launch.py`

## 2026-10-12 · 러너를 다시 시작한 뒤 남는 도구 권한 카드를 닫는다 (#521)

- 결론: runner는 incarnation을 디스크에 두고 재시작해도 같은 값을 쓴다. 그래서 이전 프로세스가 띄운 카드가 R2 판정을 빠져나가 스냅샷에 계속 남았고, 승인해도 아무 일이 없었다. 이제 재시작과 재연결은 프로세스마다 새로 만드는 `process` 값으로 가린다. 카드를 띄운 task가 결과를 보내도 그 카드를 닫는다.
- 바뀐 것:
  - `runner.hello`에 `process`(Runner마다 uuid4, 저장하지 않음)를 싣는다. gateway는 runner 카드에 `origin_process`로 남기고 `_orphan_reason`에서 붙는 프로세스와 비교한다. incarnation은 지금처럼 task 세대 판정에 쓴다.
  - 호환: `process`가 없는 이전 러너끼리, 그리고 이 변경 전에 저장된 카드는 지금의 incarnation 규칙을 그대로 따른다.
  - 보강: 카드의 task가 `task.result`를 보내면 그 카드를 기존 expired 경로로 닫는다(note "그 작업이 끝나 닫힘"). 살아 있는 runner에도 거절이 가서 hook이 timeout까지 기다리지 않는다. 알림만 하는 `codex_sandbox_setup` 카드는 남긴다.
  - runner: 끝난 task의 카드에 늦게 온 답이 직원 상태를 `working`으로 되돌리지 않는다. daemon 변경은 4줄이다.
  - `docs/manual.md` R2 문단.
- 실행한 것: main(d083828c)에서 재현 스크립트로 확인했다. 같은 state로 Runner를 두 번 만들면 incarnation이 같고, 재등록 뒤 `expire_orphan_approvals`는 `[]`, `task.result` 뒤에도 카드가 남았다. 새 test 4개(같은 incarnation 재시작은 websocket 경유, 같은 프로세스 재연결 유지, task.result로 닫힘, 이전 러너 호환·업그레이드, 늦은 답의 상태)는 main 코드에서 4 failed, 이 branch에서 통과한다. Windows 전체 `pytest -q` 5020 passed·64 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.
- 미해결: `recruits_running`도 같은 incarnation 문제를 안고 있다(#521 P2 11·18). 이번 `process` 값을 그대로 쓰면 고쳐지지만 이 PR 범위 밖이라 남긴다.
- 근거: `labhq/gateway/server.py`, `labhq/runner/daemon.py`, `tests/test_request_lifecycle.py`, `docs/manual.md`.

## 2026-10-12 · v0.5.0 이후 교차 리뷰 #2 #3 #16 #17 — 승인 게이트·공유 환경 설치 판정 회귀

## 결론

교차 리뷰가 확인한 결함 4건을 고쳤습니다. 결함마다 사례 하나가 아니라 그 부류를 닫는 구조 변경 하나씩입니다.

| 리뷰 | 등급 | 닫은 부류 | 변경 |
|---|---|---|---|
| #2 | P1 (v0.5.0 대비 회귀) | 다운로드 명령의 쓰기 경로가 쓰기 허용 폴더 검사에 닿지 않는 경우 | curl·wget 옵션을 표 하나로 읽는 walker. 출력 operand는 변수 앞 리터럴 부분(`-o "C:/x/$f"` → `C:/x/`)으로 검사. 묶은 짧은 옵션(`-fsSLo`, `-qO`), 붙여 쓴 값, `=` 형식, getopt 약어(`--out`), 파일을 쓰는 다른 옵션(curl `-D`·`-c`·`--trace` 등, wget `-o`·`-a`), PowerShell `Invoke-WebRequest`·`Invoke-RestMethod` `-OutFile`(Windows PowerShell의 `curl`·`wget` 별칭 포함) |
| #3 | P1 | 다른 프로그램의 인자 자리에 있는 변수를 설치 선택자로 보는 경우 | "변수 인자가 있으면 설치" 규칙을 실행 파일 자리에만 둠. 인자 자리 변수는 리터럴 설치 동사(`install`·`create`)가 뒤따를 때만 설치. 실행 파일 자리는 wrapper(`nohup`·`sudo`·`time`·`timeout`·`nice`·`stdbuf`·`xargs`·`env`·`command`·`exec`·`uv run`·`conda run`)의 옵션을 건너뛴 다음 단어까지 포함하고(모르는 옵션이면 변수가 있을 때 거절), python·Rscript는 script 경로 앞의 변수, 변수로 시작하는 `-c`·`-e` 코드, `-m` 뒤 변수, 설치 module 뒤 변수를 거절(리뷰 2차) |
| #16 | P2 | 프로그램 이름이 변수라서 R 설치 호출을 놓치는 경우 | 따옴표로 감싼 변수 실행 파일이 R 설치 호출을 받으면 R로 보고 Rscript와 같은 `lib=` 규칙 적용. wrapper 뒤 인자 자리의 변수도 R일 수 있는 것으로 봄 |
| #17 | P2 | lexical 정규식이 못 보는 재귀 삭제 | rm의 `--` 앞 모든 옵션 단어(`-f -r`, `-v -rf`, `--recursive`, 대상 뒤 `-rf`)를 보고 기존 `.tmp` 규칙으로 보냄. `find -delete`·`-exec rm`(`sh -c` 본문 포함)·`\| xargs rm`도 같은 규칙(단일 명령, 시작 경로가 모두 `.tmp` 아래 리터럴 상대 경로). `outputs/` 등 작업 폴더의 다른 곳은 `rm -rf outputs/x`처럼 질문. cmd `rd /s`·`del /s`는 언제나 질문. PowerShell은 첫 pipe 뒤(script block 포함)의 Remove-Item 계열·`.Delete()`를 재귀 삭제로 보고, pipe 앞 `Get-ChildItem`의 폴더가 모두 `.tmp` 아래 리터럴일 때만 허용(리뷰 2차) |

## 바뀐 것

- `labhq/policy.py`
  - `_shell_word_prefix`: shell word의 리터럴 앞부분과 완결 여부. `_literal_shell_word`는 이 위의 얇은 wrapper라 동작이 같습니다.
  - `_DOWNLOAD_OPTIONS` 표와 `_download_write_targets` 재작성. `_download_options_known`도 같은 표를 씁니다. 모르는 글자가 낀 묶음은 그 뒤 출력 글자의 값을 보고합니다(과보고는 허용 폴더 밖 절대경로일 때만 질문이 됨).
  - `_ps_download_targets`: `-OutFile`의 모든 prefix.
  - `_recursive_rm`, `_find_deletes`, `_find_delete_inside_step_tmp`, `_CMD_RECURSIVE_DELETE`, `_ps_piped_delete`·`_ps_piped_delete_inside_step_tmp`. rm·find·PowerShell pipe 삭제는 대상 검사 `_targets_below_step_tmp`(기존 `_recursive_delete_inside_workdir`에서 떼어 냄)를 같이 씁니다. 설정의 `BASH_RECURSIVE_DELETE_PATTERN`은 바꾸지 않았고, 이 pattern이 `bash_ask_patterns`에 있을 때만 새 검사도 돕니다(설정에서 빼면 재귀 삭제 검사 전체가 꺼지는 기존 의미 유지).
  - `.tmp` 재귀 삭제 허용이 `-v`·`--recursive`·`--force`·`--verbose`도 받습니다. 새로 재귀로 잡히는 `rm -v -rf .tmp/x`가 질문으로 바뀌지 않게 하려는 것입니다.
- `labhq/environment_install.py`: `_dynamic_installer_syntax`의 비리터럴 인자 조항을 실행 파일 자리로 한정. 주 루프에 `r_program`(R 실행 파일 또는 R 설치 호출을 받는 변수 실행 파일). `_installation_syntax`의 R 검사가 변수 단어도 R 후보로 봅니다. `_EXEC_WRAPPERS`·`_RUN_SUBCOMMANDS` 표와 `_wrapped_program_is_computed`(wrapper 옵션을 건너뛰고 실행 파일 규칙을 재귀 적용), `_python_selects_computed`, `_rscript_selects_computed`.
- `docs/manual.md`: 공유 환경 설치 문단, 승인 게이트의 재귀 삭제 문단, 셸 쓰기 검사 항목.

## 실행한 것

- main에서 재현: 네 결함 모두 리뷰가 적은 대로 재현(`evaluate_tool` windows=True, `shared_environment_install_denial` protected=True).
- 새 test 102건: origin/main 코드에서 73건 실패, 이 branch에서 100건 통과·2건 skip(Windows 일반 계정은 symlink를 못 만들어 `.tmp/link` 시작 경로 test 2건 skip). 나머지 27건은 유지 동작을 고정하는 test로 main에서도 통과합니다(`"$PY" script.py "$INPUT"` 거절, `.tmp` 아래 삭제 허용, 작업 폴더 안 다운로드 허용 등).
- 리뷰 2차(139d027의 P1 세 건) test 82건: 139d027 코드에서 51건 실패(wrapper 22·interpreter 20·PowerShell pipe 삭제 9), 이 branch에서 82건 통과. find `--`·`-files0-from`은 139d027에서도 이미 질문해서 고정 test만 더했습니다.
- 기존 test는 지우거나 바꾸지 않았습니다. 관련 5개 파일 851 passed·7 skipped.
- Windows 전체 pytest(리뷰 2차 반영 뒤): 5212 passed, 66 skipped.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check`, `git diff --check` 통과.

## 미해결

- `~`로 시작하는 쓰기 경로(`cp a ~/x`, `echo x > ~/.bashrc`, `curl -o ~/x`)는 쓰기 허용 폴더 검사를 받지 않습니다. v0.5.0에도 있던 결함이고 #521에서 다룹니다.
- python·Rscript의 script 경로가 변수인 반복문(`for f in s/*.py; do python "$f"; done`)도 이제 거절합니다. 변수가 `-m`·`-c`가 될 수 있어서이고, script 경로를 리터럴로 쓰면 통과합니다.
- 재귀 삭제 검사는 여전히 lexical입니다. `echo "rm -f -r x"`, `git rm --cached -r x`처럼 지우지 않는 명령도 질문할 수 있고, 미리 허용된 셸(`Bash(python *)` 등) 안의 삭제는 게이트를 거치지 않습니다.

## 근거

- `labhq/policy.py`
- `labhq/environment_install.py`
- `tests/test_shell_write_quotes.py`
- `tests/test_policy.py`
- `tests/test_environment_install.py`
- `docs/manual.md`

## 변경

- 요청 묶음의 재현 등급과 근거를 MANIFEST, RO-Crate, verify, 웹·CLI에 표시합니다.
- 환경 lock을 complete/partial/unavailable로 기록하고 제출한 HPC 잡 스크립트를 hash와 함께 묶습니다.
- restricted/private 입력은 경로와 hash를 남기지 않으며, 유효한 재실행 receipt가 있을 때만 rerun_verified를 부여합니다.

## 검증

- Windows 전체 pytest: 5017 passed, 63 skipped
- 관련 pytest: 323 passed, 5 skipped
- Node test 파일 31개 통과
- `bash scripts/check_public.sh` 통과

Closes #423

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 요약

- direct 응답이 기존 `declare_output_types` 어휘로 실제 `outputs/` 산출의 데이터·형식 종류를 함께 선언합니다.
- runner가 기존 파일 상한·허용 구역·정규 파일 검사 뒤 해시와 종류 기록을 남기며, 요청 묶음·RO-Crate·`labhq verify`의 기존 direct 산출 경로를 그대로 씁니다.
- 두 번째 direct 요청의 재사용 후보는 첫 산출에 종류 선언이 있을 때만 잡히고, 없으면 `type_unknown`을 유지합니다.

## 검증

- `origin/main` + 새 회귀검사: 2 failed, 52 passed, 2 skipped
- 관련 semantics·bundle·verify 회귀: 721 passed, 6 skipped
- Windows 전체 pytest: 5020 passed, 64 skipped
- `bash scripts/check_public.sh`: 통과

## 문서

- `docs/manual.md`: 104,097자 → 105,268자(+1,171자)

Closes #221

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 2026-10-12 · 파견직 채용물 고정과 외부 전송 선언 (PR #522)

## 결론

파견직 계약서에 원본 commit·license·외부 전송 대상과 labhq가 직접 잰 skill·MCP hash가 남습니다. `external_services`가 있는 파견직은 입력이 restricted 구역에 닿는 단계를 맡지 못합니다. CSO 계획 검증에서 먼저 거부하고, 러너가 엔진을 띄우기 전에 한 번 더 거부합니다. restricted 작업에서 그런 파견직에게 묻는 상담(`colleague:<id>`)도 refs 복사 전에 거부합니다.

## 바뀐 것

- `ContractInfo`·`OFFER_SCHEMA`: `source_commit`(40-hex 검증), `code_license`, `weights_license`, `external_services[{host, what_is_sent}]`. 계약서에는 `skill_sha256`·`mcp_sha256`과 MCP hash를 잰 대상 `mcp_path`(ZIP만 온 경우 그 파일, 리뷰 P2)도 들어가고 재고용 때 같은 대상을 다시 잽니다. 예전 계약서는 기본값으로 읽힙니다.
- hash는 오퍼레터 값을 믿지 않고 `plugin_provenance(..., every_file=True)`로 labhq가 계산합니다. git checkout이면 git 목록, 아니면 모든 파일을 넣고 `.venv`·`__pycache__` 등은 어느 쪽이든 뺍니다. 큰 weight 파일을 생각해 1 MiB 단위로 읽습니다.
- 수습: Paper2Agent `offer_status`가 초안 상태(`mechanical_failure`·`unreviewed`·`unresolved_discrepancies`·`partial`·`blocked`)면 `probation`에 남깁니다. 이유는 `verification.probation_reason`에 적힙니다.
- 재고용: hash를 다시 재서 다르면 `verification.rehire_changed`, `contract.warning` 이벤트(피드 경고), 직원 카드 표시. 재고용 자체는 막지 않습니다.
- restricted 판정: 요청 본문·참고 경로·프로젝트 폴더, 단계 지시, 선행 단계 지시를 `policy.touches`·`overlaps_restricted`로 봅니다. CSO 계획(일반·연구·재계획)은 `PlanAgentError`로 교정을 받고, 단계 task에는 `restricted_inputs` 판정이 실립니다. 러너는 `external_service_refusal`로 엔진 실행 전에 거부합니다. 오류에는 구역 경로를 넣지 않습니다.
- 상담(리뷰 P1): 러너가 묻는 작업의 restricted 판정을 ask token(`source_restricted`, 호출자가 못 지움)에 싣습니다. 게이트웨이는 그 판정이나 요청 자체가 restricted면 외부 전송 파견직 상담을 거부하고, 다른 상담 task에는 `restricted_inputs`를 실어 러너가 다시 확인합니다.
- 수습(리뷰 2차 P1): main에서도 `probation` 파견직은 명단에 남아 CSO가 배정할 수 있었습니다. 문서는 미통과를 "probation 유지 · PI 확인"으로만 적었고, 수습 중 시험 업무를 일부러 허용하는 코드·테스트는 없었습니다. 그래서 의도된 동작이 아닌 누락으로 보고, `active`가 아닌 파견직을 CSO 명단·직접 요청(409)·상담에서 빼고 러너도 거부하게 했습니다. 웹 명단에는 `수습(PI 승인 대기)`로 보입니다. 호스팅 AlphaGenome 예시 계약서(`status: probation`)에는 activate 안내와 `external_services`를 적었고, 러너로 파견직을 돌리던 기존 test 5곳은 계약 상태를 `active`로 명시했습니다(검증 내용은 그대로).
- host(리뷰 2차 P1): `external_services[].host`는 `hostname[:port]`만 받습니다. 오퍼레터가 URL을 주면 hostname만 남기고 인증·경로·query·fragment는 버리며, 노트에도 원문을 옮기지 않습니다.
- 3차 리뷰(P2 3건): 납품된 skill·MCP의 hash를 못 재면 `active`로 올리지 않고 `probation_reason: provenance hash failed`로 남깁니다. 재고용 확인은 기록된 hash가 없는 부분을 건너뛰지 않고 `skill(hash 없음)`으로 표시합니다. `mcp_args`가 `mcp_dir` 밖의 압축 해제된 서버를 실행하면 그 폴더를 `mcp_entry_path`·`mcp_entry_sha256`으로 재고 재고용 때 다시 잽니다. 캐시 checkout의 origin을 읽을 수 없을 때도 pull하지 않고 새로 clone합니다.
- `setup-paper2agent --commit <40-hex>`·`recruit.skill_commit`: 그 commit을 fetch·checkout하고 다르면 기존 설치를 건드리지 않고 멈춥니다. `recruit.skill_source`가 캐시 checkout의 origin과 다르면 pull하지 않고 옆에 새로 clone해 바꿔 끼웁니다(리뷰 P2). 설치한 commit은 `.labhq-source.json`에 남고 채용 때 `verification.paper2agent_commit`이 됩니다.
- 웹: 직원 카드에 commit 7자리·license·외부 전송, 채용 피드 한 줄, 채용 제안 카드에 "채용하면 무엇을 남기는지" 안내. CSO 명단에 외부 전송 호스트 표시. `labhq talent` 출력에 commit·외부 전송.
- `docs/manual.md` 파견직 제도 절, 이벤트 표.

## 실행한 것

- 새 test(`tests/test_contract_provenance.py` 41개 + 리뷰 반영 13개, `tests/web_contract_provenance.cjs` 3개)는 main에서 실패(Python은 수집 단계 ImportError, node는 3개 모두 실패), 변경 뒤 통과. 리뷰 반영 13개는 직전 head(`cf538de`)에서 11개 실패(나머지 2개는 기존 동작 확인용), 수정 뒤 통과. 2차 반영 20개(test_cso 1개 포함)는 직전 head(`33288c7`)에서 15개 실패(나머지 5개는 정상 host·기존 계약서 확인용), 수정 뒤 통과. 3차 반영 5개는 `f4f0b16`에서 모두 실패, 수정 뒤 통과. 캐시 checkout fixture 두 곳(`test_paper2agent_install.py`, 이 파일의 `install`)에는 실제 clone처럼 origin 설정을 넣었습니다(단언 그대로).
- 관련 test(e2e mock·registry·paper2agent 설치·strict schema·cso·plugin hash·workspace boundary): 371 passed, 21 skipped.
- Windows 전체 pytest: 첫 실행 4 failed(plugin hash test가 `_plugin_files`를 인자 하나로 바꿔 끼움, encoding lint, 새 .cjs 미등록). `_folder_files` 분리·`open(path, "rb")`·CI node 목록 등록 뒤 5054 passed, 63 skipped. 리뷰 반영 뒤 5067 passed, 63 skipped. 2차 반영 첫 실행은 러너로 수습 파견직을 돌리던 기존 test 8건 실패, 계약 상태를 active로 명시한 뒤 5087 passed, 63 skipped. 3차 반영 뒤 5092 passed, 63 skipped. basetemp는 종료 뒤 삭제.
- web node test 32개 파일 모두 통과.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.

## 미해결

- `source_commit`·license·`external_services`는 recruiter의 신고값입니다. labhq는 형식만 검증하고, 선언하지 않은 외부 전송은 막지 못합니다.
- restricted 판정은 경로 문자열 기준입니다. 경로 없이 넘어온 restricted 내용은 잡지 못합니다.
- 헤드헌팅 결과의 license·API 키를 채용 요청으로 넘기는 경로는 만들지 않았습니다. recruiter가 변환 때 저장소에서 직접 확인합니다.
- Paper2Agent 기본값은 아직 고정하지 않습니다(`recruit.skill_commit: null`). PI가 commit을 정하면 그 값을 넣으면 됩니다.

## 근거

- `labhq/models.py`
- `labhq/recruit/paper2agent.py`
- `labhq/recruit/provenance.py`
- `labhq/adapters/claude_code.py`
- `labhq/adapters/mock.py`
- `labhq/registry.py`
- `labhq/runner/daemon.py`
- `labhq/orchestrator/cso.py`
- `labhq/settings.py`
- `labhq/cli.py`
- `labhq/web/ui/strip.js`, `labhq/web/ui/decide.js`, `labhq/web/state.js`, `labhq/web/index.html`
- `tests/test_contract_provenance.py`, `tests/web_contract_provenance.cjs`, `tests/test_e2e_mock.py`, `tests/test_web_3d.py`
- `docs/manual.md`

## 2026-10-12 · 승인 게이트: 백그라운드 셸 도구 기본 허용 (#518 후속)

- 결론: #518 뒤 분류되지 않은 도구는 PI에게 묻는다. Claude 직원이 백그라운드로 돌린 Bash의 출력을 읽거나 멈추는 도구(BashOutput·KillShell, 새 판 TaskOutput·TaskStop)도 그렇게 돼, 긴 분석에서 의미 없는 카드가 쌓일 수 있었다. 이 넷을 기본 허용 목록에 넣었다. 셸 명령 자체는 이미 Bash 게이트를 거쳤다.
- 바뀐 것: `labhq/settings.py` `_default_auto_allow`, `config/labhq.example.yaml`(패키지 사본도 같게), `docs/manual.md` 안전 장치 문장, `tests/test_policy.py`.
- 실행한 것: test가 main 코드에서 실패하고 이 branch에서 통과. trial 이벤트에서 Claude 직원 도구 이름을 세어 나머지(Bash·Read·Edit·Write·PowerShell·StructuredOutput·WebFetch·Grep)는 이미 허용 목록이나 파일·셸 규칙에 있음을 확인. 정책·init test, 전체 `pytest -q`(Windows) 5012 passed·63 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 봇 리뷰 P1 고침: `labhq init`이 #518 목록을 설정에 그대로 적어 새 기본값이 덮였다 → 목록이 #518 템플릿과 정확히 같으면 load 때 네 도구를 덧붙인다(PI가 고친 목록은 그대로). test 1.
- 미해결: PI·trial 설정은 `auto_allow_tools`를 직접 적지 않아 기본값을 쓴다(확인함).
- 근거: trial gateway 이벤트 `agent.tool` 집계, `labhq/policy.py` `_evaluate_tool`.

## 2026-10-12 · up/down 동시 실행과 POSIX 종료를 안전하게 처리한다 (#500)

- 결론: 같은 instance의 `labhq up`·`down`을 state 폴더의 OS lock으로 직렬화하고, POSIX 종료는 프로세스 신원이 사라질 때까지 기다린 뒤 필요하면 SIGKILL한다.
- 바뀐 것: Windows `msvcrt.locking`, POSIX `fcntl.flock` 기반 non-blocking lock과 같은 process의 thread lock을 추가했다. POSIX `_terminate_tree`는 기록된 생성 시각이 같은 process에 SIGTERM을 보내고 10초 뒤에도 남으면 SIGKILL한 다음 재확인한다. `docs/manual.md`에 두 동작을 적었다.
- 실행한 것: 새 Windows 회귀는 main 코드에서 2 failed·1 skipped였고 수정 뒤 `tests/test_cli_launch.py` 28 passed·1 skipped다. Windows 전체 `pytest -q`는 4989 passed·64 skipped다. 첫 CI 3.12에서 test용 `sys.platform` 값이 lock의 OS 판정까지 바꾼 결함을 확인해 `os.name`으로 고쳤다. `scripts/check_public.sh`, `scripts/patch_notes.py check`, `scripts/notes_index.py --validate`, `git diff --check`도 통과했다.
- 미해결: 없음. 봇 리뷰 판단과 병합은 총괄에게 넘긴다.
- 근거: `labhq/cli.py`, `tests/test_cli_launch.py`, `docs/manual.md`.

## 2026-10-12 · 승인 게이트의 native 위임과 Codex 확장 기능 차단

## 결론

분류되지 않은 도구는 PI에게 묻고, Claude Code native 위임은 일반 직원에게서 거부합니다. Codex 쓰기 직원은 multi-agent·memory·apps·plugins를 모두 끕니다.

## 바뀐 것

- `policy._evaluate_tool`: `Task`·`Agent`·`Workflow`·`TeamCreate`·`TeamDelete`를 설정값보다 먼저 deny. 알려진 비위임 도구만 `auto_allow_tools`에 둠.
- Claude Code: 일반 직원은 native 위임을 거부하고, 설정된 recruiter가 `Agent`를 선언한 Paper2Agent 변환만 예외.
- Codex: 쓰기·읽기 직원 모두 `multi_agent`·`memories`·`apps`·`plugins`를 `--disable`.
- manual과 두 example config를 맞추고, example config 두 벌의 바이트 동일성을 test로 고정.

## Codex 기능 확인

설치본에서 `codex features list`로 확인했습니다.

```text
codex-cli 0.160.1
apps          stable  true
memories      stable  true
multi_agent   stable  true
plugins       stable  true
```

## 실행한 것

- 수정 전 새 회귀: 9 failed.
- 관련 test: 182 passed, 4 skipped.
- Windows 전체 pytest: 첫 실행은 바뀐 `Task` 기대값 1건 실패, 기대값을 새 정책에 맞춘 뒤 4989 passed, 63 skipped.
- `bash scripts/check_public.sh`, `git diff --check` 통과.

## 미해결

없음.

## 근거

- `labhq/policy.py`
- `labhq/adapters/claude_code.py`
- `labhq/adapters/codex.py`
- `labhq/settings.py`
- `tests/test_policy.py`
- `tests/test_isolation.py`
- `tests/test_read_only_profile.py`
- `tests/test_intake_references.py`
- `docs/manual.md`

Fixes #421

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 2026-10-12 · 이어 간 연구 보고서의 "이전 차수에서 고친 것" 절을 보장한다

- 결론: #512는 보고서 작성자에게 "이전 차수에서 고친 것" 절을 쓰라고 지시만 했고, 빠뜨려도 확인하지 않았다(#512 리뷰 P2). 이제 그 절이 없으면 labhq가 이전 차수 리뷰 P1과 CP2 수정 요청 목록으로 절을 본문에 붙인다. 절이 없다는 이유로 요청을 `report_incomplete`로 만들지는 않는다(claim 검사는 앵커만 본다).
- 바뀐 것: `labhq/orchestrator/cso.py` `EARLIER_ROUNDS_HEADING`, `_earlier_rounds_fallback`, `_research_report`에서 리뷰 참고 앞에 붙임. 작성자가 쓴 절(제목 단계 무관)이 있으면 그대로 둔다. `docs/manual.md` 한 줄.
- 실행한 것: `tests/test_research_continue.py` 종단 test 단언 추가와 새 test 1건이 main 코드에서 실패하고 이 branch에서 통과. 연구 test, 전체 `pytest -q`(Windows) 4982 passed·63 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 봇 리뷰 P1 고침: 이어 간 벤치에서 대체 절이 `LABHQ_BENCH_RESULT` 블록 뒤에 붙어 채점이 형식 실패가 됐다. `_before_bench_block`으로 대체 절과 리뷰 참고 줄(연구·일반 경로)을 끝의 블록 앞에 넣는다. 새 test 2.
- 미해결: 없음.
- 근거: #512 리뷰 인라인 지적(cso.py:3971), `tests/test_research_continue.py`.

## 2026-10-11 · 웹 첫 화면이 빈 사무실로 보이지 않는다

- 결론: 페이지를 새로 열면 gateway의 첫 스냅샷이 올 때까지 "오늘의 할 일을 기다리는 중", "사용 비용 $0.00", "지금은 결정할 일이 없어요"가 보여 요청이 없는 사무실로 읽혔다(웹 연구 시운전 10-08). 이제 그동안은 "사무실 상태를 불러오는 중", "사용 비용 불러오는 중", "결정할 일을 불러오는 중"이다.
- 바뀐 것: `labhq/web/index.html` `snapshotLoaded`(데모는 처음부터 참, live는 첫 snapshot 이벤트에서 참). 상태 reducer(`state.js`)는 그대로라 2.5D·3D 공유 상태와 golden hash가 바뀌지 않는다.
- 실행한 것: `tests/web_cards_status.cjs`에 처리 경로 test 1건(main 코드에서 실패). web node test 전체, `tests/test_web.py`·`tests/test_web_3d.py`, inline module `node --check`, 전체 `pytest -q`(Windows) 4979 passed·63 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 봇 리뷰 P2 2건 고침: 토큰이 없거나 틀리면 스냅샷이 오지 않아 "불러오는 중"이 끝나지 않았다 → "토큰을 넣으면 … 불러와요". `docs/manual.md` 웹 사무실 절에 첫 스냅샷 전 표시를 적었다.
- 미해결: 3D 사무실(`lab3d`)은 첫 연결 전에 "roster를 기다립니다"를 이미 보여 고칠 것이 없다. Codex 리뷰는 사용자 지시로 보류 중이라 draft로 둔다.
- 근거: `labhq/web/index.html`(open() → ws.onmessage, renderHeader, board, decisions).

## 2026-10-09 · labhq down이 이어 묻기와 채용도 확인한다 (#500 첫 항목)

- 결론: `labhq down`은 진행 중 요청 목록만 봐서, 끝난 요청에서 돌고 있는 이어 묻기(follow-up)나 `labhq recruit` 채용 중에는 `--force` 없이 runner를 껐다(#500의 P1, 결과를 잃을 수 있는 유일한 항목). 이제 둘 다 있으면 거부하고 무엇이 돌고 있는지 한 줄씩 보인다.
- 바뀐 것: gateway `GET /api/shutdown-readiness`(client token 필요): 진행 중 요청, `followups[].status == running`인 이어 묻기, runner가 돌리는 채용 수, `ready`. 채용 수는 `/api/recruit`에서 올리고 `recruit.done`·`recruit.failed`나 새 runner 프로세스(incarnation 변경)에서 내린다. CLI `_down`이 요청 목록과 함께 이 API를 본다. API가 없는 이전 gateway면 지금처럼 요청 목록만 본다. `docs/manual.md` down 문장.
- 실행한 것: 새 test 2건(`tests/test_cli_launch.py` down 거부·force·이전 gateway, `tests/test_request_lifecycle.py` readiness·recruit.done)이 main 코드에서 실패하고 이 branch에서 통과. 전체 `pytest -q`(Windows) 4983 passed·63 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 봇 리뷰 P1 2·P2 1 고침: readiness 조회가 404 외 이유로 실패하면 거부(fail closed), 요청 목록을 읽은 뒤 들어온 요청도 readiness 응답으로 거부, 채용 수를 runner에 보내기 전에 올리고 보내기 실패 시 되돌림. 새 test 2.
- 미해결: #500의 나머지 두 항목(같은 instance에서 동시에 `up` 두 번, POSIX 종료 대기)은 그대로다. PI는 Windows 터미널 하나에서 `up`을 한 번 쓰므로 드물다. gateway를 다시 시작하면 채용 수를 잊는다(runner는 채용을 계속함). 이 branch는 문서 PR #513 위에 쌓았다(같은 manual 문장).
- 근거: `labhq/gateway/server.py` `shutdown_readiness`·`recruits_running`, `labhq/cli.py` `_shutdown_blockers`·`_down`.

## 2026-10-09 · CP1 수정 요청: 메모와 이전 계획으로 새 요청이 다시 계획한다

- 결론: CP1 카드에 **수정 요청**이 생겼다. 메모와 함께 고르면 지금 요청은 단계를 돌리지 않고 `plan_revision_requested`(완료)로 끝나고, 같은 요청 문장에 `[CP1 수정 요청 · <id>]` 메모를 붙인 새 연구 요청이 열린다. 새 요청의 계획자는 승인받지 못한 이전 계획과 메모를 받아 그 계획을 고치고, 새 CP1이 뜬다. 지금까지는 승인·거절뿐이라 계획 한 곳이 틀려도 거절하고 요청을 손으로 다시 보내야 했다(웹 연구 시운전 10-08, PI 점검 R10의 남은 항목).
- 바뀐 것:
  - `labhq/gateway/server.py`: `create_revised_request`(새 요청: 문장+메모, 요청 자체 참고 자료(query를 뗀 URL은 원문으로 다시 만들어 표시 유지), `default_references`, 확인 질문 답·PI 메모(`inherited` 표시, 새 요청의 질문 횟수·메모 상한에 세지 않음, 메모는 최근 20개), 처음 요청한 예산(`requested_budget_usd`, 새로 모든 요청에 기록), 프로젝트·CSO 모델·route·meta, `revision_of`(이전 계획·hash·메모)). 모두 새 요청의 첫 저장에 들어간다(`create_request(extra=)`). `revised_request_of`가 `meta.revised_from`으로 이미 연 새 요청을 찾아 재시작에도 두 번 열지 않는다.
  - `labhq/orchestrator/cso.py`: CP1 카드 `detail.revise_allowed`(단계가 돌기 전 첫 CP1에만: 이어 가기 차수·단계 뒤 재승인에는 없음). 메모 없는 수정 요청은 카드를 다시 묻고 세 번 비면 거절처럼 끝난다. 새 요청을 열 수 없으면(설정에서 빠진 프로젝트·CSO 모델·참고 경로) `plan_revision_failed`로 끝나고 보고서에 메모가 남는다. 새 요청의 연구 계획 prompt에 "CP1 revision request" 블록(이전 계획 digest·hash·메모)이 붙는다. 넘겨받은 확인 질문 답은 `MAX_CLARIFY_CARDS`에 세지 않는다.
  - 웹(2.5D·3D 공통 `labhq/web/ui/decide.js`, `index.html`): `revise_allowed`인 CP1 카드에 수정 요청 버튼, 메모 필수, 결과 안내 한 줄, 보낸 뒤 안내. 승인·거절도 그 카드에서는 choice를 함께 보낸다. CLI: `labhq approve <id> --choice revise --note "…"`(그런 CP1에만).
  - 문서: `docs/manual.md`(CP1 절), `docs/pi-qa.md`(CP1 계획이 마음에 안 들면), `docs/research_protocol.md`(CP1 행; 실제 화면에 없던 "CP2·3 위임 범위"는 뺐다).
- 실행한 것: 새 test 13건(`tests/test_research_cp1_revise.py`)과 웹 test 1건(`tests/web_cp2_evidence.cjs`)이 main 코드에서 실패하고 이 branch에서 통과. 전체 `pytest -q`(Windows, 봇 리뷰 수정·main 병합 뒤) 4996 passed·63 skipped. web node test 전체. 리뷰는 Codex 대신 Claude 워크플로로 했다: 관점 4개(재시작·넘겨받는 상태·orchestrator 흐름·웹/CLI) 리뷰와 관점별 반박 검증 → P1 1·P2 13(중복 포함 6갈래) 모두 고침 → 고친 부분 재검증(관점 2) → P2 2 고침. 봇 리뷰 P2 2(이전 계획 step 목록이 8,000자에서 잘림, 결정 저장 직후 멈추면 재시작에서 CP1 재질문) 고침, 새 test 2.
- 미해결: Codex 리뷰는 사용자 지시로 보류 중이라 draft로 둔다. 새 요청은 이전 요청의 브리핑을 다시 돌린다(비용 소액). 이어 가기 차수의 CP1에서 계획을 고치려면 지금처럼 거절 뒤 새 요청이다.
- 근거: `tests/test_research_cp1_revise.py`, `labhq/gateway/server.py` `create_revised_request`·`revised_request_of`, `labhq/orchestrator/cso.py` `finish_research_plan`.

## 2026-10-09 · PI 시험 전 문서 대조 21건과 CLI traceback

- 결론: PI가 읽는 문서(README, `docs/pi-qa.md`, `docs/manual.md`, `docs/research_protocol.md`, 예제 설정)를 현재 코드와 대조해 확인된 23건 중 21건을 고쳤다. PI 시험을 막는 항목은 없었다(혼동 5, 사소 18). 대조 중 찾은 코드 결함 하나(`labhq send` 대기·`watch`의 traceback)도 고쳤다.
- 바뀐 것:
  - `labhq/cli.py` `_run_websocket`: websocket을 먼저 여는 `send`(기다림)·`watch`가 gateway 꺼짐·연결 실패는 "gateway에 연결할 수 없습니다 … up", 토큰 거부(gateway가 1008로 닫음)는 client_token 안내, 중간 끊김은 "다시 watch"를 한 줄로 내고 exit 2.
  - 문서: `research.enabled`를 끄면 일반 lane, `evidence_checkpoint`만 끄면 CP1 뒤 `plan_approved`로 끝남(manual이 둘을 섞어 적었다). CP2는 단계 실행 뒤·리뷰 전. 7일 시간 초과 결과 이름을 카드별로. 개인 경로 기본 목록에 `~/.gemini`·`~/.env`, 직원 CODEX_HOME, AlphaGenome 키 파일. 작은 요청 단독 처리는 `orchestrator.solo_agent`를 켰을 때. Codex 직원은 Windows elevated sandbox에서 읽기 거부도 받음. `semantics: ab`. 실시간 출처 조회 시간은 `research.live_source_timeout_s`·`live_source_deadline_s`. 그 밖에 상태 목록(`PI 결정 대기`), 감사 번들 파일, doctor 준비 판정, `down` 거부 조건, 예제 설정 첫 줄의 복사 방법 등.
  - `labhq/settings.py` semantics 주석.
- 실행한 것: 새 test 2건(`tests/test_cli_launch.py`, gateway 꺼짐·토큰 거부 × `watch`·`send`)이 main 코드에서 실패하고 이 branch에서 통과. 전체 `pytest -q`(Windows) 4981 passed·63 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 문서 대조 23건 중 `docs/research_protocol.md` CP1 행의 "CP2·3 위임 범위"는 같은 행을 고치는 CP1 수정 요청 PR에서 고친다. 대조는 Claude 워크플로(영역 5 × 읽기·반박 검증)로 했다. Codex 리뷰는 사용자 지시로 보류 중이라 draft로 둔다.
- 근거: 대조 결과 목록(로컬), `labhq/orchestrator/cso.py`(`_research_after_steps`, 4421–4433), `labhq/doctor.py` 73–80, `labhq/private_paths.py` `DEFAULT_HOME_ENTRIES`, `labhq/adapters/codex.py` 235–255.

## 2026-10-08 · 이어 간 연구 요청의 보고서가 이전 차수에서 고친 것을 적는다

- 결론: 웹 연구 시운전(GSE10072) 2차 보고서가 1차 리뷰 P1 5건(stage IV 오분류, 경로 BH 범위, 가설 재정의, permutation 가정, 비유의=동등)을 한 번도 언급하지 않았다. PI가 2차 CP2 메모로 "1차 대비 고친 점을 한 표로"를 요구했지만, 보고서 prompt에는 이전 차수 CP2 메모만 있고 리뷰 지적은 없었다. 이제 보고서가 "이전 차수에서 고친 것" 절에 지적마다 어떻게 고쳤는지(claim anchor), 못 고쳤는지 적는다.
- 바뀐 것: `labhq/orchestrator/cso.py` `earlier_round_lines`(차수 archive의 리뷰 P1, CP2 수정 요청 메모), `EARLIER_ROUNDS_RULE`을 연구 보고서 prompt 끝에 붙이고, 같은 줄을 감사 부록(`report_appendix`)에 남긴다. 이어 가지 않은 요청은 그대로다. `docs/manual.md` 리뷰·이어 가기 절 한 줄.
  - #511 리뷰 P2: 2.5D 말풍선(`bubble`)과 3D 라벨(`lab3d/src/main.js` `stateLabel`)도 질문 답 대기를 "답 기다리는 중"·"답 대기"로 보인다.
- 실행한 것: `tests/test_research_continue.py::test_revise_continues_through_a_new_cp1_and_runs_only_the_changed_step`에 보고서 prompt·감사 부록 단언 추가(main 코드에서 실패, 이 branch에서 통과). 전체 `pytest -q`(Windows) 4977 passed·63 skipped, web·3D test(`tests/test_lab3d.py`, `tests/test_web_3d.py`, `tests/test_web.py`)와 node test 전체, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 이번 시운전 요청(`req_d77e574f85`)은 이미 끝나 보고서는 그대로다.
- 근거: trial `req_d77e574f85` 2차 보고서·감사 부록, `labhq verify` exit 0.

## 2026-10-08 · 연구 단계가 CP1 가설 원문을 받는다

- 결론: 연구 단계 prompt의 동결 요약(`frozen_context`)에 CP1 가설(주가설, null/대안, 구별할 관찰)이 없었다. 웹 연구 시운전 1차에서 s9가 A1·A2를 다시 정의해 리뷰 P1을 받았고, 2차에서는 "TASK.md에 가설 원문이 없다"며 PI에게 물으며 멈췄다. 이제 단계 prompt에 가설 원문이 들어가고, 가설은 그대로 쓰고 이름을 바꾸거나 합치지 말라는 지시가 붙는다.
- 바뀐 것:
  - `labhq/research/continuation.py` `frozen_context`: `hypotheses`(primary, null_or_alternatives, distinguishing_observations) 추가. 같은 요약이 이어 가기 재사용 기준이므로 가설이 바뀐 계획은 단계를 재사용하지 않는다(사유 "question, scope, hypotheses, protocol or pack values changed"). 재사용이 줄어드는 쪽으로만 바뀐다.
  - `labhq/orchestrator/cso.py`: 단계 prompt 머리말을 "Frozen question, hypotheses and protocol"로 바꾸고 가설 원문 사용 지시 한 문장.
  - 웹: 직원이 HPC job이 아니라 질문의 답을 기다리면 "답 기다리는 중"(하단 띠는 "답 기다림"). runner `agent.status`의 `jobs`·`asks`로 가른다. 페이지를 새로 열 때 `recent_events`에 계획 이벤트가 없어도 계획이 있는 진행 중 요청은 "실행"(리뷰가 accept면 "리뷰")부터 보인다. 시운전 2차 CP2에서 "브리핑"으로 보였다.
  - 문서: `docs/research_protocol.md` 동결 PLAN 계약, `docs/manual.md` 직원 상태 표.
- 실행한 것: 새 test가 main 코드에서 실패하고 이 branch에서 통과(`tests/test_research_cp2.py` 가설 원문, `tests/test_research_continue.py` 가설 변경 시 재사용 없음, `tests/web_cards_status.cjs` 답 대기 표시·새로 연 페이지 단계). 전체 `pytest -q`(Windows, 가설 커밋 기준) 4975 passed·63 skipped, web node test 전체, `tests/test_web.py`, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 이미 동결된 계획으로 도는 요청은 새 prompt를 다음 단계 dispatch부터 받는다(trial `req_d77e574f85` 2차는 PI 답으로 원문을 받았다).
- 근거: trial `req_d77e574f85` 1차 리뷰 P1(s9), 2차 직원 질문 카드 `appr_30672a3be3`.

## 2026-10-08 · 시운전 이어 가기에서 나온 카드 소음 세 가지

- 결론: 웹 연구 시운전 2차(이어 가기)에서 PI가 판단할 필요가 없거나 읽기 어려운 카드 세 종류를 고쳤다.
- 바뀐 것:
  - 예산 카드: 요약 끝에 "승인하면 상한을 $X로 올리고, 거절하면 새 작업을 보내지 않습니다."(`labhq/orchestrator/cso.py` `_check_budget`). 웹은 `*_usd` 값을 `$25.86`처럼, 키를 지금까지 쓴 비용·지금 상한·승인하면 새 상한·비용 미집계 작업·미집계 작업 1건당 가정 비용으로 보인다(`labhq/web/ui/decide.js`). detail 값 자체는 그대로다(bench가 읽음).
  - `__pycache__`: 직원 실행 환경에 `PYTHONDONTWRITEBYTECODE=1`을 넣는다(`labhq/adapters/base.py` `staff_env`). analyst가 자기 산출의 `__pycache__`를 `rm -rf`하려고 권한 카드를 띄웠다. 설정이나 runner 환경에 값이 있으면 그 값을 쓴다.
  - null 장치: `curl -o /dev/null`, `tee /dev/null`, `cp x NUL`처럼 명령 인자로 잡힌 null 장치를 쓰기 대상에서 뺀다(`labhq/policy.py` `_shell_write_targets`). 리다이렉트(`2>/dev/null`)는 전부터 쓰기가 아니었다.
  - `docs/manual.md`: 예산 카드 요약, `PYTHONDONTWRITEBYTECODE`, 이어 가기 비용(새 계획이 protocol을 바꾸면 재사용 없음, 1차가 상한 가까이 썼으면 바로 예산 카드).
- 실행한 것: 새 test가 main 코드에서 실패하고 이 branch에서 통과(`tests/test_cso.py` 예산 요약, `tests/test_claude_staff_home.py` 환경변수, `tests/test_policy.py` null 장치 3건, `tests/web_decision_detail.cjs` 예산 카드). 전체 `pytest -q`(Windows) 4977 passed·63 skipped, web node test 전체, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: `curl -so <경로>`처럼 붙여 쓴 짧은 옵션의 출력 경로는 원래도 쓰기 대상으로 잡히지 않는다(이 PR 범위 밖). 이어 가기 2차 시운전은 진행 중이다.
- 근거: trial `req_d77e574f85` 카드 `appr_785d1b55f2`(예산), `appr_1524b060de`(`__pycache__`), `appr_18572d98ea`(`curl -o /dev/null`).

## 2026-10-08 · 재개 카드가 멈춘 자리를 바르게 적는다

- 결론: 단계를 모두 마친 뒤(CP2·리뷰·이어 가기 카드·보고서 차례) 멈춘 요청의 재개 카드가 "남은 단계: 요청 전체" 대신 "없음(단계는 모두 끝났고 그 뒤 검토·결정·보고서부터 이어 갑니다)"으로 적힌다. 계획이 없는 요청은 지금처럼 "요청 전체"다.
- 바뀐 것: `labhq/gateway/server.py` `new_resume_approval`. 웹 `labhq/web/ui/decide.js`: 재개 카드 detail에서 요약과 겹치는 `request_text`를 숨기고, `created_at`은 접수 시각(YYYY-MM-DD HH:MM), `steps`는 남은 단계(쉼표 목록, 비면 "없음")로 보인다. `docs/manual.md` 재개 카드 문장.
- 실행한 것: 새 test 2건(`tests/test_request_lifecycle.py`, `tests/web_decision_detail.cjs`)이 main 코드에서 실패하고 이 branch에서 통과. `tests/test_request_lifecycle.py` 전체, web node test 전체, `scripts/check_public.sh`, `scripts/patch_notes.py check`. 실측: trial `req_d77e574f85`를 이어 가기 카드에서 재시작 → 재개 승인 → 같은 이어 가기 카드가 다시 뜨고 승인하면 2차로 넘어감.
- 미해결: 없음.
- 근거: trial `req_d77e574f85`(10-08 14:12 재시작), `tests/test_request_lifecycle.py::test_a_resume_card_after_every_step_finished_says_no_step_is_left`.

## 2026-10-08 · 이어 가기 카드가 리뷰 P1 지적을 읽을 수 있게 보인다

- 결론: 리뷰 revise 뒤 뜨는 이어 가기 카드(`research_continue`)가 P1 지적을 단계·문제·고칠 점 목록으로 펼쳐 보인다. 전에는 JSON 원문(시운전에서 3,492자)이 접힌 채로만 있었다.
- 바뀐 것: `labhq/web/ui/decide.js` `renderReviewIssues`. 지적 목록이 맨 위에 펼쳐지고, 원문 JSON은 그 아래 접혀 남는다. 내부 이름 `gate`는 숨기고 `round`·`limit`·`plan_sha256`은 이어 가기 차수·상한·지난 계획 plan hash로 보인다. `docs/manual.md` 리뷰·이어 가기 절 한 줄.
- 실행한 것: 새 node test 1건이 main 코드에서 실패하고 이 branch에서 통과(`tests/web_decision_detail.cjs`). web node test 전체, `tests/test_web.py`, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 카드 안 "승인하면 CSO가 새 계획을 쓰고 새 CP1을 받습니다" 문구는 그대로다.
- 근거: 웹 연구 시운전 trial `req_d77e574f85`(GSE10072) 리뷰 revise 카드, `tests/web_decision_detail.cjs`.

## 2026-10-08 · engineer 직원 모델을 gpt-5.6-sol로 (PI 결정 5A)

- 결론: 저장소 roster의 engineer는 `gpt-6.1-sol`이었고, v0.5를 통과한 trial roster는 `gpt-5.6-sol`이었다. 6.1-sol은 v0.5 시운전 3차의 환경 단계에서 마무리 응답 중 두 번 멈췄다(#473이 그 뒤 자동 재시도를 넣음). PI 결정 5A(#435)로 저장소도 통과한 구성에 맞춘다. 두 roster의 차이는 이 한 줄뿐이었다.
- 바뀐 것: `agents/core/engineer.yaml`, README(한·영) 배지(`scripts/integrations.py --write`로 생성), `docs/manual.md` 직원 표, 배지 문구를 단정하는 `tests/test_integrations.py` 기대값.
- 실행한 것: `tests/test_integrations.py`·`test_cost_accounting.py`·`test_doctor.py` 89 passed(가격표에 gpt-5.6-sol 있음), `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: PI 인스턴스는 병합 뒤 다시 띄워야 반영된다(agents_dir이 저장소 roster).
- 근거: `agents/core/engineer.yaml`.

## 2026-10-08 · CP2 수정 요청이 새 계획·새 CP1로 이어 간다 (PI 점검 R10)

- 결론: CP2에서 **수정 요청**을 고르면 요청이 끝나지 않는다. PI 메모가 수정 요청이 되어 CSO가 새 계획을 쓰고, PI가 새 CP1에서 승인하면 바뀐 단계와 그 아래 단계만 다시 돈다. 지금까지는 거부와 똑같이 `evidence_revision_requested`로 끝났고, #494에서 버튼을 `수정 요청(요청 끝남)`으로 바꿔 두기만 했다.
- 바뀐 것:
  - 흐름: CP2 revise는 리뷰 revise와 같은 이어 가기(`_research_continue`)로 들어간다. 이어 가기 카드는 따로 묻지 않는다. PI가 CP2에서 이미 수정을 골랐고 새 계획 승인은 새 CP1이 맡으므로, 같은 질문을 두 번 묻지 않으려는 것이다. `continuation.trigger: cp2_revise`와 `pi_request`(메모 전문)를 남긴다. 새 계획 prompt에는 리뷰 P1 지적 대신 "PI revision request (CP2)"로 메모가 한 번 들어가고, 그 차수의 리뷰 prompt도 이 요청이 해결됐는지 본다. 재사용 규칙(사양·질문·protocol·pack이 바이트까지 같고 산출 hash가 그대로)은 바뀌지 않았다. 새 CP1 요약은 "이어 가기 n차 계획(CP2 수정 요청 반영). 재사용: … · 다시 실행: …"으로 시작한다.
  - 메모 필수: 메모 없는 revise는 CP2 카드를 다시 띄워 메모를 묻는다(세 번까지, 끝내 없으면 새 계획 없이 끝남). 웹 2.5D·3D는 빈 메모로 보내지 않고 안내를 띄운다(`revisionNoteMissing`). `labhq approve <id> --choice revise`는 `--note`가 없으면 거절한다.
  - 상한: `research.revise_continuations`(기본 2)가 CP2 revise와 리뷰 revise를 합쳐 센다. 상한에 닿았으면 CP2 카드 요약·`detail.revise_continues: false`·버튼 `수정 요청(요청 끝남)`으로 알리고, 메모 없이도 지금처럼 끝난다.
  - 결과 이름: 새 CP1 거절·시간 초과, 계획 실패, 예산 카드 거절, 오류로 새 차수가 단계 실행 전에 끝나면 1차 계획·결과·CP2 receipt를 되돌리고 `evidence_revision_requested`로 끝난다(리뷰 revise에서 왔으면 지금처럼 `research_review_revise`).
  - 웹: 버튼은 `수정 요청`, 카드 한 줄 안내는 "수정 요청은 메모로 새 계획을 세워 새 CP1을 받고 바뀐 단계만 다시 돌립니다. 거부하면 요청이 끝납니다." 메모 칸 안내도 바꿨다.
  - #499 리뷰 P2: 차수 archive에 요청 집행 상한(`budget_usd`)을 남기고, 이어 가기를 되돌릴 때(`restore_round`) 함께 복원한다. 이전 archive에는 이 값이 없으므로 그때는 지금 상한을 그대로 둔다.
  - CP1 카드 요약은 "CP1 연구 계획 승인: 고정할 질문·방법·완료/중단 조건·데이터 범위·적용 pack을 확인하고 승인하세요."와 집행 상한 한 줄이다. plan hash는 `detail.target_sha256`에만 있다.
  - 문서: `docs/manual.md`(단계와 CP2, 리뷰·이어 가기, 메모, CLI), `docs/research_protocol.md`(머리말, CP2 행, 메모), `docs/pi-qa.md`(CP2에서 근거를 보강하게 하는 법), `config/labhq.example.yaml` 주석.
- 실행한 것: 새 회귀 14건(`tests/test_research_cp2_revise.py`) 중 13건이 main 코드에서 실패하고 이 branch에서 통과했다(메모 넣은 revise → 새 CP1·s1 재사용·s2만 재실행, 빈 메모 재질문·끝내 없음, 상한 합산 두 방향, 새 CP1 거절·시간 초과, 계획 작성 중·새 CP1 카드에서 재시작, 집행 상한 복원 두 경로, CP1 요약, CLI 거절). 나머지 1건은 상한에서 빈 메모를 다시 묻지 않는 보존 test다. 집행 상한 복원 test는 복원 두 줄만 빼도 실패하는 것을 확인했다. 웹 node test 5건이 main의 웹 코드에서 실패하고 통과했다(`tests/web_cp2_evidence.cjs`, `tests/web_cards_status.cjs`). 기존 CP2 test 4건은 revise가 끝나는 경로를 보던 것이라 `revise_continuations: 0`(상한)으로 그 경로를 그대로 지킨다. 연구·CLI test 502 passed, web node test 31파일 통과. 전체 pytest(Windows) 4868 passed·59 skipped·3 failed였다. 실패 3건은 패키지에 든 예제 설정 사본이 바뀐 주석과 달라서였고(`efbbcc9`에서 맞춤), 그 세 파일을 다시 돌려 83 passed. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: CP1에는 여전히 승인·거부뿐이다(R10의 `research-cp1-no-revise`). PI 메모에 단계 id를 적어도 그 단계를 강제로 다시 돌리지는 않는다. 다시 돌 단계는 새 계획이 바꾼 단계로 정하고, 새 CP1 카드에 재사용·재실행 목록이 보인다. 실제 CLI 실측은 아직 없다.
- 근거: `tests/test_research_cp2_revise.py`, `labhq/orchestrator/cso.py`(`_research_after_steps`, `_research_continue`, `_unstarted_continuation_end`), `labhq/research/continuation.py`(`plan_prompt`, `review_prompt`, `archive_round`, `restore_round`), `labhq/web/ui/decide.js`(`revisionNoteMissing`), `labhq/cli.py`.

## 2026-10-08 · 연구 카드 메모가 단계·리뷰·보고서·새 계획에 가고, 계획 예산이 집행 상한이 된다 (PI 점검 R11 R17)

- 결론: CP1·CP2·이어 가기 카드 메모가 다음 일을 하는 prompt에 들어간다. 계획의 숫자 `budget_usd`는 CP1 승인 때 요청의 집행 상한이 되어, 넘으면 예산 카드가 뜬다. 시운전 요청 `req_7ccde78be0`에서는 CP2 메모 6항목이 task prompt 24개 어디에도 없었고, "USD 5 이내" 계획이 카드 없이 $18.28을 썼다.
- 바뀐 것: CP1 승인 메모는 그 계획의 모든 단계 prompt에 "PI 승인 메모 (CP1)"로, CP2 승인 메모는 리뷰와 보고서 prompt에(이어 간 요청은 앞 차수 CP2 메모도 round 표시와 함께), 이어 가기 메모와 직전 CP2 메모는 새 계획 prompt에 들어간다. prompt에는 2,000자까지 넣고 잘렸다고 표시한다. CP2는 여전히 선택값만 읽고, 감사 부록의 `PI note:`는 그대로다. 재사용된 단계는 다시 돌지 않으므로 새 CP1 메모를 받지 않는다. research PLAN에 선택 field `budget_usd`(양수, 0·음수는 없는 것으로 봄, true/false는 검증 오류)를 더했다. CP1 승인 때 min(`budget_usd`, 설정 상한)을 요청 `budget_usd`로 쓰고 CP1 receipt의 `budget`에 남긴다. 설정 상한은 요청 예산, 없으면 `policy.budget.per_request_usd`이며 처음 적용할 때 `research_contract.budget_cap`에 고정해, 이어 간 차수의 계획도 같은 설정 상한을 받는다. 이미 승인된 CP1은 재개 때 다시 적용하지 않아 예산 카드로 올린 상한을 지킨다. CP1 카드 요약 끝에 "집행 상한 $N(…)" 한 줄과 `detail.budget`(`plan_budget_usd`·`cap_usd`·`source`·`configured_cap_usd`)을 더했다. `budget_usd`가 없는 계획은 canonical JSON·plan hash·상한이 전과 같다.
- 리뷰 반영(`3f6d449`): P1 — CP1 승인 직후, 단계 없이 끝나는 분기(`plan_only`·`evidence_checkpoint` 꺼짐)보다 먼저 지금까지 쓴 비용을 새 상한으로 판정한다. 넘었으면 예산 카드가 뜨고, 거절되면 `research_failed`로 끝난다. P2 — `budget_usd: true/false`는 검증 오류로 계획 교정을 받는다. 앞 차수 CP2 메모는 감사 부록에 `PI note (round N CP2)`로 전문이 남는다. 단계·리뷰 prompt에 `protocol.resource_limits`와 별도로 실제 집행 상한 한 줄을 넣는다.
- 실행한 것: 새 회귀 18건(`tests/test_research_notes_budget.py`) 중 10건이 main 코드에서, 리뷰 반영 5건이 그 앞 head에서 실패하고 이 branch에서 통과했다. 나머지 3건은 메모 없는 계획·메모가 결정하지 않음·옛 계획 재개를 지키는 보존 test다. 엔진용 research PLAN schema와 plan prompt의 고정 hash만 바꿨다(`tests/test_output_types_research.py`). 전체 pytest(Windows) 4771 passed·59 skipped, 리뷰 반영 뒤 4826 passed·59 skipped·1 failed(`test_bench.py::test_baseline_timeout_and_cancel_kill_cli_tree` fake CLI 기동 flake, 따로 3번 돌려 모두 통과). #494·#496~#498·#503·#504 위로 rebase한 뒤 연구·CSO·clarify 관련 727 passed(test_web·test_login_wait 포함). CI pytest-windows가 한 번 `test_login_wait` 시간 flake(이 변경과 무관)로 실패했고 재실행에서 통과했다. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 웹 CP1 카드는 `detail.budget`을 이름표 없이 `budget` 키 그대로 보인다(#494 뒤의 일반 detail 표시). 한국어 이름표는 웹 쪽에서 붙인다. 상한은 카드 요약 끝줄에도 있다. 집계 못 한 task가 있으면 기존 규칙대로 건당 `per_task_usd`로 보므로, 작은 `budget_usd`에서는 예산 카드가 일찍 뜰 수 있다.
- 근거: `tests/test_research_notes_budget.py`, `labhq/orchestrator/cso.py`(`approval_note_block`, `_research_budget`), `labhq/research/contract.py`(`ResearchPlan.budget_usd`).

## 2026-10-08 · 동적 실행 파일을 인자와 무관하게 차단 (#472)

- 결론: 따옴표 없는 변수·명령 치환·배열이 실행 파일 자리에 오면 뒤 인자가 리터럴이어도 설치 가능 명령으로 거부한다. cmd 확장과 PowerShell call operator·`Invoke-Expression`도 같은 원칙을 쓴다. `"$PY" script.py`처럼 따옴표로 감싼 단일 변수와 리터럴 분석 인자는 계속 허용한다.
- 바뀐 것: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.
- 실행한 것: 새 회귀는 수정 전 12건 중 10건 실패(명령 치환 2건은 기존 차단), 수정 뒤 환경 설치 시험 344 passed·1 skipped. Windows 전체 pytest 첫 실행은 장시간 MCP mock 1건만 비결정적으로 실패했고 개별 재실행 1 passed, 최종 전체 실행 4865 passed·59 skipped. `scripts/check_public.sh`와 `scripts/patch_notes.py check` 통과.
- 미해결: 없음.
- 근거: `tests/test_environment_install.py::test_unquoted_dynamic_executables_fail_closed_with_literal_arguments`, `::test_dynamic_install_guards_keep_analysis_commands_allowed`.

# 웹 token 오류와 연구 문서 어긋남 수정 (PI 점검 R23 R26)

- 결론: PR #504에서 잘못된 token은 브라우저에 1008로 전달되고, 두 웹 화면은 재시도를 멈춘 뒤 token 입력창을 보인다. 연구 문서와 artifact 거부 사유도 현재 동작과 맞췄다.
- 바뀐 것: `/ws/client` 인증 close 순서, 2.5D·3D 공통 close 판정, CSO 우선 blocking 답변, 연구 verifier·CP2·Codex auto 문구, 연구 lane 다섯 소제목.
- 실행한 것: 실제 uvicorn 1008 회귀, 관련 pytest 374 passed·1 skipped, Windows 전체 pytest 4790 passed·59 skipped, `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_web_3d.py`, `tests/web_state.cjs`, `tests/test_cso.py`, `tests/test_research_cp2.py`, `docs/manual.md`, `docs/research_protocol.md`.

## 2026-10-08 · 웹·CLI에서 작업 종류(자동·간단·연구)를 PI가 고름 (PI 점검 R16)

- 결론: 연구 lane을 켜면 CP1·CP2를 거칠지는 접수 규칙만으로 정해졌다. 웹 시운전에서 메타데이터 표 요청이 CP1과 5단계 연구 계획으로 간 일이 있었다(규칙은 #498에서 고침). 이제 PI가 웹 입력창 옆 **작업 종류**(자동 판단·간단한 일·연구)나 `labhq send --work-kind simple|research`로 직접 고른다. 연구를 직원 한 명에게 직접 보내는 조합은 웹·CLI 모두 보내기 전에 막고 안내한다. 메모 모드에서는 선택이 숨는다.
- 바뀐 것: `labhq/web/index.html`(선택·전송·메모 모드), `labhq/cli.py`(`send --work-kind`), `docs/manual.md`(웹 설명과 CLI 예), 시험 `tests/web_work_kind.cjs`·`tests/test_intake_references.py` 1건.
- 실행한 것: 웹·CLI 관련 시험 163 passed, 전체 pytest(Windows) 4793 passed·59 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 3D 사무실에는 따로 입력창이 없어 해당 없음.
- 근거: `tests/web_work_kind.cjs`, `tests/test_intake_references.py::test_cli_send_passes_the_work_kind_and_refuses_research_to_one_agent`.

## 2026-10-08 · 요청·승인 수명 정리: 결정 카드 기한, 요청 취소, 러너 대기, 떠난 카드 (PI 점검 R2 R5 R13 R15)

- 결론: PI가 혼자 쓸 때 요청이 조용히 죽던 네 경로를 막았다. 결정 카드(확인 질문·직원 질문·범위·예산·CP1·CP2·이어 가기·재개)는 1시간이 아니라 `policy.approvals.pi_decision_timeout_s`(기본 7일) 동안 기다리고, 그동안 요청은 `waiting_pi`다. 기한을 넘기면 `clarify_timed_out`·`plan_timed_out`·`evidence_timed_out` 같은 시간 초과로 끝나 거절과 구분된다(R5). 요청 취소를 웹 버튼·`labhq cancel`·`POST /api/requests/{id}/cancel`로 넣었다(R13). 러너가 없을 때 새 요청은 `waiting_for_runner`로 기다렸다가 러너가 붙으면 진행하고, 웹 상단에 **러너 꺼짐** 띠가 뜬다(R15). 러너가 다시 시작되거나 직원이 다른 러너로 옮기면 이전 프로세스의 도구 권한 카드는 러너 등록 때 닫히고, 재개 카드에는 요청 문장·접수 시각·남은 단계가 적힌다(R2).
- 바뀐 것: `labhq/gateway/server.py`(결정 카드 기한·`waiting_pi`·재개 카드 timer, `cancel_request`·`close_approval`, `wait_for_runner`, `expire_orphan_approvals`, `request.status` event, snapshot `quota_deadline_at`, `/api/health` `runner_online`), `labhq/orchestrator/cso.py`(종류별 `*_timed_out`, 취소 때 단계 task 정리, CP1 거절 문구 한국어), `labhq/research/contract.py`, `labhq/settings.py`(`pi_decision_timeout_s`, `gateway.runner_wait_s`), `labhq/request_status.py`, `labhq/cli.py`(`cancel`, 상태 라벨, 러너 안내를 `labhq up`으로), `labhq/web/state.js`·`index.html`·`ui/decide.js`(2시간 넘는 대기는 시간·일), 예시 설정 두 벌, README·README.en §3·§4, `docs/manual.md`, `docs/pi-qa.md`.
- 실행한 것: 전체 pytest(Windows) 4819 passed·59 skipped. 새 시험 `tests/test_request_lifecycle.py` 21건(종류별 park·기한·거절 구분, 취소·멱등·늦은 카드·비용, 러너 대기·한국어 실패, 떠난 카드, 상태 event), `tests/web_request_lifecycle.cjs`, CP1·CP2 timed_out 시험. 임시 gateway(127.0.0.1:18799, 별도 state)를 러너 없이 띄워 웹에서 러너 꺼짐 띠 → 요청이 러너 기다림 → 요청 취소 → 취소됨·보고서를 확인했다. Codex 로컬 리뷰 P1 1건(러너 연결 전 빈 roster로 계획 검증)·P2 4건(병렬 단계 취소, 취소 뒤 늦은 카드, 취소 turn 비용, 러너 대기 해제 event)을 모두 고쳤다. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: gateway 재시작 때 열린 결정 카드는 여전히 닫히고 재개 카드로 돌아온다(재개 승인 뒤 러너 대기는 `resume_wait_s` 300초 그대로). 취소는 HPC에 이미 낸 job을 멈추지 않는다.
- 근거: `tests/test_request_lifecycle.py`, `tests/web_request_lifecycle.cjs`, `tests/test_research_protocol.py::test_an_unanswered_cp1_card_is_not_a_rejected_plan`, `tests/test_research_cp2.py::test_cp2_decision_is_read_from_the_choice_alone`.
- 후속(리뷰·main 병합): main(#493 #494 #496 #497 #498 #503)을 병합하고 manual 충돌을 양쪽 내용으로 풀었다. 봇 P2 두 건을 고쳤다: direct 요청은 저장된 roster나 `runner.agents_dir`에 있는 직원이면 받아서 러너를 기다리고, 재개 카드도 웹에 남은 시간을 보인다(예전 1시간 카드는 시작 때 7일로 맞춤). 봇 P1(패치노트 없음)은 기록 커밋 db5d85b로 해결됐다.

## 2026-10-08 · 경고와 카드 소음 축소 (PI 점검 R20)

- 결론: `outputs/scripts/`·`outputs/reference/`·`outputs/env/`의 미선언 파일을 `unreported_outputs`와 요청 묶음에 보존하고, 사람이 보는 경고에서만 숨겼다.
- 바뀐 것: runner 로그·CP2 카드·`labhq verify` 경고가 공통 필터를 쓴다. `__pycache__`는 전처럼 목록에서도 제외한다.
- 실행한 것: 수정 전 회귀 1 failed, 수정 뒤 관련 pytest 191 passed·5 skipped. Windows 전체 pytest 4826 passed·60 skipped, `scripts/check_public.sh`·패치노트·목차 검사와 `git diff --check` 통과.
- 미해결: push 뒤 CI 확인.
- 근거: `labhq/artifact_policy.py`, `labhq/runner/daemon.py`, `labhq/evidence/audit.py`, `labhq/orchestrator/cso.py`, `tests/test_observed_outputs.py`, `tests/test_labhq_verify.py`, `tests/test_research_cp2.py`, `docs/manual.md`.

## 2026-10-08 · 메타데이터 표·목록 정리는 단순 작업으로, 직원 직접 지정은 연구 lane 밖으로 (PI 점검 R16)

- 결론: 연구 lane을 켠 trial에서 웹으로 "GEO GSE10072 시료 메타데이터를 표로 정리해 줘"를 보내자, 규칙 분류가 "단순 작업으로 확실히 한정되지 않음"으로 research에 넣어 CP1과 5단계 연구 계획이 떴다. PI 설정에도 연구 lane을 켰으므로 내일 PI가 같은 일을 겪는다. 이제 출처에 있는 것을 표·목록으로 정리하거나(메타데이터·시료 정보) 추출·다운로드하는 요청은 simple이다. 분석(DE·경로·생존 등)이 들어가면 표를 함께 요청해도 research로 둔다. 직원 한 명을 직접 지정한 요청은 PI가 research를 명시하지 않는 한 simple이고, 명시했을 때의 안내는 한국어로 바꿨다.
- 바뀐 것: `labhq/research/contract.py`(`_SIMPLE_SIGNALS`·`_RESEARCH_SIGNALS`, `classify_intake(mode=)`), `labhq/orchestrator/cso.py`(mode 전달, direct+research 문구), `docs/research_protocol.md` 접수 표, `tests/test_research_protocol.py` 7건.
- 실행한 것: 연구 시험 415 passed, 전체 pytest(Windows) 4769 passed·59 skipped, main(#496) 위로 rebase 뒤 연구·cso 시험 재실행 통과. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 웹·CLI에서 PI가 연구/간단을 직접 고르는 선택지는 웹 카드 PR(#494)과 기동 PR(#493) 병합 뒤 따로 넣는다.
- 근거: `tests/test_research_protocol.py::test_collecting_what_a_source_says_is_simple_but_any_analysis_is_research`.

## 2026-10-08 · 연구 요청 묶음에 claim 원장(claims.json)을 넣음 (PI 점검 R24)

- 결론: 보고서의 `[[claim:<단계>/<claim>]]` 앵커가 가리키는 claim·근거 원장은 감사 번들(`labhq verify --bundle`)에만 있었다. 그래서 요청 묶음만 받은 동료는 수치를 근거까지 따라갈 수 없었다. 이제 연구 요청의 묶음에 감사 번들과 같은 claim 기록(단계 원장, CP2 기록, 앵커 검사)을 `claims.json`으로 넣는다. MANIFEST에는 generated로 오르고 RO-Crate File entity가 생기며, 묶음 README가 앵커와 원장 id의 관계를 한 줄로 설명한다. 일반 요청 묶음에는 넣지 않는다.
- 바뀐 것: `labhq/evidence/audit.py`(`claims_record`를 빼서 감사 번들과 같이 씀), `labhq/request_bundle.py`, `docs/manual.md` 요청 묶음 표 한 줄, `tests/test_request_bundle.py` 2건.
- 실행한 것: 새 시험 2건(기존 코드에는 claims.json을 쓰는 경로가 없음), 묶음·verify 시험 108 passed, 전체 pytest(Windows) 4760 passed·59 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 웹에서 보고서 앵커를 각주 링크로 바꾸는 일은 웹 카드 PR(#494) 뒤로 미룬다.
- 근거: `tests/test_request_bundle.py::test_a_research_bundle_carries_the_claims_its_report_anchors_name`.

## 2026-10-08 · clarify 재질문과 실패 보고 원인을 보강 (PI 점검 R6 R12)

- 결론: 답한 질문은 정규화한 본문이나 stable id가 같으면 다시 묻지 않는다. 새 질문만 두 번째 카드로 보내며, 요청당 카드 2장을 넘으면 남은 질문을 한국어 보고서에 적고 멈춘다.
- 바뀐 것: 일반·연구 계획과 실패 재계획이 같은 질문 필터를 쓴다. 단계 실패 보고서 첫머리는 단계·담당·가린 원인·PI 조치를 세 줄로 보여 주고, 서버가 웹 feed용 실패 이유도 보낸다.
- 실행한 것: 관련 pytest 286 passed, 보존 확인 14 passed. Windows 전체 pytest 4752 passed·59 skipped. `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_intake_questions.py`, `tests/test_cso.py`, `tests/test_research_cp2.py`.

## 2026-10-08 · #493 — PI 단독 기동과 CLI 복구 경로

- 결론: 새 PowerShell·cmd에서 `.\labhq -c config\labhq.yaml up`으로 안전하게 기동하고 `open`할 수 있다. `down`은 프로세스 생성 시각과 active 작업을 확인하며, 강제 종료는 `--force`를 명시해야 한다.
- 바뀐 것: PID 숫자 파일을 역할·생성 시각·설정 경로가 든 JSON 기록으로 교체했다. 살아 있는 runner 중복 기동 방지, 인증된 상태 확인, runner 트리 종료, wrapper 이름 안내를 더했다.
- 실행한 것: 새 회귀가 수정 전 7건 실패, 수정 뒤 21건 통과. Windows 전체 pytest 4763 passed·59 skipped, 자체 throwaway process 생성 시각 확인, `scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: repo root의 `labhq`가 Python package 디렉터리라 POSIX wrapper는 `labhq.sh`다.
- 근거: `tests/test_cli_launch.py`, `labhq/cli.py`, `labhq/adapters/base.py`, `labhq/doctor.py`, `README.md`, `README.en.md`, `docs/manual.md`.

## 2026-10-08 · #494 — 웹 결정 카드와 요청 상태 정리 (PI 점검 R10 R14 R18 R19 R21)

- 결론: PI가 내일 헷갈릴 웹 화면 다섯 가지를 고쳤다. gateway 재시작 뒤 요청이 '실패'로 보이지 않고, 재개를 승인하면 새로고침 없이 상태가 바뀐다. CP2 카드는 claim 표로 읽히고, CP2 '수정 요청'이 요청을 끝낸다는 것이 버튼과 카드에 보인다.
- 바뀐 것:

  | 항목 | 화면 |
  |---|---|
  | R14 | 요청 상태 라벨 10개(중단됨·러너 기다림·취소됨·거부됨 포함, 모르는 상태는 그대로 표시). 재개 카드 승인과 `request.resume_waiting`·`resumed`·`resume_timeout`을 reducer가 반영한다: 러너 기다림(빠진 직원) → 진행 중, 초과면 중단됨. 새 재개 카드가 뜨면 그 요청을 중단됨으로 표시한다. 2.5D·3D 공통 |
  | R18 | CP2: claim별 표(단계·claim·상태·근거 종류·표시), 원장 JSON·산출 hash는 접는다. CP2 summary 한국어(`cso.py` 그 한 줄). CP1: raw key 중복 제거, 단계 목록(id·직원·지시 앞부분·산출) 펼침, protocol 접음, 가설은 JSON 대신 글 |
  | R10 | CP2 버튼 `수정 요청(요청 끝남)`, 카드 안내 한 줄. manual·research_protocol의 revise 경로 문구 정정 |
  | R19 | KIND_KO에 resume·question. 재개 카드 step id join, countdown 없음. 거절 결과 한 줄(재개·CP1·CP2·직원 질문). 직원 질문 카드는 선택지를 버튼으로 그리고(하나뿐이어도) 선택을 요구한다. 결정 카드 첫 줄에 요청 문장(3D에도 requests 전달) |
  | R21 | alert 로그에 toast. 한도 대기 카드에 `MM-DD HH:MM` 재개와 최대 기한. manual에 `quota_default_wait_s`·`quota_max_wait_s`와 `지금 재개` 범위 |

- 실행한 것: trial `req_7ccde78be0`의 CP1·CP2 카드를 gateway DB에서 read-only로 꺼내 줄인 fixture로 CP2 22행·CP1 단계 12개를 확인했다. 새 node 시험 12건(`tests/web_cards_status.cjs`), CP2 summary 단언 1건. 전체 pytest(Windows, 1d8c778) 4739 passed·59 skipped. 리뷰 반영(61e4e54): 선택지가 하나인 직원 질문도 버튼과 선택 강제. 회귀 시험 1건은 고치기 전 코드에서 실패했고, 그 뒤 node 시험 전부와 `tests/test_web*`·`tests/test_lab3d.py` 73 passed·1 skipped. 375px 정적 미리보기에서 가로 넘침 없음. Codex 로컬 리뷰 지적 0건. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결(서버 쪽이 필요한 것):
  - gateway가 재시작 때 요청을 interrupted로 바꿔도 status event가 없다. 열린 페이지는 새 재개 카드가 뜰 때만 알고, 이전 재개 카드가 남아 있던 요청은 새로고침해야 안다. status event나 boot id가 필요하다(`gateway/server.py`).
  - snapshot `step_details`에 quota `deadline_at`이 없어 새로고침 뒤에는 최대 기한이 빠진다(`request_step_details`).
  - UAC 대기 전용 event(시작·해제)가 없어 `#engine-holds` 행은 만들지 않았다. alert toast로 대신한다(`adapters/base.py`).
  - 재개 summary의 Python list repr(`server.py` `new_resume_approval`), CP1 summary·거절 문구 English(`cso.py`)는 웹에서만 다듬었다.
  - CP2 수정 요청 → 이어 가기 흐름, `labhq send/watch`의 quota 표시는 범위 밖이다.
- 근거: `tests/web_cards_status.cjs`, `tests/fixtures/research_cards_req_7ccde78be0.json`, `tests/test_research_cp2.py::test_cp2_refuses_evidence_whose_artifact_was_not_collected`.

## 2026-10-08 · 연구 lane이 끝까지 도는지 doctor가 알리고, research 오타 키를 오류로 (PI 점검 R1 R25)

- 결론: PI 시운전 전 점검(R1)에서 PI 설정에 `research` 블록이 없어 v0.5 연구 lane이 돌지 않는 것을 찾았다. 문서는 `enabled: true` 한 줄로 켠다고 했지만, 그러면 CP1 승인 뒤 단계를 실행하지 않고 끝난다. `labhq doctor`에 `research lane` 행(꺼짐 / CP1에서 멈춤 warn / 끝까지 감)을 넣고, `research` 아래 오타 키는 시작할 때 오류로 한다. `docs/research_protocol.md`와 예시 설정은 `enabled`·`evidence_checkpoint`·`active_packs` 세 키 형태로 고쳤다. R25: `docs/pi-qa.md`의 옛 창구(#298 → #435), #402 상태(병합됨), Codex 직원 보호 설명을 매뉴얼과 맞추고, 폰 접속 보류를 적었다.
- 바뀐 것: `labhq/doctor.py`(`_research_row`), `labhq/settings.py`(`ResearchSettings` extra forbid), `docs/research_protocol.md`, `config/labhq.example.yaml`과 패키지 사본, `docs/manual.md` doctor 절 한 줄, `docs/pi-qa.md`, `tests/test_doctor.py` 2건(4 case).
- 실행한 것: 관련 시험 99 passed. 전체 pytest(Windows)는 그림자 hook 표시 시험 1건만 실패(doctor가 semantics를 읽은 줄) → 그 부분을 빼고 그 파일 포함 관련 시험 재실행 통과. 나머지 4742 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: PI 설정에 실제로 켜는 일은 PI 결정 3(#435)을 따른다.
- 근거: `tests/test_doctor.py::test_doctor_says_whether_the_research_lane_runs_end_to_end`, `::test_a_misspelled_research_key_is_an_error_not_a_silent_default`.

## 2026-10-08 · #495 — 웹 사무실이 붙을 때 gateway가 11초 멈추던 문제

- 결론: 웹 사무실이 `/ws/client`에 붙으면 `hub.snapshot()`이 요청마다 task 원장 전체를 두 번 JSON으로 풀었다(`request_summary`→`running_tasks`, `request_step_details`). trial state는 요청 40건, task 472행 18.5 MB라 연결 한 번에 80번을 푼다. 그동안 event loop가 11.4초 멈췄고 `/api/health`도 응답하지 않았다(07:47 trial에서 본 증상). CPU를 쓰는 멈춤이다. 재현에서 11.4초 동안 CPU를 12.9초 썼다. 다른 프로세스가 같은 sqlite를 읽는 것은 원인이 아니었다. 읽기 프로세스 2개를 붙여도 11.3초로 같았다. 이제 snapshot과 `/api/requests`는 원장을 한 번만 읽어 넘긴다. `running_tasks`는 진행 중 요청이 없으면 원장을 읽지 않는다. gateway 시작 때 라운드 기록 복구도 같은 모양이었다(요청마다 원장·이벤트·결정 전체, 시작이 7.2초 늦음). 이것도 한 번 읽어 나눠 쓴다.
- 바뀐 것: `labhq/gateway/server.py`(`running_tasks(tasks)`, `request_summary(req, running)`, `request_step_details(..., ledger)`, `snapshot`, `/api/requests`는 고른 요청만 요약), `labhq/integrations/rounds.py`(`record_sources`, `build_record(..., sources)`, `recover`), 시험 `tests/test_snapshot_ledger_reads.py` 4건, `tests/web_issue87.cjs`의 정적 검사를 새 코드 모양에 맞춤.
- 실행한 것: trial state 복사본에 gateway를 띄우고 event loop가 1초 넘게 늦으면 스택을 남기는 감시 thread를 붙였다. 브라우저 순서(GET /, `/ws/client`, `/api/approvals/history`, 0.5초 간격 `/api/health`)를 재현했다. 고치기 전에는 ws 첫 frame이 11.4초, health timeout(4초) 2회였다. 스택은 `ws_client > snapshot > request_step_details·running_tasks > store.all`이다. 고친 뒤에는 ws 첫 frame 0.24초(동시 2개 0.42초), health 2 ms이고 1초 넘는 멈춤이 없다. 같은 복사본에서 snapshot·요청 요약·라운드 기록이 고치기 전과 JSON으로 같다(snapshot 10.07초 → 0.12초). 새 시험 4건은 고치기 전 코드에서 원장을 61·30·1·29번 읽어 실패했고 고친 뒤 통과한다. 전체 pytest(Windows) 4742 passed·59 skipped(main ec50530으로 rebase 전 head, rebase 뒤 관련 시험 280 passed). `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 진행 중 요청이 있으면 `/api/health`는 호출마다 원장을 한 번 푼다(이 state에서 약 0.1초). `ws_client`는 snapshot을 보내는 동안 `event_lock`을 쥔다. 느린 client가 이벤트 발행을 늦출 수 있지만 loop를 막지는 않는다.
- 근거: `tests/test_snapshot_ledger_reads.py`.

## 2026-10-08 · #491 — sed·curl·wget 출력 경로 해석 보강 (#482)

- 결론: sed 옵션 순서와 script 출처에 상관없이 `-i` 입력 파일을 쓰기 대상으로 잡고, curl·wget 출력 인수의 붙은 따옴표를 해석한다.
- 바뀐 것: `labhq/policy.py`의 sed 2단계 인수 분류와 download literal 해석, `docs/manual.md` 승인 게이트 한 줄, `tests/test_shell_write_quotes.py` 회귀 시험.
- 실행한 것: 관련 policy test 263 passed. Windows 전체 pytest 4747 passed·59 skipped. 첫 전체 실행의 무관한 임시파일 잠금 2건은 개별 재실행과 두 번째 전체 실행에서 통과. `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_shell_write_quotes.py::test_commands_that_execute_text_or_name_outputs_keep_their_write_targets`, `::test_text_that_only_looks_like_a_redirect_is_not_a_write`.

## 2026-10-08 · #490 — labhq verify가 조상 산출물 hash를 다시 확인 (#484)

- 결론: live 출처 검증을 켠 `labhq verify`가 각 단계의 재해시 결과에 계획상 모든 조상 단계의 산출물을 `<workdir_id>/<path>`로 더한다. 비조상 단계와 실패한 단계는 제외한다. 원격 runner나 크기 상한으로 재해시하지 못한 파일의 기존 사유와 exit 판정은 유지한다.
- 바뀐 것: `labhq/evidence/audit.py`, `tests/test_live_source_verify.py`, `tests/test_labhq_verify.py`, `docs/manual.md`.
- 실행한 것: 관련 pytest 36 passed·2 skipped, 전체 pytest(Windows) 4740 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `tests/test_labhq_verify.py::test_live_verify_resolves_a_grandparent_artifact`, `::test_live_verify_does_not_resolve_an_unrelated_step_artifact`, `::test_upstream_artifact_mapping_stays_off_with_live_check_disabled`.

# labhq v0.5: PI가 자기 공개 데이터로 로컬에서 연구하는 판

**통과 기준:** 연구 lane 요청 2건이 CP2 승인 → 리뷰 → 보고서까지 완주하고, `labhq verify`가 exit 0.

**근거:** 실제 CLI(Claude·Codex 직원)로 trial 인스턴스에서 돌린 연구 요청 2건입니다. PI 결정 1A·2A(2026-10-08, #435)로 이 근거에 선언합니다. 이어 가기 실측과 PI 본인 시운전 결과는 #435에 이어 붙입니다.

| 요청 | 데이터 | 결과 | verify | 비용(Claude 기록) |
|---|---|---|---|---|
| 모의 시운전 10차 | GSE10072 | CP1 → 12단계 → CP2 → 리뷰 accept → 보고서(앵커 58, 문제 0) | exit 0 | — |
| v0.5 시운전 8차 req_7ccde78be0 | GSE19804 60쌍 + 검증 GSE32863 | CP1 → 12단계 → CP2(claim 22·근거 31·거부 1) → 리뷰 accept(P2 4·P3 1) → 보고서(앵커 48, 문제 0) | exit 0, 산출 67개 일치, RO-Crate·MANIFEST 대조 통과 | $18.3 |

두 번째 요청은 8차에 완주했습니다. 1~7차는 실제 요청에서만 드러난 labhq 결함으로 멈췄고, 매번 고쳐 병합했습니다(#467 #470 #474 #476 #478 #480 #483 #486). 8차에서 드러난 근거 인용·묶음 검사 구멍도 고쳤습니다(#487 #488).

**아직 못 잰 것:** 리뷰 revise 뒤 새 CP1로 이어 가기(#448). 코드·test는 있지만, 두 요청 모두 리뷰가 accept여서 실제 CLI로는 돌지 않았습니다.

## 할 수 있는 것 (v0.25에 더해)
- **연구 lane 완주:** 계획 동결(CP1) → claim·근거 원장 → PI 근거 승인(CP2) → 다른 회사 모델 리뷰 → 수치마다 근거 앵커가 달린 보고서 → `labhq verify` 재검사.
- **요청 묶음:** 산출·스크립트·환경 기록·입력 hash를 한 폴더로 모으고 RO-Crate(Process Run Crate)와 MANIFEST로 기술합니다. 재현 등급(replayable/documented)을 매깁니다.
- **분석 기준:** topic 43개, 점검표, topic당 근거 논문 30편 이상(1286편), 선행 연구의 필수·권장 분석, 원 연구 Methods 참고.
- **환경:** 계획 첫 환경 단계만 공유 환경에 설치하고 뒤 단계 설치는 자기 폴더로 격리합니다. 환경 실패는 서명으로 분류해 승인 뒤 고쳐 다시 돕니다.
- **작은 요청:** 직원 한 명이 단독 처리합니다(CSO 자동 라우팅, 실패하면 팀으로).
- **조회 도구:** 변이(VEP·gnomAD·ClinVar)·조절 영역(ChIP-Atlas·ENCODE cCRE·GTEx)·AlphaGenome 예측, 판본 기록.

## 견고함
- 무응답 직원 CLI 재시도, 로그인 만료는 실패 대신 대기, 재시작 뒤 재개, Windows 명령줄·잠금·실행 별칭 대응.
- 승인 게이트: 규칙 없는 도구를 기본 허용하지 않음, 설치 명령 파서 fail closed, 오탐 감소.

## 알려진 한계
- 연구 lane은 기본 꺼짐입니다. `research.enabled`·`evidence_checkpoint`·`active_packs`를 설정에서 켜야 합니다([매뉴얼](../manual.md)).
- 이어 가기는 실제 CLI 미실측.
- 온톨로지 그림자 모델은 돌지만 산출 종류 선언이 꺼져 있어 아직 수확이 없습니다.
- 시설팀 LLM 직원은 없습니다(결정적 점검·자동 수정만).

## 2026-10-08 · #488 — verify가 crate의 File을 MANIFEST status와 상관없이 검사하고 깊은 JSON을 문제로 적음 (#475)

- 결론: PR #471 리뷰가 남긴 두 가지를 고쳤다. (1) 묶음 파일을 지우고 MANIFEST 행의 status만 `not copied: missing`으로 바꾸면 존재·hash 검사를 건너뛰어 `labhq verify`가 exit 0이었다. 이제 crate가 `File`로 선언한 경로는 status와 상관없이 검사하고, status가 crate 기록과 다르면 문제로 적는다. (2) 재귀 한도를 넘게 중첩된 `ro-crate-metadata.json`이 RecursionError traceback으로 끝났다. 이제 "nested too deeply" 문제 한 줄로 적는다.
- 바뀐 것: `labhq/ro_crate.py`(`verify_bundle_copy`), `docs/manual.md` verify 절, `tests/test_request_bundle.py` 2건.
- 실행한 것: 새 시험 2건이 기존 코드에서 실패(exit 0, RecursionError)하고 고친 뒤 통과. 전체 pytest(Windows) 4733 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: MANIFEST·crate·payload를 모두 일관되게 고쳐 쓰면 여전히 알 수 없다. 서명이 없는 사본 검사의 한계다.
- 근거: `tests/test_request_bundle.py::test_verify_checks_a_crate_file_whatever_its_manifest_status_says`, `::test_verify_reports_deeply_nested_crate_json_instead_of_crashing`.

## 2026-10-08 · #487 — 연구 단계 근거가 수집 안 된 파일을 인용하면 CP2 전에 한 번 교정 (#485)

- 결론: v0.5 시운전 8차(req_7ccde78be0)에서 qc_data 판정 근거가 계획에 없는 `outputs/qc/data_qc_checks.json`을 인용했다. 이 파일은 수집되지 않아 CP2에서 거부됐고, claim은 unsupported가 됐다. 리뷰어도 같은 점을 P2로 짚었다. 이제 단계 결과 교정 루프에서 원장이 계약을 통과한 뒤 CP2와 같은 결속 검사를 한 번 돌린다. 거부될 근거가 있으면 수집 산출 목록을 주고 인용을 고치게 한다. 이 교정이 실패하거나, 수집 안 된 파일만 새로 쓰거나, 인용을 그대로 두면 단계는 교정 전 결과로 통과하고 CP2가 지금처럼 거부한다. 교정 질문은 단계당 한 번이다. 미보고 산출 목록에서 `__pycache__` 아래 파일은 뺀다(관찰 기록에는 남음).
- 바뀐 것: `labhq/orchestrator/cso.py`(`ancestor_artifacts`로 CP2와 같은 조상 산출 목록, `unbound_evidence_problems`, 교정 루프의 `bindable` 폴백), `labhq/runner/daemon.py`(미보고 산출에서 `__pycache__` 제외), `docs/manual.md` 연구 lane 절, 시험 `tests/test_research_cp2.py` 4건·`tests/test_observed_outputs.py` 1건, `tests/test_research_report.py`는 합성 호출을 순서 대신 kind로 찾음.
- 실행한 것: 새 시험이 고치기 전 코드에서 실패, 고친 뒤 통과. 전체 pytest(Windows) 4736 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `tests/test_research_cp2.py::test_a_citation_of_an_uncollected_file_gets_a_correction_before_cp2`, `::test_a_binding_correction_never_fails_a_step_that_passed`.

## 2026-10-08 · #486 — runner probe가 어떤 경우에도 제한 시간 안에 돌아옴

- 결론: v0.5 시운전 7차(req_030729b6b3)가 "no runner hosts cso"로 즉시 실패했다. 두 runner가 gateway에 붙기 전에 PATH의 `python3 --version`을 조사하는데, Store Python Install Manager가 만든 `python3` 실행 별칭이 응답하지 않았다. faulthandler로 보니 `Popen` 안의 CreateProcess에서 돌아오지 않았다. 그래서 `Popen`·`wait`의 timeout으로는 막을 수 없다. 조사를 데몬 스레드에서 돌리고, 호출한 쪽은 `timeout + 1초` 뒤 `(None, "")`로 넘어가고, 끊는 일은 그 스레드가 이어서 한다. 실제 별칭으로 재보니 이전 코드는 150초 안에 돌아오지 않았고, 처음 고친 판(끊기까지 기다림)은 14.0초였다. 14초면 e2e 시험의 runner 등록 대기 15초를 넘어서, 끊기를 기다리지 않게 바꿨다.
- 바뀐 것: `labhq/runner/versions.py`(`_probe`는 스레드 제한 시간, `_run_probe`는 출력을 파이프 대신 임시 파일로, `_kill_tree`는 taskkill `/T`와 제한된 대기). doctor·codex_sandbox·engine 버전 조사가 모두 이 함수를 쓴다. `labhq/doctor.py`: 버전에 답하지 않는 interpreter는 package import 조사 8회를 건너뛴다(리뷰 P1). POSIX는 probe를 새 session으로 띄워 process group째 끊는다(리뷰 P2). `docs/manual.md` 알려진 한계 한 줄. `tests/test_probe_timeout.py` 4건(POSIX 1건), `tests/test_doctor.py` 1건.
- 실행한 것: 새 시험 3건, probe 관련 시험 162 passed, 실제 별칭 재현(고치기 전 150초 timeout, 첫 판 14.0초 ×2), 첫 판 전체 pytest(Windows) 4688 passed·58 skipped·19 failed·2 errors(전부 runner 등록 15초 대기 초과) → 지금 판으로 그 파일들 재실행 229 passed·2 skipped(4분 45초, 첫 수정 판 7분 2초), probe·doctor 시험 163 passed·1 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 끊기지 않는 별칭 프로세스와 그 스레드는 남을 수 있다(runner 한 번에 하나). 인스턴스는 지금 PATH에서 별칭 폴더를 빼고 띄우는 우회를 쓰는 중이며, 병합 뒤에도 그대로 둔다.
- 근거: `tests/test_probe_timeout.py::test_a_probe_returns_even_when_ending_the_child_never_returns`.

## 2026-10-08 · #483 — 명령줄이 넘치면 Claude 설정을 작업 폴더 옆 파일로 넘김

- 결론: v0.5 시운전 `req_14b3465387`이 10/12단계 뒤 결과 QC 단계에서 Windows 명령줄 32,288 > 32,000으로 막혔다. 프롬프트 포인터로도 넘치면 Claude `--settings`를 작업 폴더 옆 파일로 넘긴다. 넘치지 않으면 지금처럼 인라인이다.
- 바뀐 것: `labhq/adapters/base.py`(`RunContext.compact_command`, 두 번째 축소 단계), `labhq/adapters/claude_code.py`(`_settings_file`), `docs/manual.md` 알려진 한계 한 줄, `tests/test_adapters_fake_cli.py` 2건.
- 실행한 것: 새 시험 1건 수정 전 실패·수정 뒤 통과, 가드 시험 1건, 전체 pytest(Windows) 4671 passed·57 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 7차.
- 근거: `tests/test_adapters_fake_cli.py::test_settings_move_to_a_file_when_the_pointer_is_not_enough`.

## 결론

PR #481의 봇 리뷰 P1을 `caba362`에서 고쳤습니다. sed·curl·wget option을 확정하지 못하면 따옴표를 가리지 않고 원문을 검사합니다.

## 바뀐 것

- `-e'…'`·`-e"…"`·`--expression='…'`처럼 붙은 shell word 조각을 합쳐 sed script로 판별합니다.
- 알려진 sed option과 묶음을 명시해 해석하고, 변수·미지원 option·script file은 fail closed로 처리합니다.
- curl·wget도 확인한 option만 데이터 인수로 다루며, config와 미지원 option은 원문 검사로 돌립니다.

## 실행한 것

- 관련 policy test: 252 passed.
- Windows 전체 pytest: 4702 passed, 57 skipped.
- `scripts/check_public.sh`, `git diff --check` 통과.

## 미해결

push 뒤 CI 확인 전입니다.

Closes #479

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 2026-10-08 · #480 — 단계에 간접 조상 단계의 산출도 inputs/로 연결

- 결론: 단계의 입력 링크와 읽기 폴더를 직접 의존 단계에서 모든 조상 단계로 넓혔다. v0.5 시운전 `req_8433789745`의 전처리 단계가 간접 조상(fetch)의 산출을 못 찾아 실패한 원인이다.
- 바뀐 것: `labhq/orchestrator/cso.py`(`input_steps`, `upstream_steps`·`upstream_dirs`, 간접 조상 파일 목록 맥락), `docs/manual.md` 한 문장, `tests/test_cso.py` 1건.
- 실행한 것: 새 시험 수정 전 실패·수정 뒤 통과, 전체 pytest(Windows) 4669 passed·57 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 6차.
- 근거: `tests/test_cso.py::test_a_step_gets_inputs_links_for_ancestors_it_reaches_through_other_steps`.

# 2026-10-08 · PR #477

## 결론

네 번째 봇 리뷰의 P1 두 건을 `3626ebd`에서 고쳤다. 미등록 출처와 현재 artifact hash를 `labhq verify`가 다시 정확히 판정한다.

## 바뀐 것

- Crossref와 doi.org가 모두 404인 DOI는 PMID·PMCID 부재와 같이 `not_found` 결함이 된다.
- 감사 재검증은 단계별 output scan의 `path → sha256`을 artifact 출처 검증에 넘긴다.
- 관찰 hash가 인용 hash와 같으면 exit 0, 다르면 conflict와 exit 1이다.

## 실행한 것

- 관련 pytest: 32 passed, 2 skipped.
- Windows 전체 pytest: 4683 passed, 57 skipped.
- `scripts/check_public.sh`, 패치노트·목차 검사 통과.

## 미해결

- push 뒤 CI 전체 job 결과를 확인한다.

Closes #422

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 2026-10-08 · #478 — 결과 원장 salvage가 설명 조각과 실패 조회 링크에서 포기하지 않게 함

- 결론: v0.5 시운전 `req_ce7c64089c`의 s10 annotation 원장이 교정 두 번 뒤 salvage까지 실패해 요청 전체가 끝났다. salvage는 이제 행 참조 없는 설명 조각을 건너뛰고, 실패·0건 조회를 근거로 단 링크만 거부한다. 실제 원장은 문제 행 하나와 그 링크만 빠지고 claim 둘이 남는다.
- 바뀐 것: `labhq/research/contract.py`(`_names_a_row`, 설명 조각 규칙, 실패 조회 링크 대상), `labhq/evidence/claims.py`(0건 observed 안내), `docs/manual.md` 연구 절 한 문장, `tests/test_research_cp2.py` 3건.
- 실행한 것: 새 시험 중 2건은 수정 전 실패·수정 뒤 통과, 실제 원장 재현, 전체 pytest(Windows) 4665 passed·57 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 5차.
- 근거: `tests/test_research_cp2.py::test_a_failed_lookup_linked_as_support_is_salvaged_by_refusing_only_the_link`.

## 2026-10-08 · #476 — 요청 묶음 폴더 이름 바꾸기 Windows 잠금 재시도

- 결론: 요청 묶음의 마지막 rename이 Windows 공유 잠금(WinError 5)에 걸리면 0.2·0.5·1·2초 간격으로 다시 시도한다. v0.5 시운전 `req_ce7c64089c`의 묶음이 이 오류로 남지 않았다.
- 바뀐 것: `labhq/request_bundle.py`(`_replace_dir`, `RENAME_RETRY_DELAYS`), `tests/test_request_bundle.py` 3건.
- 실행한 것: `tests/test_request_bundle.py` 84 passed·2 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `tests/test_request_bundle.py::test_bundle_rename_retries_a_brief_windows_lock`.

# 2026-10-08 · PR #474

## 결론

봇 리뷰 P1 두 건을 고쳤다. 출력 없는 직원 CLI는 도구 호출과 하위 명령이 모두 없다고 확인될 때만 종료한다.

## 바뀐 것

- Codex의 tool item과 Claude의 `tool_use`·`tool_result`를 짝지어 완료되지 않은 호출 수를 센다.
- 호출이 0이고 process tree에도 실행 중인 명령이 없을 때만 transient로 끝낸다. 스트림을 해석할 수 없으면 끝내지 않는다.
- UAC와 process 조회 실패는 감시를 끝내지 않고 다시 확인한다. 같은 상태의 경고는 반복하지 않는다.

## 실행한 것

- Windows 전체 pytest: 4611 passed, 56 skipped.
- 관련 어댑터 pytest: 72 passed. `scripts/check_public.sh` 통과.

## 미해결

- CI와 후속 봇 판단은 총괄이 이어받는다.

Closes #473

🤖 Generated with Codex (gpt-5) for the labhq dev lead

## 2026-10-08 · #469 — 요청 묶음 RO-Crate와 사본 검증

- 결론: PR #471의 validator 실패와 4차 리뷰 P1 두 건을 `7248895`에서 고쳤다.
- 실패 원인: `conformsTo`가 `schema:conformsTo`로 확장돼 `dct:conformsTo`를 요구하는 `process-run-crate-0.5_2.1`(Root Data Entity conformsTo: "The Root Data Entity MUST reference a CreativeWork entity with an @id URI that is consistent with the versioned permalink of the profile")과 `ro-crate-1.1_5.3`(Metadata File Descriptor entity: `conformsTo` property: "The RO-Crate metadata file descriptor MUST have a `conformsTo` property with the RO-Crate specification version")이 실패했다.
- 바뀐 것: `conformsTo`를 DCTERMS로 고쳤고, MANIFEST에 기록된 crate·README 누락과 README 읽기 실패를 문제로 처리한다. 묶음 폴더 이름과 root Dataset `identifier`도 요청 ID와 대조한다.
- 실행한 것: `roc-validator==0.12.2` REQUIRED 42/42·exit 0, 관련 82 passed·1 skipped, Windows 전체 4519 passed·57 skipped, 공개 검사 통과.
- 미해결: push 뒤 CI 결과 확인 전이다.
- 근거: `labhq/ro_crate.py`, `labhq/evidence/audit.py`, `tests/test_request_bundle.py`, `docs/manual.md`.

## 2026-10-07 · #470 — 계획의 pack·점검표 키 표기 정규화와 교정 안내 수정

- 결론: v0.5 두 번째 시운전(`req_bb9f96f460`)이 CP1 전에 실패한 원인을 고쳤다. CSO가 쓴 `bulk_tumor_normal`(판 없음)과 `microarray_expression.batch`(topic.id)를 검증 전에 정확한 키로 바꾼다. 틀린 pack 키의 교정 안내는 이제 topic이 적용한 pack을 기대값으로 보인다. 전에는 `[]`를 보여 CSO가 pack 값을 지웠다.
- 바뀐 것: `labhq/research/packs.py`(`normalize_pack_keys`, 선택 오류 문구), `labhq/vocab/topic_checklists.py`(`normalize_answers`), `labhq/orchestrator/cso.py`(`normalize_plan_keys`를 두 lane에 적용, 선택 예외 때 기대 pack), `docs/manual.md` 한 줄, `tests/test_research_bulk_pack.py` 3건.
- 실행한 것: 새 시험 중 2건은 수정 전 실패·수정 뒤 통과. 전체 pytest(Windows) 4471 passed·56 skipped. 실제 계획 4개(`req_cec10975ab` 1개, `req_bb9f96f460` 3개)로 원인 재현.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 재제출.
- 근거: `tests/test_research_bulk_pack.py::test_cso_spellings_of_pack_and_checklist_keys_reach_cp1_without_a_correction`, `::test_wrong_pack_key_correction_names_the_applied_pack_not_an_empty_snapshot`.

## 2026-10-07 · #468 — 설치 명령 파서 fail closed

- 결론: 두 번째 봇 리뷰 P1 두 건까지 고쳐 중첩 shell 판별 부류를 구조적으로 닫았다.
- 바뀐 것: shell별 command option과 인자 소비를 구분하고, 실행 파일·subcommand·`-m` module·설치 목적지만 리터럴인지 검사한다. 일반 분석 인자의 변수 확장은 허용한다.
- 실행한 것: 환경 파서 pytest 284 passed·1 skipped, Windows 전체 pytest 4594 passed·56 skipped, public 검사 통과.
- 미해결: CI와 후속 봇 판단은 총괄이 이어받는다.
- 근거: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.

## 2026-10-07 · #467 — 재개한 요청이 재개 뒤 자기 호출을 다시 받지 않게 함

- 결론: 재시작 뒤 재개된 요청은 재개 전에 보낸 task만 이어받는다. 재개 뒤 새로 보낸 같은 식별의 호출(clarify 답 뒤 재계획, A/B·교정 재계획)은 새로 돈다.
- 바뀐 것: `labhq/gateway/server.py`(`recovery_started`, `_recovery_matches`를 `recovery_attempt`·`dispatch`가 함께 씀), `docs/manual.md` 상태 절 한 줄, `tests/test_state.py` 회귀 1건.
- 실행한 것: 새 회귀가 수정 전 코드에서 실패하고 수정 뒤 통과. 관련 9개 파일 445 passed·1 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 발견 경위는 v0.5 두 번째 연구 시운전(trial, 재시작 뒤 재개한 요청이 clarify 답 직후 실패). 시운전은 새 요청으로 다시 돈다.
- 근거: `tests/test_state.py::test_resumed_request_does_not_replay_its_own_earlier_call`.

## 2026-10-07 · #462 #466 — 영문 README 근거 논문 수치 갱신

- 결론: `README.en.md`의 topic 근거 논문 수치를 #462 이후 값(topic당 30편 이상, 1286편)으로 맞췄다.
- 바뀐 것: `README.en.md` 한 문장. 코드 변경 없음.
- 실행한 것: `scripts/check_public.sh`, `scripts/patch_notes.py check`. 다른 문서의 `1255`는 #455 검증 이력이라 그대로 뒀다.
- 미해결: 없음.
- 근거: `docs/reference/topic_papers.tsv`(1286행), `README.md` 같은 절.

# 2026-10-07 · PR #464

## 결론

Paper2Agent 설치 갱신에서 원본 누락·복사 실패가 기존 skill을 지우던 버그를 고칩니다.

## 바뀐 것

- 원본 폴더와 SKILL.md를 확인하고, Claude Code·Codex의 새 복사를 모두 staging에 마친 뒤 설치를 교체합니다.
- 교체 실패는 기존 두 설치로 rollback합니다. 복구도 실패하면 백업을 보존하고 경로를 오류에 표시합니다.
- install_skill의 인자·반환값과 설치 위치를 유지하고, manual에 실패 때의 동작을 적었습니다.
- 세 PR의 최종 status를 각 PR 본문과 맞춰 알려진 P1/P2 후속이 보고에서 빠지지 않게 했습니다.

## 실행한 것

- tmp_path와 mocked git만 사용한 실패 주입 회귀: 14 passed.
- Windows 전체 pytest: 4467 passed, 56 skipped. 단일 basetemp 종료 삭제 확인.
- scripts/check_public.sh 통과.

## 남은 지적

- P2: rename 완료 직후 상태 기록 전 중단 구간의 복구가 남습니다. 현재 회귀는 동기 복사·교체 오류를 검증했습니다.

#424의 source commit 고정·license·hash·외부 전송 계약은 별도 범위입니다. 이 PR은 설치 실패 버그만 다루며 #424를 닫지 않습니다.

Refs #424

🤖 Generated with Codex for the labhq dev lead

# 2026-10-07 · PR #462

## 결론

43 topic 모두 근거 논문 30편 이상을 채웠습니다. 총 1255편에서 1286편으로 늘었습니다.

## 바뀐 것

| topic | 이전 | 현재 |
|---|---:|---:|
| viral_genomics | 20 | 30 |
| metaproteomics | 23 | 30 |
| microarray_expression | 24 | 30 |
| amplicon_sequencing | 25 | 30 |
| metagenome_assembly | 28 | 30 |
| metatranscriptomics | 29 | 30 |

- 직접 관련된 방법·프로토콜·벤치마크 논문 31편을 추가했습니다. 기존 1255행과 8개 열 형식은 유지했습니다.
- README·manual·topic 요약표를 갱신하고, v0.9 로드맵의 논문 목표와 HANDOFF 작업 큐를 완료로 갱신했습니다.
- 추가 PMID의 검증표는 `docs/reference/topic_papers_added_2026-10-07.verified.tsv`에 남겼습니다.

## 실행한 것

- 지정된 `verify_sources.py`: 31행 한 묶음, 31/31 ok. 새 저자·연도·저널·제목은 NCBI esummary metadata에서 가져왔습니다.
- 독립 QA: 원본 필드·상대 순서 보존, 신규 서지 metadata와 문서 수치 일치.
- 수치 대조: PMID 중복 0, 최소 30·최대 82편, 여러 topic에 걸친 논문 429편.
- `scripts/vocab_tables.py --check`, `scripts/check_public.sh` 통과.
- Windows 전체 pytest: 4453 passed, 56 skipped. 단일 basetemp 종료 삭제 확인. 첫 실행의 ledger 기록 실패 1건은 단독 재실행과 전체 재실행에서 통과했습니다.

## 남은 지적

- P2: README.en.md의 옛 논문 수치(20/1255)를 30/1286으로 맞추는 후속 작업이 남습니다.

🤖 Generated with Codex for the labhq dev lead

# 2026-10-07 · PR #460

## 결론

환경 단계에서 판별되는 설치 명령은 작업 폴더 환경만 예외로 허용합니다. 남은 parser P1은 #461로 넘겼습니다.

## 바뀐 것

- `.venv` interpreter, 로컬 `--target`, conda `-p`, uv `--python`은 허용합니다.
- bare 설치, 폴더 밖 경로·링크, 충돌 옵션, 설치 환경변수 우회를 거절합니다.
- shell wrapper·분기문·실행 치환을 판별하고, Linux에서도 Windows drive·UNC 경로를 거절합니다.
- `R CMD INSTALL`은 R이 받는 `-l LIB`·`--library=LIB`를 검사합니다. 환경 단계는 로컬 library, 뒤 단계는 `./.rlib`만 허용합니다.
- 환경 계획은 Windows·POSIX의 `.venv` Python 경로를 지정합니다. 뒤 단계의 `./.pylib`·`./.rlib` 제한은 유지합니다.

## 실행한 것

- R 직접 판정: 157 passed·1 skipped.
- Windows 전체 pytest: 4453 passed·56 skipped. 단일 basetemp는 종료 뒤 삭제했습니다.
- WSL 관련 검사: 399 passed·2 skipped·9 failed. 실패는 DrvFS의 기존 POSIX 권한 검사입니다. Linux 3.10·3.12 CI는 통과했습니다.
- `scripts/check_public.sh` 통과. patch notes·status 검사도 통과했습니다. 프롬프트 SHA 두 값을 갱신했고 정확한 비교와 schema 기대값은 유지했습니다.
- 실제 설치·labhq 요청·직원 CLI 호출은 하지 않았습니다.

## 남은 지적

봇 P1인 비리터럴 설치 인자·subshell cwd 복원과 인용된 중첩 shell 본문은 후속 #461로 넘깁니다. 같은 parser 부류는 구조 수정 한 번 뒤 병합한다는 작업 규칙을 따릅니다. 간접 script·상속된 installer 설정·검사 뒤 파일시스템 변경은 기존 직접 명령 가드의 범위 밖입니다.

Fixes #458

🤖 Generated with Codex for the labhq dev lead

# 2026-10-07 · PR #460

## 결론

환경 단계도 자기 작업 폴더 안 환경에만 설치합니다. 공유 환경을 쓰는 모든 shell 호출은 승인 게이트를 거칩니다.

## 바뀐 것

- `.venv` interpreter, 로컬 `--target`, conda `-p`, uv `--python`은 허용합니다.
- bare 설치, 폴더 밖 경로·링크, 충돌 옵션, 설치 환경변수 우회를 거절합니다.
- shell wrapper·분기문·실행 치환을 판별하고, Linux에서도 Windows drive·UNC 경로를 거절합니다.
- 환경 계획은 Windows·POSIX의 `.venv` Python 경로를 지정합니다. 뒤 단계의 `./.pylib`·`./.rlib` 제한은 유지합니다.

## 실행한 것

- Windows 전체 pytest: 4424 passed·56 skipped. 단일 basetemp는 종료 뒤 삭제했습니다.
- WSL 관련 검사: 399 passed·2 skipped·9 failed. 실패는 DrvFS의 기존 POSIX 권한 검사이며 CI에서 다시 확인합니다.
- `scripts/check_public.sh` 통과. patch notes·status 검사도 통과했습니다. 프롬프트 SHA 두 값을 갱신했고 정확한 비교와 schema 기대값은 유지했습니다.
- 실제 설치·labhq 요청·직원 CLI 호출은 하지 않았습니다.

## 남은 지적

인용된 중첩 shell 본문은 후속 #461로 넘깁니다. 간접 script·상속된 installer 설정·검사 뒤 파일시스템 변경은 기존 직접 명령 가드의 범위 밖입니다.

Fixes #458

🤖 Generated with Codex for the labhq dev lead

# 2026-10-07 · PR #460

## 결론

환경 단계도 자기 작업 폴더 안 환경에만 설치합니다. Claude의 사전 허용 규칙과 승인 게이트에 있던 환경 단계 예외를 함께 좁혔습니다.

## 바뀐 것

- `.venv` interpreter, 작업 폴더의 `--target`, conda prefix, uv `--python`은 허용합니다.
- bare 설치, 폴더 밖 경로, 경로 이탈·링크·충돌 옵션·같은 명령의 설치 환경변수 우회는 거절합니다.
- 환경 계획은 Windows·POSIX의 `.venv` Python 경로를 직접 지정합니다. 뒤 단계의 `./.pylib`·`./.rlib` 제한은 유지합니다.

## 실행한 것

- 관련 pytest 319 passed·12 skipped, CSO pytest 196 passed, 마지막 직접 판정 67 passed·1 skipped.
- Windows 전체 pytest: 4351 passed·56 skipped. 매번 단일 basetemp를 쓰고 종료 후 지웠습니다.
- `scripts/check_public.sh`, `python scripts/patch_notes.py check`, `python scripts/notes_index.py --validate` 통과.
- 프롬프트 변경에 맞춘 SHA 두 값만 갱신했으며 정확한 SHA 비교와 schema 기대값은 유지했습니다. 실패 회귀 묶음은 133 passed·1 skipped.
- 실제 패키지 설치·labhq 요청·직원 CLI 호출은 하지 않았습니다.

## 미해결

기존 직접 명령 가드의 한계는 유지됩니다. 간접 script, 상속된 installer 설정, 검사 뒤 파일시스템 변경은 검증 범위 밖입니다.

Fixes #458

🤖 Generated with Codex for the labhq dev lead

## 2026-10-07 · 로그인 대기 test가 CI 부하에서 흔들리던 것

- 결론: `tests/test_login_wait.py::test_login_retry_expiry_ends_notice_and_next_failure_notifies_again`의 로그인 대기 상한을 0.2초에서 1초로 늘렸다. 검사 내용은 그대로다.
- 바뀐 것: `tests/test_login_wait.py` 한 줄과 주석.
- 실행한 것: `tests/test_login_wait.py` 45 passed. PR #457 CI의 `pytest-windows`가 이 test에서 두 번 연속 `calls == 1`로 실패했고, 같은 커밋을 다시 돌리자 통과했다.
- 미해결: 없음.
- 근거: 이 PR.

## 2026-10-07 · #35 시설팀 3단계 — 승인된 환경 수정 (PR #457)

- 결론: 봇 P1 4건을 고쳐 패키지는 원 단계의 격리 폴더에 설치하고, 복원 승인과 `disk_full` 정리가 멈추지 않게 했습니다.
- 바뀐 것: 직원 산출물 interpreter 실행 제거, 고정 `./.pylib`·`./.rlib` 재실행 지시, 승인 거절·만료 복구, 캐시 우선 정리, manual.
- 실행한 것: 관련 272 passed·1 skipped, 재현 39 passed, Windows 전체 pytest 4260 passed·55 skipped, Node CJS 28개, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: `labhq/facilities/fixes.py`, `labhq/facilities/fixes.yaml`, `tests/test_facilities_fixes.py`.

### 두 번째 리뷰 반영 (`bcf3429`)

- 결론: 검증된 패키지만 자동 설치 카드에 올리고, 재실행 지시는 적용과 성공을 구분합니다.
- 바뀐 것: Python import→PyPI distribution 38개, R package→CRAN·Bioconductor 44개 매핑, Bioconductor 설치 분기, `applied`→`succeeded`·`failed` 상태와 웹 문구.
- 실행한 것: 공식 저장소 82개 이름 확인, 관련 pytest 145 passed, Windows 전체 4264 passed·55 skipped, Node CJS 28개, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: `labhq/facilities/packages.yaml`, `labhq/facilities/fixes.py`, `labhq/orchestrator/cso.py`, `tests/test_facilities_fixes.py`.

## 2026-10-07 · #456 — 공유 environment 동시 설치 격리

- 결론: 환경 단계만 공유 environment를 바꾸고, 뒤 단계의 추가 패키지는 `./.pylib`·`./.rlib`에 격리한다.
- 바뀐 것: 보호 요청의 설치 가능 shell 규칙을 승인 게이트로 보내고, 경로가 붙은 Python package manager와 R 설치 호출별 library를 검사한다.
- 실행한 것: Windows 전체 pytest 4214 passed·55 skipped, 관련 pytest 457 passed·11 skipped, public·패치노트·목차 검사 통과.
- 미해결: Codex·Gemini·Antigravity와 셸 script 안의 간접 설치는 호출 훅이 없어 공통 지침에 의존한다.
- 근거: `labhq/environment_install.py`, `labhq/tools/approval_mcp.py`, `labhq/adapters/claude_code.py`, `tests/test_mcp_servers.py`, `tests/test_private_paths.py`.

## 2026-10-07 · #446 — topic별 근거 논문 묶음

- 결론: topic 43개 모두 근거 논문 10편 이상(최소 20, 최대 82), 모두 1255편이다. 30편 미만은 6개(`viral_genomics` 20, `metaproteomics` 23, `microarray_expression` 24, `amplicon_sequencing` 25, `metagenome_assembly` 28, `metatranscriptomics` 29).
- 바뀐 것: `docs/reference/topic_papers.tsv`·`topic_papers.md` 추가, reference README·README·README.en·manual에 한 줄씩 연결.
- 실행한 것: 1255개 PMID 전부 NCBI esummary 대조(제목 단어 50% 이상 겹침) → 1255 ok·0 제외, `scripts/check_public.sh`, `pytest -q`.
- 미해결: 30편 목표(PI 결정)는 위 6개 topic이 남았다.
- 근거: `docs/reference/topic_papers.md`.

## 2026-10-07 · 한국어 Windows UTF-8 모드에서 키 파일 권한 검사가 멈추던 문제

- 결론: `labhq init`의 AlphaGenome 키 쓰기와 doctor의 계정 확인이 `whoami`·`icacls` 출력을 콘솔 코드 페이지로 읽는다. 전에는 `PYTHONUTF8=1`(인스턴스를 띄우는 방식)에서 한국어 출력(CP949)을 UTF-8로 읽다 실패해 키 쓰기가 멈추고 doctor는 계정을 잃었다.
- 바뀐 것: `labhq/tools/annot_keys.py`(`CONSOLE` 인코딩), `labhq/doctor.py`(whoami), `tests/test_annot_keys_console.py`.
- 실행한 것: `PYTHONUTF8=1` pytest `tests/test_annot_regulatory.py`·doctor test·새 test 통과. 고치기 전에는 이 PC에서 `test_with_a_key_the_server_offers_alphagenome_and_keeps_the_key_out_of_everything`가 `UnicodeDecodeError`로 실패했다.
- 미해결: 없음.
- 근거: 이 PR.

## 2026-10-07 · 어휘·topic·점검표 표 (#453)

- 결론: PI 요청대로 topic이 무엇인지, topic별 점검표, 핵심 단어(어휘 키)를 표로 보는 md 하나(`docs/vocabulary.md`)를 README에서 링크했다.
- 바뀐 것: `scripts/vocab_tables.py`(원본 `labhq/vocab/`에서 생성, `--write`·`--check`), `docs/vocabulary.md`(topic 43·점검표 124항목·data 30·format 28·operation 22·공개 자원 21), `tests/test_vocab_tables.py`(어긋나면 실패), README·영문 README 링크, HANDOFF 작업 방식 한 줄.
- 실행한 것: `tests/test_vocab_tables.py`·`tests/test_integrations.py` 36 passed, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: PR #453.

## 2026-10-07 · #446 — 점검표: 가능하면 하고, 못 하면 이유와 경고 (#452)

- 결론: topic 점검표는 자료가 허락하는 점검을 하고, 못 한 점검은 `assumption: <못 한 이유>`로 답한다. 이유가 있는 생략은 받아들이고 항목마다 경고 `점검 못 함 <topic>/<id>: <이유>`를 한 번 띄운다. 이유가 비었거나 자리표시(`-`, `n/a`, `없음` 등)인 답은 누락과 같아 기존 교정 경로를 탄다. 승인 카드나 실행 중단은 없다.
- 바뀐 것: `labhq/vocab/topic_checklists.py`(자리표시 판정, `skipped`·`skip_warnings`, 계획 규칙 문구), `labhq/orchestrator/cso.py`(일반·연구·재계획 경로의 경고, `보고서 경고` 건수, 일반·연구 리뷰 프롬프트), `labhq/web/state.js`(피드 alert 한 줄씩), manual 점검표 절 끝 한 문단과 로드맵, HANDOFF PI 결정 2줄·#420 행. 연구 lane은 이유 있는 `assumption`을 전에도 통과시켰고 test로 확인했다. 경고는 동결 계획에 들어가 CP1 hash에 포함된다.
- 실행한 것: 바꾼 모듈을 import하는 test 파일 43개 1645 passed·4 skipped, 전체 suite 4197 passed·55 skipped, `node tests/web_checklist_skip.cjs`, `scripts/check_public.sh`.
- 미해결: 없음. manual의 기존 점검표 문단은 topic-papers PR과의 충돌을 줄이려고 손대지 않았다(새 문단은 절 끝).
- 근거: `tests/test_topic_checklists_precedents.py`, `tests/web_checklist_skip.cjs`.

### 리뷰 반영 (35f62e7)

- 재계획: `with_checklist_skip_warnings`가 옛 `점검 못 함` 경고를 지우고 현재 점검표 것만 붙인다. 이제 하는 점검의 경고가 남거나 이유가 바뀐 항목이 두 건으로 세어지지 않는다.
- 웹 피드: 같은 요청에서 이미 보인 생략 경고는 재계획 `request.plan`에서 다시 올리지 않는다.
- 재시작: #446 전에 CP1 승인된 연구 계획의 자리표시 이유(`not_applicable: none` 등)는 실패 대신 요청의 `checklist_reasonless`에 남긴다. 동결 계획은 hash 때문에 바꾸지 않는다.
- 실행한 것: 바꾼 모듈을 import하는 test 파일 44개 1704 passed·6 skipped, 전체 suite 4199 passed·55 skipped, `node tests/web_checklist_skip.cjs`. 새 test 셋은 옛 코드에서 실패함을 확인했다.

## 2026-10-07 · #382 — 개인 경로를 Codex sandbox 읽기 거부로

- 결론: 안 A 적용 뒤 직원 홈(elevated)에서 실측했다. 개인 경로가 Codex sandbox의 읽기 거부로 걸리고(canary `Access is denied`), 작업 폴더·`--add-dir` 쓰기는 그대로이며 승인 창은 뜨지 않았다.
- 바뀐 것: Codex 격리 실행의 `-s` → permission profile(`extends` + `filesystem` deny), 기본 개인 경로 `~/.gemini`·`~/.env`, doctor의 다른 elevated 홈 경고가 낡은 직원 홈에서도 나옴, manual.
- 실행한 것: 실측 2건(`:workspace`, `:read-only`), 관련 test 560 passed·2 skipped, `scripts/check_public.sh`.
- 미해결: 없음. PI 홈은 unelevated라 PI 본인 Codex와 총괄 위임에는 deny-read가 없다(안 A의 대가).
- 근거: PR #451 본문 실측 표, 직원 홈 `.sandbox/deny_read_acl_state.json`.

## 2026-10-06 · #435 C ③ — labhq_annot 조절 영역·발현·AlphaGenome (#450)

- 결론: 직원이 ChIP-Atlas(enrichment·target genes), ENCODE cCRE, GTEx(발현·eQTL)를 내장 도구로 조회하고, 키가 있으면 AlphaGenome 예측도 부른다. 판본 기록·조회 로그·결과 파일·캐시는 #449와 같은 틀이다. 통제 구역 데이터의 변이·영역도 경고·승인 없이 조회한다(PI 결정 10-06).
- AlphaGenome 키: `labhq init`이 키가 없을 때 한 번 묻고(Enter면 건너뜀) `annot.alphagenome_key_file`에만 둔다. POSIX 0600, Windows는 상속을 끊고 현재 계정만. 설정·로그·이벤트·doctor·MCP 명령줄에는 값이 없고, doctor는 키 있음/없음만 보인다. 키나 `alphagenome` client가 없으면 도구를 등록하지 않는다.
- 바뀐 것: `labhq/tools/annot_regulatory.py`·`annot_keys.py`(새), `annot_mcp.py`, `annot.py`(pace 항목), `settings.py`(`annot`), `init_wizard.py`, `doctor.py`, `private_paths.py`(키 파일), 직원 4명 안내, CSO roster, 연결된 도구 표, `public_resources.tsv`, 예시 설정, manual. 기존 test 기대값 한 곳(`test_annot.py` stdio 도구 목록)은 새 도구가 그대로 바꾸는 값이라 고쳤다.
- 실행한 것: `tests/test_annot_regulatory.py` 23 passed·1 skipped(numpy 없음), 관련 test 15개 파일 752 passed·14 skipped, 전체 suite 4052 passed·55 skipped·1 failed(Windows). 실패한 `test_semantics_shadow_remove`의 한 test는 단독 3회 통과했고 첫 전체 실행에서도 통과해 부하 때문으로 본다. GTEx·ChIP-Atlas target genes·ENCODE는 새 코드로 실제 API를 불러 확인했다. `scripts/check_public.sh` 통과.
- 미해결: ChIP-Atlas enrichment는 제출까지만 실측했다. 10-06 18:01·18:06에 낸 두 잡이 18:59까지 결과를 내지 않아 응답 열은 EA 스크립트 소스를 따랐다. AlphaGenome은 키가 없어 실제 호출하지 않았다(client v0.9.0 소스 계약을 따름). Codex 로컬 리뷰는 30분 동안 진행 없이 멈춰 취소했다.
- 근거: `labhq/tools/annot_regulatory.py`, `tests/test_annot_regulatory.py`, PR #450 본문의 "근거 문서" 절.

### 리뷰 반영 (19:18, 2a1ba2d)

- 고친 것: AlphaGenome 구간 요약을 요청 구간의 bin으로 자름(P1, 전에는 1 Mb 창 전체의 최대·평균), client create 30초 timeout·전용 thread 2개·실패 뒤 남은 항목 즉시 실패, 기본 길이 100KB와 1 bp 출력 전 track 500KB·1MB 거부, GTEx 여러 조직은 조직마다 받고 개수도 조직별, ChIP-Atlas request_id를 제출 때 입력·옵션 hash와 묶어 다르면 거부(기록 없는 id는 캐시 안 함), 유전자 철자 그대로 dedup·캐시, 키 폴더가 다른 항목·홈·labhq state를 품으면 저장 거부, runner 계정용 키 절차(`docs/runner-account.md`), `test_annot.py` stdio test를 임시 홈·설정으로 띄우고 도구 목록을 정확히 비교.
- 고치지 않은 것: AlphaGenome `score_variant`(권장 scorer) 전환은 출력 형식이 바뀌는 별도 작업이라 이번에는 크기 상한으로 막았다.
- GTEx 배열 파라미터(`tissueSiteDetailId` 반복)는 이 PC의 TLS 가로채기로 실측하지 못해, 이미 확인한 단일 조직 query를 조직마다 부르는 쪽을 골랐다.

## 2026-10-06 · #90 #58 — revise 뒤 새 CP1로 이어 가기 (PR #448)

- 결론: 연구 리뷰가 revise로 끝나면 이어 가기 카드가 뜬다. 승인하면 리뷰 P1을 넣은 새 계획을 새 CP1로 승인받고, 바뀐 단계와 새 단계만 다시 돈다. 거절하거나 답이 없으면 지금처럼 `research_review_revise`로 끝난다.
- 바뀐 것: 재사용은 단계 사양·frozen context가 바이트까지 같고, P1이 그 단계를 가리키지 않고, 위 단계가 모두 재사용되고, 새 CP1 직후 산출 sha256이 그대로일 때만 한다. 이전 차수는 `research_contract.rounds`에 남고, 2차 이상 task는 `research_round`로 재시작 복구에서 이전 차수와 섞이지 않는다. 상한은 `research.revise_continuations`(기본 2).
- 리뷰 반영: 새 계획이 이전과 hash까지 같아도 새 CP1·CP2·리뷰를 다시 받는다(전에는 이전 승인을 그대로 써 무한 반복). 재사용이 거부된 단계는 이전 PI 결정 없이 새로 돈다. 새 CP1 거절·계획 실패로 끝나면 이전 차수 결과·리뷰로 `research_review_revise`. 재사용 단계의 salvage 거부 행이 다음 CP2에도 보인다. 끝난 요청은 나중에 이어 갈 수 없다는 한계를 manual에 적었다.
- 실행한 것: 새 test 13개(리뷰 반영 5개 포함, 고치기 전 5개 모두 실패 확인), cso를 import하는 test 48개 파일 1682 passed·5 skipped, 전체 pytest 4017 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 실제 CLI로 이어 가기 실측. runner가 gateway와 다른 PC면 hash를 못 읽어 재사용 없이 다시 돈다. 끝난 요청을 나중에 이어 가는 CLI·API는 없다.
- 근거: `labhq/research/continuation.py`, `labhq/orchestrator/cso.py`, `tests/test_research_continue.py`.

## 2026-10-06 · #435 C ② — 변이 주석 MCP labhq_annot (#449)

- 결론: 직원이 VEP·gnomAD·ClinVar를 내장 도구로 조회한다. 결과마다 DB 판본이 붙고, 작업 폴더에 조회 기록과 응답 전체가 남는다. 통제 구역에서 나온 변이도 경고·승인 없이 조회한다(PI 결정 10-06).
- 바뀐 것: `labhq/tools/annot.py`·`annot_mcp.py`, runner 배선(`auto_approve`), analyst·bioinfo-agent·biologist·lit_scout의 `builtin_mcp`, CSO roster·capability card, 연결된 도구 표·배지, `public_resources.tsv` use 문구, manual "공개 자원 조회 도구" 절. 기존 test 기대값 두 곳(bioinfo-agent `builtin_mcp`, README 배지 문구)은 이 기능이 그대로 바꾸는 값이라 고쳤다.
- 실행한 것: `tests/test_annot.py` 20 passed(HTTP 전부 가짜), 관련 test 13개 파일 441 passed·13 skipped, 전체 suite 4024 passed·54 skipped(Windows). test 밖에서 공개 변이로 세 API를 한 번 실측해 VCV 검색어와 gnomAD 인구 요약을 고쳤다. `scripts/check_public.sh` 통과.
- 리뷰 반영(6a43504): ClinVar가 VCF indel을 padding base가 아닌 구간으로 찾는다(BRCA1 c.68_69del을 놓치던 P1). canonical SPDI와 repeat를 감안해 비교하고, 같은 allele이 없으면 `exact_match: false`를 붙인다. gnomAD chrM은 `mitochondrial_variant`로 묻고, gnomAD 캐시는 30일 뒤 만료된다. 결과 파일 이름에 내용 hash를 넣어 같은 요청을 반복해도 덮어쓰지 않는다. 공유 캐시 위조 지적은 받지 않았다: 직원과 runner가 같은 OS 계정이라 MAC 키도 직원이 읽는다.
- 리뷰 반영 확인: `tests/test_annot.py` 25 passed, 영향 test 5개 파일 330 passed·3 skipped, 전체 suite 4029 passed·54 skipped. ClinVar 구간 검색과 gnomAD chrM query는 공개 API로 실측했다.
- 미해결: 속도 조절이 MCP 프로세스마다 따로라 동시 호출 합은 재시도에 맡긴다. ClinVar 위치 검색은 left-normalise된 VCF를 전제로 한다.
- 근거: `labhq/tools/annot.py`, `tests/test_annot.py`, PR #449 본문의 "API 문서" 절.

## 2026-10-06 · #35 시설팀 2단계 — environment 실패 종류와 오류 서명 표 (PR #447)

- 결론: 환경 고장(패키지·명령·R 패키지 없음, 디스크 부족, 이름 풀이·프록시, Docker 데몬, Codex sandbox 준비)이 `environment`로 분류되고 재시도하지 않는다. 웹 작업판·보고서·`labhq status`에 "환경 문제: 원인 — 할 일"이 뜬다.
- 바뀐 것: `labhq/facilities/signatures.yaml`·`.py`, `failure_kind`(login·quota 다음), runner의 실패 출력 수집(stderr 끝·실패한 도구 호출만), `TaskResult.environment`, `request.step_done`, 보고서·라운드 기록·request summary·snapshot, `labhq status`, 웹 카드, manual 절.
- 실행한 것: 전체 `pytest -q` 4068 passed·54 skipped(Windows), 바꾼 곳 test 367 passed·1 skipped(node 포함), 새 test 63개, `scripts/check_public.sh`.
- 미해결: 시설팀 직원(Codex read-only)·`facilities_fix` allowlist·자동 재개는 3단계.
- 근거: #35 PI 결정(10-06, A 내부 구조 먼저), `tests/fixtures/environment_signatures.yaml`.

### 리뷰 반영 (17:29, c3be2fa)

- 결론: 리뷰 지적 6건(P2)을 모두 고쳤다. 한국어 Windows 문구를 잡고, 인용만 한 줄·건너뛴 단계·이미 넘어간 실패 명령·timeout 같은 일시 오류는 환경 문제로 세지 않는다.
- 바뀐 것: 서명 표(cmd·PowerShell·.NET 한국어 문구, network·docker anchor), 매칭 전 인용 줄 제외(pytest `E `·`>`, `path:N:`, `logs/x.log:`), adapter는 마지막 실패 명령만 남기고 뒤에 성공한 셸 명령이 있으면 지움, stderr 서명은 timeout·429·5xx·runner restarted에 짐, `error_max_*`면 command 서명 무시, skipped 단계 제외, 보고서 Next는 빠진 산출물을 command 서명보다 먼저.
- 실행한 것: 전체 `pytest -q` 4096 passed·54 skipped, 바꾼 곳 test 511 passed, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: PR #447 리뷰 지적, `tests/test_environment_signatures.py`.

## 2026-10-06 · #420 — 새 topic 23개 점검표

- 결론: #420으로 채택한 topic 23개에 점검표 65항목을 넣었다(전체 43 topic·125항목). 리뷰 지적대로 계획 프롬프트에는 점검표 대신 작업 폴더 `topic_checklists.tsv` 이름만 싣는다(점검표 부분 10,400자 → 643자). 병합 전에 PI가 항목을 검토한다.
- 바뀐 것: `labhq/vocab/topic_checklists.yaml`, `docs/reference/topic_checklists_sources.md`(topic별 근거 한 줄), `topic_checklists.py`(TSV·짧은 규칙·누락 오류에 점검 내용), `cso.py`(계획·재계획 Task에 TSV), 러너 `workspace.py`·`daemon.py`(TASK.md 전에 TSV 쓰기), 점검표 test, README·README.en·manual.
- 실행한 것: 근거 PMID 87개 esummary 제목 대조(87 ok), 점검표·공개 자원 test 27 passed, 점검표·작업 폴더 관련 test 13개 파일 253 passed·32 skipped, 전체 suite 4010 passed·54 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 낡은 러너는 TSV를 쓰지 않지만, 그때도 답 누락 고침 요청이 점검 내용을 담는다.
- 근거: #420, PR #440, PR #446.

## 2026-10-06 · #435 C ① — 계획마다 공개 자원 목록

- 결론: PI 결정 C의 첫 단계. 모든 계획이 결과 종류별 공개 자원 21개의 이름을 참고로 받고, 조회 단계에 자원 판본을 기록하게 한다. 목록 밖 자원도 계획한다.
- 바뀐 것: `labhq/vocab/public_resources.tsv`·`.py`, `planning_guidance`, manual, package-data.
- 실행한 것: 새 test 3개, 오케스트레이터 관련 test 1890 passed·3 skipped, `scripts/check_public.sh`.
- 미해결: 조회 도구(labhq 주석 MCP: VEP·gnomAD·ClinVar, ChIP-Atlas·ENCODE·GTEx, AlphaGenome)는 Codex 레인.
- 근거: #435 PI 댓글(10-06 16:19).

## 2026-10-06 · #423 — 단계 입력 파일 SHA-256 기록

- 결론: 단계가 읽을 수 있던 외부 입력을 파일별 경로·크기·mtime·sha256으로 남기고 요청 묶음 `INPUTS.tsv`와 재현 등급 설명에 넣었습니다.
- 바뀐 것: PR #442. 산출 held-directory walker를 입력에도 쓰며 링크·junction·mount를 따라가지 않습니다. 파일당 2 GiB, 단계당 cache miss 20 GiB, 기존 항목·깊이 상한을 적용하고 `(실제 경로, 크기, mtime_ns)` cache를 runner 상태 폴더 한 파일에 둡니다. restricted·private은 파일을 열지 않고 생략 이유만 기록합니다.
- 실행한 것: 새 test 8건과 관련 test 192건 통과. Windows 전체 pytest 3996 passed·54 skipped, `scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: #423의 `rerun_verified` 영수증 경로와 벤치 C t6 재채점은 다음 작업입니다.
- 근거: `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `labhq/request_bundle.py`, `tests/test_input_hashes.py`, `docs/manual.md`.

## 2026-10-06 · 재개 대기 test의 시간 경쟁

- 결론: `test_resume_waits_for_runner_and_reoffers_after_timeout`가 0.15초 재개 대기를 첫 요청에도 써서 느린 CI에서 단언 전에 대기가 끝났다(10-06 Python 3.10·Windows에서 각 1번). 첫 요청은 30초 대기와 폴링, 시간 초과 검사만 0.15초.
- 바뀐 것: `tests/test_state.py` 한 test. 서버 코드는 그대로.
- 실행한 것: 고친 test 6회 연속 통과, `tests/test_state.py` 57 passed, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: GitHub Actions run 37404631861(3.10), 37411745645(Windows).

## 2026-10-06 · #420 — topic 23개 채택

- 결론: PI 결정 A대로 출처 3개 이상인 후보 23개를 어휘에 넣었다(topic 20 → 43). topic 상한은 없앴다(PI 10-05).
- 바뀐 것: output_types.yaml 23개, topic 상한 제거(로더·EDAM 생성기), EDAM 부분집합 재생성(새 topic 11개 연결, 전체 79키), 별칭 10개 정규화, 일반 lane 계획 프롬프트는 topic 이름만, README·영문 README·manual 수치.
- 실행한 것: 어휘·topic 관련 test 692 passed·1 skipped, `scripts/edam_subset.py --fetch`, `scripts/integrations.py --check`, `scripts/check_public.sh`.
- 미해결: 새 23개 topic의 점검표 항목(근거 문헌, PI 검토).
- 근거: `docs/reference/topic_candidates_420.tsv`, `labhq/vocab/output_types.yaml`.

## 2026-10-06 · #420 — topic 후보 조사 결과

- 결론: 출처 133개(논문 63·교과서 4·웹 자료 66)에서 새 topic 후보 38개를 찾았다. 출처가 많은 순으로 proteomics 14, de_novo_genome_assembly 11, single_cell_proteomics 10, gwas·multi_omics_integration 9, proteomics_dia·structural_variant_calling 8. PI 검토 전 후보다.
- 바뀐 것: `docs/reference/topic_candidates_420.tsv`·`_sources.tsv`·`.md`, 참고 기록 README 링크.
- 실행한 것: Codex DAG(luna 조사 4 + sol 병합, 18분), 출처 id 스크립트 확인(129 확인, 5 사이트 차단), 리뷰 뒤 같은 논문 합치기·topic 연결 하나 떼기와 재집계(`recount_topics.py`), `scripts/check_public.sh`.
- 미해결: PI가 채택할 후보를 고르면 `labhq/vocab/`에 넣고 점검표 항목을 붙인다. README의 "논문 N편" 문장은 그때 실제 수로.
- 근거: `docs/reference/topic_candidates_420.md`.

## 2026-10-06 · #373 — 보고서 본문 줄이기

- 결론: 벤치 C t6 보고서 본문 12,600자(Astra 3,100자) 중 약 2,000자가 `리뷰 참고` 두 벌이었다. 모델이 쓴 H1 절을 labhq가 지우지 못했다. 이 중복을 없애고 분량 기준을 프롬프트에 넣었다.
- 바뀐 것: 리뷰 참고 절은 제목 수준과 상관없이 하나, P2는 한 줄씩, P3는 건수 한 줄. 일반·연구 보고서 프롬프트에 본문 약 4,000자 목표·수치 한 번·방법 요약 8줄 안팎·한계는 결론을 바꿀 것만. manual 보고서 절.
- 실행한 것: 새 test 1건, 보고서 관련 test 50개 파일 1822 passed·6 skipped, `scripts/check_public.sh`.
- 미해결: 효과는 다음 벤치(t6 재실행)에서 본문 길이와 읽기 점수로 잰다.
- 근거: `labhq/orchestrator/cso.py` `_review_reference`·`_with_review_reference`·`SYNTH_PROMPT`.

## 2026-10-06 · #382 — 낡은 직원 Codex sandbox는 승인 창 대신 실행 전에 거부

- 결론: 한 PC의 elevated Codex 홈 둘이 공용 sandbox 계정 비밀번호를 번갈아 바꿔 승인 창(UAC)이 반복됐다(PI 결정 안 A, #435). labhq는 이제 직원 홈이 낡았는지 실행 전에 알아보고 Codex를 띄우지 않는다.
- 바뀐 것: 실행 전 점검(빈·깨진 setup_marker, sandbox_users.json 없음, 그 파일이 계정 비밀번호 변경 시각보다 오래됨), 첫 sandbox setup required에서 Codex 프로세스 트리 종료, doctor의 "다른 elevated 홈" warn, setup 안내의 한 홈 주의, manual.
- 실행한 것: 관련 test 125 passed, 어댑터 관련 50개 파일 1794 passed·34 skipped. 이 PC 실측으로 직원 홈 낡음·PI 홈 marker 빔·PI 홈 elevated를 모두 잡음. `scripts/check_public.sh`.
- 미해결: 개인 경로(`policy.private_paths`)를 Codex deny-read로 넘기기는 안 A 적용 뒤 canary 실측과 함께 별도 PR.
- 근거: `labhq/adapters/codex.py` `elevated_home_problem`·`sandbox_password_set`, `labhq/runner/codex_sandbox.py` `other_elevated_home`.

## 2026-10-06 · #421 — 규칙 없는 도구를 승인 게이트가 기본 허용하지 않기

- 결론: #316 검토의 P1 남은 몫. 승인 게이트는 셸·파일·MCP가 아닌 도구를 그냥 허용했다. 로컬 실행 기록 252개를 세어 보니 직원이 `Monitor`를 한 번 썼는데, 이 도구는 셸 명령을 돌리면서 Bash가 받는 통제 구역·개인 경로·위험 명령 검사를 거치지 않는다. 읽는 곳이 없던 `policy.approvals.auto_allow_tools`를 연결해, 목록에 없는 도구는 PI에게 묻는다.
- 바뀐 것: 기본 목록에 실측으로 확인한 `StructuredOutput`·`ToolSearch`를 더함. 목록 밖 도구는 ask(이유에 설정 키 이름). 매뉴얼 승인 게이트 절.
- 실행한 것: 도구 이름 실측(Bash 689·Read 652·PowerShell 496·shell 494·Write 318·Edit 290·web_search 157·pubmed MCP·StructuredOutput 126·ToolSearch 54·WebSearch 52·WebFetch 36·Monitor 1 등), 새 test 1건(Monitor는 ask, 목록 도구는 allow, 설정에 넣으면 allow), 전체 pytest 3961 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 처음 보는 도구가 밤에 쓰이면 PI 카드가 승인 시간 한도까지 단계를 붙잡는다. 멈춤 경고(#433)가 그 사이 알린다.
- 근거: `labhq/policy.py` `_evaluate_tool`, `labhq/settings.py` `_default_auto_allow`.

## 2026-10-06 · #382 — 조용히 멈춘 직원 CLI 경고

- 결론: 10-06 새벽 Codex가 UAC 승인 창을 기다리며 셸에서 무기한 멈췄는데 아무 신호가 없었다(#382 재발). 직원 CLI가 오래 조용하면 실행마다 한 번 경고하고, Windows에서 UAC 승인 창이 떠 있으면 그 원인을 alert로 알린다. 시설팀 설계안(#35)의 1단계와 같은 일이며 A/B 어느 쪽이든 필요하다.
- 바뀐 것: 프롬프트 stdin 쓰기를 timeout·감시 범위 안으로 옮김(봇 P2: stdin을 안 읽는 CLI가 둘 다 시작 전에 labhq를 붙잡음). `runner.stall_warn_s`(기본 600초, 0이면 끔). adapter 실행 루프가 stdout·stderr 마지막 출력 시각을 보고, 넘으면 작업 로그에 warn 한 줄, `consent.exe`가 떠 있으면 alert 한 줄. 긴 분석 명령도 조용하므로 끝내지는 않는다. 매뉴얼 안전 장치 절.
- 실행한 것: 새 test 5건(조용한 CLI에 경고 한 번, UAC 있으면 alert, 꺼짐·제때 출력이면 없음, stdin을 안 읽는 CLI도 timeout·경고 — 고치기 전 코드에선 무기한 대기 확인). 이 PC에서 실제 UAC 확인 함수가 True(대기 중인 승인 창 있음). 전체 pytest 3963 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 웹 alert 외 폰 알림은 없다. 시설팀(#35)이 붙으면 이 경고를 시설팀 호출로 잇는다.
- 근거: `labhq/adapters/base.py` `stall_watch`, `pending_uac_prompts`.

## 2026-10-06 · #423 — 환경 단계 없이 돈 스크립트 단계의 환경 기록

- 결론: 환경 단계 없이 runner의 기본 인터프리터로 스크립트를 돌린 단계는 환경 기록이 없어 요청 묶음이 `replayable`이 될 수 없었다(PR #431 등급의 남은 조각). 어떤 인터프리터를 썼는지는 직원만 알므로 runner가 추측해 쓰지 않고 단계 프롬프트에 규칙 한 줄을 더한다.
- 바뀐 것: 일반·연구 단계 프롬프트에 "스크립트를 돌렸으면 그 스크립트를 실행한 인터프리터로 판과 패키지 목록(`<인터프리터> --version`·`-m pip freeze`)을 `outputs/env/<단계 id>.txt`에 저장. 앞 환경 단계의 lock이 그대로 덮으면 생략, 직접 설치한 패키지(`./.pylib` 등)는 언제나 기록"(봇 P2 둘 반영). 매뉴얼 보고서 절.
- 실행한 것: 새 test 1건(두 프롬프트에 규칙), 프롬프트 고정 hash 3곳 갱신, 관련 test 245 passed, `scripts/check_public.sh`.
- 미해결: 입력(참고 폴더·PI 데이터) sha256 기록.
- 근거: `labhq/orchestrator/cso.py` `RESEARCH_STEP_PROMPT`.

## 2026-10-06 · #423 — 요청 묶음 재현 등급

- 결론: 요청 묶음이 `replayable`인지 `documented`인지 이유와 함께 적는다. `docs/research_protocol.md`가 요구한 구분인데 계산하는 코드가 없었다(#423, #316 검토).
- 바뀐 것: `replayable` = 기록 산출이 모두 복사됐고, 데이터를 낸 단계마다 스크립트가, 스크립트를 쓴 단계마다 자기나 앞 단계의 환경 기록(`outputs/env/`)이 있고, 복사한 스크립트에 절대경로가 없음. 하나라도 빠지면 `documented`와 이유. `rerun_verified`는 다른 곳에서 다시 돌린 기록이 있을 때만 주며 아직 쓰는 곳이 없다. 묶음 README "재현 등급" 절, 부록 한 줄, gateway `bundle_grade`(요청·`request.bundle` 이벤트), 2.5D·3D 웹의 묶음 경로 옆 표시(불완전한 묶음에도 — 봇 P2). 매뉴얼 요청 묶음 절.
- 실행한 것: 새 test 3건(앞 단계 환경 기록을 물려받은 묶음은 replayable·.md만 낸 단계는 스크립트를 묻지 않음, 스크립트·환경·절대경로가 빠지면 이유 셋과 documented, .txt 데이터도 스크립트를 요구 — 봇 P1) + gateway·웹 test 단언 추가. 전체 pytest 3954 passed(웹 표시 test 한 건은 고친 뒤 다시 통과), `scripts/check_public.sh`.
- 미해결: 스크립트 단계의 환경 기록은 ENV 단계가 있을 때만 생긴다. runner의 기본 Python으로 돈 단계는 `documented`로 남으니, runner가 패키지 목록을 자동으로 남길지 다음에 정한다. 입력(참고 폴더·PI 데이터) sha256 기록도 남았다.
- 근거: `labhq/request_bundle.py` `_grade`.

## 2026-10-06 · #423 — 앞 단계 입력을 inputs/로 잇고 보고하지 않은 산출도 묶음에

- 결론: 벤치 C t6의 재현성 결함 둘을 고친다(PI 10-06: inputs/ 방식, Claude 구현). 직원 스크립트가 앞 단계 파일을 runner 절대경로로 나눠 적어 묶음에서 안 돌았고, 스크립트가 읽던 미보고 파일(gene-set 사본·그림 등)이 묶음에 없었다.
- 바뀐 것: runner가 실행 전에 단계 폴더 `inputs/<앞 단계 id>`를 앞 단계 `outputs/`에 링크한다(POSIX symlink, Windows junction; 남은 링크는 새로 걸고 진짜 폴더는 건드리지 않음). CSO가 주는 앞 단계 파일 목록은 `inputs/<id>/...` 상대경로이고, 단계 프롬프트는 스크립트에 절대경로를 쓰지 말라고 한다. 링크를 못 걸면 이유와 원래 경로를 프롬프트에 적는다. 링크는 작업 폴더와 `inputs/`를 HeldDir로 잡은 채 바꾸고, 항목을 옆 이름으로 옮긴 뒤 링크일 때만 지운다(봇 P1·P2). 링크 이름 규칙은 요청 묶음의 단계 폴더 규칙(128자)과 같고, 규칙 밖 id는 프롬프트에 원래 경로와 함께 적힌다(봇 P2). 공유 계정 HPC 모드(`hpc.submit_prefix`)에서는 링크를 만들지 않고 원래 경로를 준다. 잡이 `hpc_out/`에서 다른 계정으로 돌고 제출 검사가 링크를 모두 거부하기 때문이다(봇 P1 셋을 이 한 규칙으로 닫음, HPC 제출 검사는 main과 같음). 요청 묶음은 기록된 산출을 다 복사한 뒤 남은 상한 안에서 보고하지 않은 산출을 `copied (unreported)`로 복사하고(빠져도 incomplete 아님), 의존이 있으면 `link_inputs.py`와 README 첫 명령을 넣는다.
- 실행한 것: 새 test 9건 — 링크로 읽기·낡은 링크 교체·진짜 폴더 보존, 열리지 않은 앞 단계 안내, 미보고 산출 순서·상한, 묶음을 옮긴 뒤 `link_inputs.py`로 스크립트 재실행, 경합 중 inputs 교체, relink 링크·폴더, relink 일반 파일, 공유 계정 HPC 모드. 프롬프트 고정 hash 3곳 갱신. 전체 pytest 3953 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: #423의 재현 등급(documented/replayable/rerun_verified)·환경 lock hash·입력 sha256은 다음 PR. 공유 계정 HPC 모드는 링크 대신 원래 경로를 쓰고, 그 잡이 private인 앞 단계 폴더를 못 읽는 문제(main에도 있음)는 #430.
- 근거: `labhq/runner/workspace.py` `link_inputs`, `labhq/runner/daemon.py`, `labhq/request_bundle.py`, `labhq/orchestrator/cso.py`.

## 2026-10-06 · README — 첫 문장·bioinfo-agent 소개와 영문 README

- 결론: PI 요청(10-06). README 첫 문장에 "멀티 에이전트 오케스트레이션 플랫폼"을 합치고, PI가 만든 bioinfo-agent를 링크와 한 줄 설명으로 소개하고, 영문 README를 더한다.
- 바뀐 것: `README.md` 첫 문장·언어 전환 링크·bioinfo-agent 줄. `README.en.md`(전체 번역, 버튼·탭은 실제 한국어 UI 이름에 영문 병기, 웹 UI가 한국어뿐이라고 명시)와 영문 그림 6개(`docs/media/en/`, `docs/media/why/en/`). `scripts/integrations.py`가 영문 배지 블록도 같은 블록에서 만들고 검사하며, 번역표에 없는 한국어 구가 남으면 실패한다(PR #428 리뷰). `scripts/ci_skip.py`는 README.en.md만 바뀐 커밋도 test_integrations만 돌린다.
- 실행한 것: Edge headless로 영문 그림 6개 렌더 확인(넘침 없음), `scripts/integrations.py --check`, `tests/test_integrations.py`·`tests/test_ci_skip.py` 40 passed, `scripts/check_public.sh`.
- 미해결: README의 "단어장·점검표를 N편 논문·교과서·웹 자료로 정립" 문장은 #420 조사 뒤 실제 수로 넣는다.
- 근거: `README.en.md`, `scripts/integrations.py`.

## 2026-10-05 · #373 — 해당 없는 점검표 항목을 보고서 한계에서 빼기

- 결론: 벤치 C에서 labhq는 네 과제 모두 정확성은 동점이었지만 읽기에서 졌다. 문헌 표(t4)와 정규화 점검(t5) 보고서가 요청과 무관한 `batch`·`pairing`·`gene_set_test`·`independent_validation`을 한계로 길게 늘어놨다. `not_applicable` 답은 이제 한계가 아니고 보고서 본문에서 빠진다.
- 바뀐 것: `limitations()`는 `assumption` 답만 돌려주고, 새 `not_applicable()`이 해당 없는 항목 id를 준다. 보고서 문맥은 그 id를 "본문에서 빼라"는 한 줄로 넘긴다. 점검표 규칙은 "해당하지만 못 한 점검은 `assumption: <이유>`"로 답하게 한다(그래야 한계로 남는다). 보고서 프롬프트 두 곳과 매뉴얼 점검표 절. README "아직 지는 곳"을 벤치 C 수치로 갱신했다.
- 실행한 것: 점검표 test 두 곳을 새 기준으로 정확히 단언하게 바꿨다(한계는 assumption만, not_applicable은 따로, 보고서 문맥에서 한계 부분에 없음). 전체 pytest 3944 passed·54 skipped, `scripts/check_public.sh`.
- 봇 P2: 해당 없음 목록에 계획이 요구하지 않는 key(남은 topic의 항목, 임의 key)까지 들어가면 필요한 설명을 본문에서 뺄 수 있었다. 현재 계획의 필수 항목(`required_checklist_items`)만 남기게 했다. README 큰 과제 수치는 가림 오류를 고친 t6 재채점(22:28)으로 바꿨다.
- 미해결: 보고서·요약·QC 반복, 묶음 안 링크 깨짐은 #423·후속에서.
- 근거: `labhq/vocab/topic_checklists.py`, `labhq/orchestrator/cso.py` `plan_report_context`.

## 2026-10-05 · README — "무엇이 다른가"를 장점 네 꼭지 카드와 비교 절로

- 결론: PI 요청(10-05 채팅). 옛 절은 표가 밋밋하고 기능 나열처럼 읽혔으며, 좁은 첫 칸에서 "근거/등급"처럼 단어가 잘렸다. 장점 네 꼭지를 그림 카드로 만들고, 다른 방식과의 비교는 따로 뒀다.
- 바뀐 것:
  - "labhq가 공들인 네 가지": 틀린 근거를 미리 거른다(The Virtual Biotech claim 장부, 다른 회사 리뷰어, `labhq verify`) · 논문에서 출발한다(선행 연구 기준, Paper2Agent 파견직) · 일을 넘겨도 기준은 같다(topic 어휘·EDAM, 점검표) · 끊겨도 잇고, 묶어서 남긴다(재개, 요청 묶음). 카드는 `docs/media/why/card-1..4.svg`(480×260, 자체 배경이라 밝은·어두운 테마 모두). 카드마다 한 줄 설명과 접는 "근거와 한계"(PR·이슈·시운전 근거와 미측정·기본 꺼짐 같은 한계).
  - "단독 AI 세션·연구 workbench와 비교": 7줄 표(✅/⚠️/❌, 칸은 한 줄), workbench 칸은 #316 open-science 검토 근거, 리뷰어 범위 주석, labhq가 맞는 경우와 아직 지는 곳.
  - 넷째 꼭지는 보안 대신 이어 가기·묶음을 골랐다. open-science와 견주면 labhq 경로 가드는 OS sandbox가 아니라 "더 안전하다"고 쓸 수 없기 때문이다(#316). 승인은 비교표 줄로 남겼다.
- 실행한 것: 근거 6갈래·문안·디자인 두 방향·비평 3관점 Workflow로 만들었다. GitHub markdown API로 `td width`·`valign`·`<details>`가 남는 것을 확인했고, GitHub 스타일로 렌더해 줄 잘림이 없는지 봤다(비교표 "사람의 결정" 잘림 → "승인"). README 9,734 → 13,766자(절 1,180 → 5,212자, 근거와 한계는 접어 둠).
- 미해결: 벤치 C 결과가 나오면 "아직 지는 곳"의 수치를 고친다.
- 근거: `README.md`, `docs/media/why/`.

## 2026-10-05 · #421 — 직원 세션의 여러 agent 도구와 Codex 기억 끄기

- 결론: #316 open-science 검토에서 나온 P1. 승인 게이트는 셸·파일·MCP가 아닌 도구를 그냥 허용해, 직원이 `Workflow`·`TeamCreate`로 labhq가 승인·집계하지 못하는 agent를 띄울 수 있었다. Codex 쓰기 직원은 `multi_agent`·`memories`가 켜진 채로 돌아, 하위 agent 비용이 단계 집계 밖으로 새고 공용 staff CODEX_HOME에서 다른 요청의 기억이 섞일 수 있었다.
- 바뀐 것: Claude 직원의 거부 규칙에 `Workflow`·`TeamCreate`·`TeamDelete`를 더했다(`SendMessage`·`ListAgents`와 같은 `--settings` 경로). 하위 agent(`Agent`, 옛 이름 `Task`)는 `tools`에 적은 직원(recruiter)만 남기고 나머지는 거부한다(PR #425 봇 P1). Codex는 쓰기·읽기 전용 모두 `--disable multi_agent --disable memories`를 건다(이 PC의 codex-cli `codex features list`로 이름 확인). 매뉴얼 안전 장치 절.
- 실행한 것: 거부 목록·Codex 플래그를 정확히 단언하도록 test 5곳을 바꿨다(쓰기 실행은 두 기능만 끄고 읽기 전용 묶음은 켜지 않음을 확인). 전체 pytest 3943 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: `policy.approvals.auto_allow_tools`는 여전히 읽는 곳이 없다. 연결하면 `StructuredOutput`·`ToolSearch` 같은 무해한 도구에도 PI 카드가 뜰 수 있어(trial 기록 760건 중 실제 사용 확인), 허용 목록을 실측으로 정한 뒤 #421에서 다룬다.
- 근거: `labhq/adapters/claude_code.py`, `labhq/adapters/codex.py`.

## 2026-10-05 · 문서 — 매뉴얼 로드맵에서 끝난 #84 줄 지움

- 결론: "버전에 묶인 장기 과제"에 남아 있던 v0.5 직원·CSO 프롬프트 규칙 묶음(#84)은 반영돼 닫혔다(실패한 조회·방법 낮춤 규칙이 계획·단계 prompt에 있음). 줄을 지웠다.
- 바뀐 것: `docs/manual.md` 한 줄.
- 실행한 것: `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`의 "neither evidence nor proof of absence" 규칙.

## 2026-10-05 · #298 — PI 문답에 10-04·10-05 질문 셋

- 결론: PI가 10-04·10-05에 물은 것 셋(문헌조사와 원 연구 Methods, Codex 한도 중 Claude 작업, Claude 직원 로그인 만료)을 `docs/pi-qa.md`에 답과 함께 넣었다.
- 바뀐 것: `docs/pi-qa.md` 8,258 → 9,946자.
- 실행한 것: `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `docs/pi-qa.md`.

## 2026-10-05 · 운영 — 그림자 제거 검사의 실패 진단

- 결론: `test_semantics_shadow_remove`가 Windows에서 가끔 실패하는데(오늘 6번 중 1번 등), 실패 메시지가 안쪽 pytest 출력의 마지막 3,000자뿐이라 faulthandler 스택만 보이고 어느 test인지 알 수 없었다. 이제 실패한 test 줄과 덤프 머리 줄을 먼저 보인다. 자식 출력은 UTF-8인데 cp949로 읽어 reader thread가 깨지던 것도 고쳤다.
- 바뀐 것: `scripts/semantics_shadow_remove.py`(subprocess 세 곳 `encoding="utf-8", errors="replace"`, `_ok` 메시지).
- 실행한 것: 새 test(긴 꼬리 위에 실패 줄과 덤프 머리)와 `tests/test_semantics_shadow_remove.py` 전체 통과, `scripts/check_public.sh`.
- 미해결: 간헐 실패의 원인 자체는 다음 실패 때 이 메시지로 찾는다.
- 근거: `scripts/semantics_shadow_remove.py`.

## 2026-10-05 · #57 후속 — 직원 시트의 쓴 비용

- 결론: #57 ⑦에서 뺀 직원별 누적 비용을 넣었다. 직원 상세 시트가 화면에 있는 요청들의 `by_agent`(#415)를 더해 "확인 $0.50 + 추정 $0.30 (요청 2건)"처럼 보여 준다.
- 바뀐 것: `labhq/web/state.js`(`agentSpentLabel`), 시트 capability card, 매뉴얼 2.5D 절.
- 실행한 것: `tests/web_cost_by_agent.cjs`에 합산·빈 경우 단언 추가. web test 통과, `scripts/check_public.sh`.
- 미해결: 화면에 없는 오래된 요청은 합산하지 않는다(gateway가 보낸 요청만).
- 근거: `labhq/web/state.js`.

## 2026-10-05 · #57 후속 — 요청 비용을 직원별로

- 결론: 요청 비용 요약이 엔진별로만 나뉘어 누가 얼마를 썼는지 알 수 없었다(#57 ⑦에서 뺀 항목). 이제 비용 항목에 직원 ID를 남기고 요약에 `by_agent`를 더해, 웹 요청 줄에 "부엉이 확인 $0.40 · 너구리 추정 $0.10"처럼 보인다.
- 바뀐 것: `labhq/costs.py`(`task_cost_item`·`outcome_unknown_item`이 `agent_id`를 담고, `aggregate_costs`가 직원별 소계를 낸다. 직원 ID가 하나도 없는 예전 기록은 요약 모양이 그대로다), `labhq/web/state.js`(`agentCostLabel`), 요청 줄, 매뉴얼.
- 실행한 것: `tests/test_cost_accounting.py`에 직원별 소계·예전 기록 test, 새 `tests/web_cost_by_agent.cjs`(CI 목록). 전체 pytest 3933 passed·1 failed·54 skipped. 실패 1건(`test_semantics_shadow_remove` 안의 재실행)은 단독으로 두 번 돌려 둘 다 통과한 간헐 실패, `scripts/check_public.sh`.
- 미해결: 여러 요청을 가로지르는 직원별 누적은 아직 없다.
- 근거: `labhq/costs.py`, `labhq/web/state.js`.

## 2026-10-05 · #59 ② — 실행 중 폭주 감지(그림자)

- 결론: Codex·Antigravity에는 max_turns나 예산 상한이 없어, 같은 명령을 되풀이하는 직원을 막는 것이 task timeout뿐이었다. runner가 이제 직원 이벤트를 보고 루프를 알린다. 그림자 단계라 멈추지 않는다.
- 바뀐 것: `labhq/runner/breaker.py`(부수효과 없는 감지기: 같은 도구·입력 8번 연속, 실패한 도구 호출 5번 연속, 신호마다 task당 한 번). runner의 task `emit`이 `agent.breaker` 이벤트와 피드 경고(`agent.log` alert)를 낸다. 엔진은 성공을 알리지 않으므로 오류 없이 지나간 도구 호출이 실패 연속을 끊는다. 매뉴얼 로드맵.
- 실행한 것: `tests/test_breaker.py` 5건(반복·입력 변경·실패 연속과 끊김·오류 줄 여러 개, runner에서 경고 뒤에도 task가 정상 종료). 전체 pytest 3937 passed·54 skipped(첫 실행의 1건 실패는 다시 나오지 않았고 이름을 잡지 못함).
- 봇 P2: Codex MCP 호출이 인자 없이 기록돼 서로 다른 검색도 같은 반복으로 셀 수 있었다. adapter가 인자를 정렬한 짧은 JSON으로 넘기고, 입력이 없는 호출은 반복으로 세지 않는다. test 2건 추가.
- 미해결: 멈추기(steer → 제한 → stop), 작업 폴더·HPC 진행 없음 신호, hook 제어(steer·pause·halt)는 #59에 남는다.
- 근거: `labhq/runner/breaker.py`, `tests/test_breaker.py`.

## 2026-10-05 · 운영 — 패치노트 검사가 숫자로 읽힌 sha를 알려 줌

- 결론: 손으로 쓴 행의 짧은 sha가 숫자로만 되어 있으면(`7999708`) YAML이 정수로 읽어 검사가 "커밋이 없다"고만 했다(PR #410). 이제 그 행을 짚어 따옴표로 감싸라고 알려 준다.
- 바뀐 것: `scripts/patch_notes.py` `entry_shas()`. `rows` 명령은 원래 `yaml.safe_dump`으로 따옴표를 붙이므로 그대로다.
- 실행한 것: 새 test가 수정 전 실패, 수정 뒤 통과. `tests/test_patch_notes.py` 17 passed, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: `scripts/patch_notes.py`.

## 2026-10-05 · #57 ⑧ — 사무실 소품을 탭 입구로

- 결론: 사무실 칠판·HPC 랙·문을 누르거나 키보드로 고르면 작업판·HPC·메신저 탭이 열린다. 결정이 기다리면 칠판이 깜박이고(움직임 줄이기 설정이면 테두리 색만) 결정 탭으로 안내한다.
- 바뀐 것: `labhq/web/index.html`(소품의 `data-tab-link`·role·tabindex, `initTabShell`이 돌려주는 탭 전환 함수 사용, 좁은 화면에서는 Command Center로 스크롤, `renderApprovals`가 칠판 상태를 바꿈), 매뉴얼 2.5D 절.
- 실행한 것: 새 `tests/web_office_props.cjs`를 CI 목록에 넣었다. 데모에서 랙→HPC, 문→메신저, 결정 2건 대기 중 칠판 깜박임→결정, Enter 키를 확인했다. web test 48 passed, `scripts/check_public.sh`.
- 미해결: 배정 때 서류 애니메이션은 넣지 않았다.
- 근거: `labhq/web/index.html`, `tests/web_office_props.cjs`.

## 2026-10-05 · #373 — README에 분석 점검표·선행 연구

- 결론: README "무엇이 다른가" 표에 topic 점검표와 선행 연구 기준(#395·#400)이 없었다. PI가 요청한 기능이라 한 행을 더했다.
- 바뀐 것: `README.md` 한 행. 15,200 → 15,524자.
- 실행한 것: `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `README.md`.

## 2026-10-05 · #57 ⑦ — 직원 상세 시트의 capability card

- 결론: 직원 시트가 엔진·모델·소속만 보여 줬다. 이제 권한, 추론 강도, 같은 세션 이어 쓰기, 읽기 전용 상담 가능 여부, 붙은 MCP를 보여 준다. 값은 gateway가 runner의 roster와 엔진 adapter에서 가져온 것이고 웹은 추측하지 않는다.
- 바뀐 것: `Hub._agent_capabilities()`가 runner 등록 때 직원마다 `capabilities`를 붙여 snapshot·roster 이벤트로 나간다. `supports_resume`는 같은 판정(`_agent_resumes`)을 쓴다. 권한은 Codex면 `sandbox`, 그 밖은 `permission_mode`다. roster 요약에 `effort`를 더했다. 웹 시트·데모 roster·매뉴얼 2.5D 절.
- 실행한 것: `tests/test_agent_capabilities.py`(Claude·Codex·CLI 직원 card), `tests/web_agent_capabilities.cjs`(snapshot·roster 갱신, 시트 항목)를 CI 목록에 넣었다. 데모에서 엔지니어 시트를 열어 확인했다. 전체 pytest 3929 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 직원별 누적 비용·토큰은 지금 직원 단위로 모으는 곳이 없어 넣지 않았다. #57 ⑧(사무실 소품 탭 입구)이 남는다.
- 근거: `labhq/gateway/server.py`, `labhq/web/index.html`.

## 2026-10-05 · #57 ⑥ — 직원별 생각·디버그 기록

- 결론: 웹은 엔진이 보내는 `thinking`·`debug` 로그를 받자마자 버렸다. 이제 직원마다 최근 60줄을 따로 보관하고, 직원 상세 시트에서 **활동 / 생각·디버그**로 골라 본다. 피드·말풍선·활동 목록에는 여전히 넣지 않는다.
- 바뀐 것: `labhq/web/state.js`(`traceTo` ring buffer, 재생 상태 비교에 들지 않는 비열거 속성이라 roster 갱신 뒤에도 유지), `labhq/web/index.html`(시트 전환 버튼, 데모 각본에 생각 두 줄), 매뉴얼 2.5D 절.
- 실행한 것: 새 `tests/web_agent_trace.cjs`(보관·경계·피드 제외·roster 갱신 뒤 유지)를 CI 목록에 넣었다. 기존 재생 상태 hash test(`web_state.cjs`)는 그대로 통과한다. 데모 페이지에서 분석가 시트를 열어 생각 줄이 보이는 것을 확인했다. web test 57 passed.
- 미해결: #57 ⑦ capability card, ⑧ 사무실 소품 탭 입구.
- 근거: `labhq/web/state.js`, `tests/web_agent_trace.cjs`.

## 2026-10-05 · #369 — bulk pack @3: 짝의 근거는 메타데이터 출처 목록에서만

- 결론: #369 2번. `bulk_tumor_normal@2`의 `pairing_evidence`는 자유 문자열이라 "발현 상관으로 짝 추정"도 통과했다. `@3`은 메타데이터 출처 목록에서만 고르고, 짝이 있다고 하면(`partial`·`complete`) 출처를 하나 이상 요구한다. #369의 세 항목(1번 #390, 2번 이 PR, P2 #403·#406)이 모두 닫힌다.
- 바뀐 것: `labhq/research/packs/bulk_tumor_normal_v3.yaml`(v2 + `pairing_evidence` pattern, 규칙 `bulk_tumor_normal.pairing_from_metadata`, fixture 하나). `@1`·`@2`는 그대로라 승인된 요청은 저장된 version과 hash로 재개한다. 설정 예시·매뉴얼·`docs/research_protocol.md` 6절을 `@3`으로.
- 실행한 것: `@3` test 9건(허용 4·pattern 거부 3·규칙 거부 2)과 `@2`의 자유 문자열 유지 test, 내장 pack hash 고정에 `@3` 추가. 전체 pytest 3926 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 없음. 운영 설정의 `active_packs`는 비어 있어(연구 lane 꺼짐) 바꿀 것이 없다.
- 근거: `labhq/research/packs/bulk_tumor_normal_v3.yaml`, `tests/test_research_bulk_pack.py`.

## 2026-10-05 · #369 — pack 조합 표의 core PLAN field 칸도 schema로 검사

- 결론: PR #403 봇 P2. 조합 표에 core PLAN field(`brief.study_type`, `protocol.statistics.applicable` 등)가 들어가면 그 칸은 검사 없이 load됐다. PLAN schema에 없는 값(`"bogus"`, bool 자리의 `1`, list field의 문자열)은 어떤 PLAN과도 맞지 않는 행이다. 이제 load에서 거부한다.
- 바뀐 것: `plan_field_problem()`이 칸의 path를 `ResearchPlan` schema로 따라가 그 field type에 strict하게 맞는지 본다. `contract.py`가 `packs.py`를 import하므로 schema는 검사할 때 늦게 가져온다. 선택(`| None`) field의 `null`은 PLAN dump의 값과 같아 허용한다. 문서: `docs/research_protocol.md` 6절, 매뉴얼 연구 lane 절.
- 실행한 것: 새 test 6건 중 거부 5건이 옛 검사에서 실패하고 이 branch에서 통과했다. 남은 1건은 맞는 칸이 계속 load되는지 확인한다. 전체 pytest 3912 passed·54 skipped, `scripts/check_public.sh`.
- 봇 P2: Field 제약(`ge=1`, `min_length=1`)은 annotation이 아니라 metadata에 있어 함께 검사한다. `protocol.revision: 0`, `brief.subject: ""` test가 수정 전 실패, 수정 뒤 통과.
- 미해결: 없음.
- 근거: `labhq/research/packs.py`, `tests/test_research_bulk_pack.py`.

## 2026-10-05 · #373 — HANDOFF 작업 큐와 PI 결정을 10-05 기준으로

- 결론: 작업 큐가 10-03 기준이라 v0.25 선언과 #373 후속이 빠져 있었다. 총괄이 시작할 때 읽는 표를 지금 상태로 맞췄다.
- 바뀐 것: 작업 큐에 v0.25 선언(#379)과 #373 진행·남은 것(벤치 C, #402), #382 실측 행을 더하고, 끝난 README 분리 행을 뺐다. PI 결정에 10-04·10-05 결정 3건(단독 라우팅 안 A, 어휘 100키, 원 연구 Methods는 참고·갈래별 분석은 선행 연구)을 더했다. 12,070자 → 12,803자.
- 실행한 것: `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `HANDOFF.md`.

## 2026-10-05 · #373 — 로그인 대기의 turn 키를 상담 ask마다 따로

- 결론: 같은 request에서 서로 다른 직원에게 보낸 상담은 병렬로 돌지만 둘 다 `kind="consult"`라 한 turn으로 묶였다. 먼저 끝난 상담이 로그인 창을 지워 남은 상담이 24시간 상한을 새로 시작했고, 두 대기 기록도 서로 덮어썼다. PR #401 봇 3차 P1로, 병합 때 남긴 것이다.
- 바뀐 것: `turn_key()` 하나가 turn을 가리킨다. 단계는 `step_id`, 그 밖은 `kind`이고, 상담은 `kind:ask_id`다. 로그인 창의 turn 목록, 로그인·한도 대기 기록, 시도 횟수가 모두 이 키를 쓴다. 전에는 세 곳이 각자 식을 썼다.
- 실행한 것: 새 test(두 직원 상담이 같은 엔진 로그인을 기다림)가 옛 키에서 실패(대기 기록이 `consult` 하나로 덮임)하고 이 branch에서 통과했다. 로그인 대기 test 44건, 전체 pytest 3898 passed·54 skipped, `scripts/check_public.sh`.
- 봇 P2(업그레이드 때 대기 중이던 상담): 옛 창에 남은 `consult` 키는 그 상담이 빠질 때 함께 지운다. 새 test가 수정 전 실패, 수정 뒤 통과.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`(`turn_key`), `tests/test_login_wait.py`.

## 2026-10-05 · #373 — topic 점검표 17개 추가와 같은 id 점검 합치기

- 결론: 점검표가 없던 topic 17개에 항목 48개를 넣어, 승인된 topic 20개 모두 점검표가 있다. #298에 올린 추가안을 그대로 옮겼다. PI가 10-05 18:57에 확인했다(#298). 근거 문헌 29편은 PMID(1편은 DOI)를 확인해 참고 기록에 둔다.
- 바뀐 것: `labhq/vocab/topic_checklists.yaml`(17 topic). `requirements()`는 두 선언 topic이 같은 id에 다른 점검을 두면(ATAC·ChIP의 `library_qc`, ATAC·메틸화의 `differential_model`, bulk·단일세포의 `batch`) 뒤 점검을 버리지 않고 ` / `로 잇는다. 앞의 둘은 이 PR의 데이터로 생겼고, `batch`는 전부터 있던 경우다. 계획 프롬프트의 점검표 규칙은 PR #400대로 점검 문장만 싣는다(1,124자 → 4,614자). 근거는 `docs/reference/topic_checklists_sources.md`, 매뉴얼 점검표 절.
- 실행한 것: 새 test(같은 id 두 topic)가 수정 전 코드에서 실패하고 수정 뒤 통과했다. `atac_seq`에 점검표가 생겨 "점검표 없는 topic은 요구 없음" 단언을 없는 topic key로 옮기고, `atac_seq`는 자기 항목만 요구하는지 확인한다. 전체 pytest 3898 passed·54 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 봇 P1(10-05): somatic 점검표가 matched normal과 panel of normals를 같은 대안으로 다뤘다. PoN은 기술적 인공물만 거르므로, 생식계열 변이는 matched normal로, 없으면 집단 생식계열 자료로 거르고 남는 위험을 한계로 밝히게 나눴다. PoN은 인공 변이 필터 항목으로 옮겼다.
- 미해결: 없음.
- 근거: `labhq/vocab/topic_checklists.py`, `tests/test_topic_checklists_precedents.py`.

## 2026-10-05 · #369 — pack 조합 표의 칸을 PLAN 답과 같은 규칙으로 검사

- 결론: pack의 `allowed_combinations` 칸이 field의 type·`minimum`·`pattern`을 어기면 그 행은 어떤 PLAN도 통과할 수 없다. 이제 load에서 거부한다. PR #366 봇 P2(#369 남은 지적)다.
- 바뀐 것: `labhq/research/packs.py`에 `field_value_problem()`을 두어 PLAN 답 검사(`contract.py`)와 조합 표 칸 검사가 같은 판정을 쓴다. 전에는 칸을 `allowed_values`로만 봤다. `null` 칸도 거부한다(생략한 답은 `null`과 같지 않고, 명시한 `null`은 type 검사에서 떨어진다). 내장 pack 내용과 hash는 그대로다. `docs/research_protocol.md` 6절.
- 실행한 것: 새 test 9건 중 거부 8건이 옛 검사에서 실패하고 이 branch에서 통과했다. 통과 1건은 맞는 칸이 계속 load되는지 확인한다. PLAN 답 오류 문구는 그대로라 기존 test 기대값을 바꾸지 않았다. 전체 pytest 3905 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 봇 P2 — core PLAN field(`brief.study_type` 등)의 칸은 PLAN schema로 검사하지 않는다(남은 지적). #369 1번(적용 pack의 `not_applicable` 우회)은 #390에서 topic 판정으로 닫혔다. 2번 `pairing_evidence` 닫힌 선택지는 pack version을 올려야 해 이 PR에 넣지 않았다.
- 근거: `labhq/research/packs.py`, `labhq/research/contract.py`, `tests/test_research_bulk_pack.py`.

# 엔진 로그인 대기와 이어서 실행 (#373)

## 단계
P1 — 로그인 만료를 요청 실패 대신 엔진별 `waiting_login`으로 주차하고, 로그인 뒤 멈춘 turn부터 재개합니다.

## 한 일
- Claude Code·Codex의 확인된 로그인 오류만 보수적으로 분류합니다.
- 같은 엔진의 단계는 대기를 공유하며, 10분마다 재시도하고 24시간 뒤 실패합니다.
- 브리핑부터 최종 보고·단독 처리·이어 묻기까지 모든 CSO turn에 같은 대기를 적용했습니다.
- 웹·3D·CLI에서 로그인 명령과 수동 재개를 제공하고, 재시작 뒤에도 대기와 마감을 잇습니다.
- CLI 명령은 runner OS와 펼친 직원 경로를 따르며, 단계 이벤트와 새 watch snapshot에도 한 번만 나옵니다.

## 테스트 결과
- [x] 관련 pytest 254건, 취소 회귀 20회 반복 통과
- [x] Windows `pytest -q`: 3873 passed, 54 skipped
- [x] Node CJS 22개와 `scripts/check_public.sh` 통과
- [x] WSL Python 3.10 `py_compile` 통과
- [x] manual: 74,227자 → 74,306자

## 기존 테스트 변경
- 요청 활성 상태·운영 요약·웹 상태 기대에 `waiting_login`을 추가했습니다.
- 기존 `resume-quota` 경로를 로그인 대기에도 쓰는 CLI·웹 계약을 검증합니다.
- 이번 수정은 PR에서 새로 만든 취소 test의 event-loop 순서만 결정적으로 바꿨습니다.

## 막힌 점
WSL에는 pytest·FastAPI가 없어 Linux suite는 CI에서 확인합니다.

# topic 점검표와 선행 연구 기준 (#373 #375)

## 단계
P1 — 계획·리뷰·보고서에 topic별 점검표와 선행 연구의 필수·권장 분석을 연결했습니다.

## 한 일
- 공개 데이터 재분석 브리핑은 원 연구 본문에서 Methods 선택을 5줄 안에 요약합니다.
- 문헌 담당은 브리핑과 병렬로 최근 논문 2–4편을 조사하며, 결과는 요청 상태에 저장합니다.
- bulk RNA-seq·microarray·single-cell 점검표를 PLAN 답, 리뷰, 한계와 단독 처리에 전달합니다.
- 일반 lane은 누락 답을 한 번 고친 뒤 경고하고, 연구 lane은 PLAN 검증 오류로 멈춥니다.

## 테스트 결과
- [x] Windows `pytest -q`: 3830 passed, 54 skipped
- [x] Node CJS 22개와 `scripts/check_public.sh` 통과
- [x] manual: 72,630자 → 73,642자

## 기존 테스트 변경
- 연구 PLAN fixture에 새 점검표 답을 넣고, 추가 schema·prompt hash와 재시작 담당 기대를 갱신했습니다.
- 문헌 담당 부재 경고와 Windows 명령 길이에 따라 달라지는 TASK.md 경로 기대를 새 계약에 맞췄습니다.

## 막힌 점
없음.

# 직원별 effort와 단독 이어 묻기 (#373)

## 단계
P1 — 직원별 추론 강도와 단독 처리 담당 세션 보존을 구현했습니다.

## 한 일
- Claude Code·Codex 직원 YAML에 선택 필드 `effort`를 추가하고 엔진별 값을 검증합니다.
- 단독 성공 요청의 이어 묻기는 CSO가 꺼져 있어도 단독 직원의 세션·작업 폴더로 갑니다.
- 단독 실패 뒤 팀 폴백은 기존처럼 CSO가 이어 묻기를 맡습니다.
- manual 예시는 전역 `extra_args` 대신 단독 직원의 `effort: ultra`를 씁니다.

## 테스트 결과
- [x] 관련 pytest: 96 passed
- [x] Windows `pytest -q`: 3767 passed, 53 skipped
- [x] Node CJS 20개 통과
- [x] `scripts/check_public.sh` 통과
- [x] manual: 71,292자 → 71,165자

## 막힌 점
없음.

# 요청 묶음 4차 P1 구조 수정 (#373)

## 단계
P1 — 산출 폴더 순회를 없애고 runner 기록만 복사하도록 단순화했습니다.

## 한 일
- `outputs ∩ output_sha256` 파일만 no-follow handle로 열어 hash를 확인하고 복사합니다.
- 누락·변경·상한 제외를 모두 manifest에 적고 묶음과 이벤트를 `incomplete`로 표시합니다.
- workdir 치환은 경로 경계에서만 적용하고 POSIX 대소문자를 구분합니다.
- 공용 runner walker는 main 상태로 되돌렸습니다.

## 테스트 결과
- [x] 수정 전 회귀 5건 실패, 수정 후 통과
- [x] 관련 test 53 passed, 4 skipped
- [x] `pytest -q`: 3756 passed, 54 skipped
- [x] Node CJS 20개와 `scripts/check_public.sh` 통과

## 기존 테스트 변경
기존 runner test 기대값은 그대로 두고, 이 PR의 요청 묶음 test만 기록 산출 구조에 맞췄습니다.

## 막힌 점
없음.

# 어휘 100키와 topic 기반 pack 판정 (#375)

## 결론
PI가 승인한 59키를 더하고, 자유 문장 대신 계획의 topic으로 pack 적용 여부를 판정합니다.

## 변경
- 어휘는 data 30, format 28, operation 22, topic 20으로 모두 100키입니다.
- branch별 상한은 36/32/24/24, EDAM 고유 term 상한은 116입니다.
- 일반 계획은 topic 정의를, 연구 계획은 짧은 승인 키 목록을 prompt에 싣습니다.
- 적용 pack과 근거 topic, 빈 topic 경고를 계획·감사 기록·CP1 카드에 남깁니다.
- `bulk_tumor_normal@1`은 그대로 보존하고 새 요청 예시는 topic 조건이 있는 `@2`를 씁니다.
- 재개 요청은 CP1에 저장된 pack version·hash로 검증합니다.
- 고정 EDAM subset에 정확히 있는 term만 연결했습니다.

## 검증
- Windows `pytest -q`: 3768 passed, 53 skipped
- WSL Python 3.10 Linux CI 회귀 테스트: 2 passed
- Node CJS 20개 통과
- `scripts/check_public.sh` 통과

## 기존 테스트 변경
- 승인 어휘 수·branch 상한·EDAM subset 기대값을 새 계약에 맞췄습니다.
- 계획 schema·prompt 고정 hash와 fixture에 `topics`를 반영했습니다.
- bulk pack 적용 테스트를 자유 문장 판정에서 topic 교집합 판정으로 바꿨습니다.

## 막힘
없음.

# 요청 묶음 2차 P1 보강 (#373)

## 단계
P1 — runner와 묶음의 순회 판정을 합치고 재실행 명령·요청별 상한을 보강했습니다.

## 한 일
- 묶음이 runner의 held-descriptor walker를 써서 link·junction·mount·device 변경·통제 구역을 제외합니다.
- 안전한 이름의 script만 DAG 위상 순서로 README 명령에 싣습니다.
- 요청별 2,048MB·5,000파일 상한을 두고 첫 제외 파일을 manifest와 부록에 기록합니다.

## 테스트 결과
- [x] 수정 전 회귀 5건 실패, 수정 후 통과
- [x] 관련 test 85 passed, 21 skipped
- [x] `pytest -q`: 3749 passed, 53 skipped
- [x] Node CJS 20개와 `scripts/check_public.sh` 통과

## 기존 테스트 변경
없음.

## 막힌 점
없음.

# 작은 요청 단독 처리와 팀 폴백 (#373 안 A)

## 단계
P1 — 일반 lane의 작은 요청을 설정한 직원 한 명에게 보내는 자동 경로를 구현했습니다.

## 한 일
- 계획의 선택 필드 `route`와 `solo_agent`·`solo_review` 설정을 추가했습니다.
- 기존 direct 실행·산출물 수집·질문 재개를 재사용하고 실패하면 저장된 팀 계획으로 이어갑니다.
- API·CLI·2.5D에서 팀을 강제할 수 있고, 2.5D·3D·CLI에 처리 방식을 표시합니다.
- 단독 결과·비용·산출 경로·메모·폴백을 저장 상태와 최종 보고서에 남깁니다.

## 테스트 결과
- [x] 수정 전 새 Python 테스트 수집 실패 확인
- [x] `pytest -q`: 3754 passed, 53 skipped
- [x] Node CJS 20개 통과
- [x] `scripts/check_public.sh` 통과

## 봇 리뷰 대응
- 단독 turn 뒤 예산 승인이 거부되면 review나 성공 종결보다 실패 처리를 먼저 합니다.
- 재시작 때 팀 직원이 아닌 단독 담당 runner를 기다리고, 폴백 뒤에만 팀 직원을 기다립니다.
- 저장된 유효 review는 다시 실행하지 않고 verdict 처리부터 잇습니다.
- 저장 상태의 다음 단계를 한 함수로 판정해 실패·빈 결과·review 거부는 팀 runner를 기다린 뒤 폴백합니다.

## 기존 테스트 변경
- 일반 계획 schema·prompt 고정 hash를 새 계약으로 갱신했습니다.
- `route` 선택 필드와 처리 방식 렌더 키를 기존 엄격성 기대값에 반영했습니다.

## 막힌 점
없음.

# 요청 묶음과 상대경로 재작성 (#373 벤치 B)

## 단계
P1 — 끝난 요청의 최종 산출물과 재실행 자료를 한 폴더에 모았습니다.

## 한 일
- 성공·부분 실패·direct·연구 요청에 `requests/<request_id>/` 묶음을 만듭니다.
- 최종 단계 산출물과 스크립트를 복사하고 요청 workdir 경로만 상대경로로 바꿉니다.
- 같은 PC의 runner workdir만 묶고, 파일은 held descriptor에서 검사·hash·복사합니다.
- 코드 경로와 재실행 cwd를 묶음 루트로 맞추고 생성 작업은 worker thread에서 한 번만 실행합니다.
- 크기 초과·남은 절대경로·대체 판을 manifest와 부록에 기록합니다.
- 웹·CLI에 묶음 경로나 생성 경고를 표시합니다.

## 테스트 결과
- [x] 수정 전 새 회귀 테스트 실패 확인
- [x] `pytest -q`: 3744 passed, 53 skipped
- [x] Node CJS 20개 통과

## 기존 테스트 변경
- 일반·연구 단계 prompt 고정 hash 3곳을 새 경로 변수 규칙에 맞췄습니다.

## 막힌 점
없음.

# CSO 질문 세 범주와 계획 가정 (#373 방향 2)

## 단계
P1 — CSO가 과학 설계를 묻지 않고 정한 뒤 계획과 보고서에 밝히도록 굳혔습니다.

## 한 일
- PI 질문을 권한·비용·접근, PI만 아는 범위, 깊이로 제한했습니다.
- 깊이 선택지는 권장안을 먼저 두고 시간·비용을 표시합니다.
- 일반 계획에 선택 `assumptions`(최대 8개)를 넣고 저장·재시작·재계획에서 보존합니다.
- 가정은 웹 계획·질문 카드·CLI와 최종 보고서의 방법 본문에 표시합니다.
- 연구 lane은 같은 질문 기준을 쓰되 기존 동결 protocol에 가정을 남깁니다.

## 테스트 결과
- [x] 수정 전 Python 회귀 6건과 웹 2경로 실패 확인
- [x] 관련 회귀 283건 통과
- [x] `pytest -q`: 3729 passed, 53 skipped
- [x] Node CJS 19개 통과
- [x] `scripts/check_public.sh` 통과

## 기존 테스트 변경
- 계획·연구 prompt와 일반 계획 schema의 고정 hash를 새 계약으로 갱신했습니다.
- 재계획 schema에서 `assumptions`만 선택 필드라는 단언으로 바꿨습니다.
- 합성 prompt 기대값에 옛 계획용 빈 가정을 추가했습니다.

## 막힌 점
없음.

# PI용 보고서와 실행 기록 분리 (#373 벤치 A)

## 단계
P1 — 긴 실행 기록에 묻히던 PI용 결론과 권고를 별도 필드로 분리했습니다.

## 한 일
- `report`에는 결론·결과·방법·한계와 짧은 리뷰 참고만 남깁니다.
- 단계·경고·리뷰 원문·비용·claim check는 `report_appendix`로 보존합니다.
- API·CLI·웹·GitHub 보고·감사 번들이 두 필드를 전달합니다.
- 연구 합성 결과를 먼저 나눈 뒤 PI용 본문만 claim anchor 검사합니다.
- 긴 실행 기록은 request에 전문을 두고 terminal에는 잘림 표지와 전문 API 경로를 냅니다. GitHub·감사 번들도 request 전문을 씁니다.

## 테스트 결과
- [x] 수정 전 회귀 4건 실패 → 수정 후 회귀 5건 통과
- [x] `pytest -q`: 3722 passed, 53 skipped
- [x] Node CJS 18개 통과
- [x] `scripts/check_public.sh` 통과

## 막힌 점
없음.

# 게이트 오탐 G9: `../상류`와 긴 인라인 스크립트 (벤치 A)

**결론:** #371은 cd 대상 아래에 개인 경로가 없으면 단어 대조를 건너뛰었지만, 단어 하나라도 `..`가 있으면 모든 단어를 다시 대조해 상한(256)에 걸렸습니다. 이제 `..`(Windows가 지우는 `.. `·`...` 포함)가 든 단어만 따로 대조합니다. 개인 경로가 아래에 있는 cd 대상은 지금처럼 모든 단어를 봅니다.

**검증:** 새 test(긴 스크립트 + `../upstream` → allow)는 옛 게이트에서 실패. 기존 `../fh/.sec/key.txt` 등은 그대로 ask. 전체 pytest 통과.

# 보고서 실행 기록 부록과 분석 재현성 (#373 벤치 A)

## 단계
P1 — 눈가림 채점에서 확인된 읽기 쉬움·재현성 열세를 고쳤습니다.

## 한 일
- 일반·연구 보고서 본문을 결론과 권고 → 결과 → 방법 요약 → 한계 순서로 안내합니다.
- 단계 상태·경고·승인·리뷰·claim check는 한 개의 `부록: 실행 기록`에 모읍니다.
- 분석 script와 결과를 좌우한 reference는 `outputs/`에 선언·보존하고, 방법에 seed·버전을 적게 했습니다.
- prompt 고정 hash는 위 재현성 규칙 추가를 이유로 갱신했습니다.

## 테스트 결과
- [x] 선행 회귀 5 failed → 수정 뒤 통과
- [x] 관련 test 238 passed
- [x] `pytest -q`: 3716 passed, 53 skipped
- [x] Node CJS 18개 통과
- [x] `scripts/check_public.sh` 통과
- [x] UI 변경 없음

## 바꾼 파일
`labhq/orchestrator/cso.py`, `tests/test_cso.py`, `tests/test_general_evidence.py`, `tests/test_output_types.py`, `tests/test_output_types_research.py`, `tests/test_research_report.py`, `tests/test_step_prompt_rules.py`, `docs/manual.md`

## 막힌 점
없음.

## PI·Claude에게 물을 것
없음.

# 일반 lane 리뷰 지적 우선순위 (#373)

## 단계
P1 — 결론을 바꾸는 지적만 일반 lane의 수정과 실패를 부릅니다.

## 한 일
- 일반 리뷰 schema·prompt에 P1·P2·P3를 추가하고, P1이 있을 때만 `revise`로 판정합니다.
- 재계획과 수정 feedback에는 P1만 보내며, 상한 뒤 P2·P3만 남으면 완료합니다.
- P2 원문을 보고서 `리뷰 참고`에 붙이고, 우선순위가 없는 저장 리뷰는 P1로 이어 갑니다.
- 동작 설명과 모의 reviewer fixture를 갱신했습니다.

## 테스트 결과
- [x] 선행 회귀 2 failed → 수정 뒤 2 passed
- [x] 관련·모의 e2e 253 passed
- [x] `pytest -q`: 3708 passed, 53 skipped
- [x] `scripts/check_public.sh` 통과
- [x] UI 변경 없음

## 바꾼 파일
`labhq/orchestrator/cso.py`, `labhq/adapters/mock.py`, `tests/test_cso.py`, `tests/test_state.py`, `tests/test_research_report.py`, `docs/manual.md`

## 막힌 점
없음.

## PI·Claude에게 물을 것
없음.

# runner 요약: 직원이 쓸 python 명령까지 고른다 (#380 후속)

**결론:** #380은 PATH의 `python3`를 먼저 골랐는데, 이 PC에서 `python3`는 분석 패키지가 없는 3.14(WindowsApps 별칭)이고 `python`이 pandas가 있는 3.12였습니다. 이제 세 명령을 모두 검사해 분석 패키지가 가장 많은 것을 고르고(동률이면 python3·python·py 순), CSO 능력 줄에 `Python=3.12.10 run as \`python\``처럼 명령까지 알립니다.

**검증:** 이 PC 실측 `python` 3.12.10 선택. 새 test 2개(패키지 많은 쪽 선택·후보 없음 fallback, 능력 줄에 알려진 명령만 표시), 관련 test 219 passed.

# runner 소프트웨어 요약: 직원이 쓰는 python을 검사 (#376 후속)

**결론:** #376의 runner 요약이 `sys.executable`(labhq 자체 venv, pandas 없음)을 검사해서, T3 재실행에서 CSO가 "numpy/pandas도 없다"며 설치를 물었습니다. 직원 셸은 PATH의 Python 3.12(pandas 있음)를 씁니다. 이제 PATH의 `python3` → `python` → `py`를 검사하고, PATH에 없을 때만 labhq interpreter로 돌아갑니다.

**검증:** 새 test 2개(PATH 우선·없으면 labhq interpreter, runner가 staff_python을 씀), 관련 test 217 passed.

# labhq v0.25: PI가 공개 데이터로 시험하는 판

**통과 기준:** PI의 공개 데이터 요청 5건 중 4건 이상이 개발자 손 없이 보고서까지 가고, 요청당 승인 카드가 3장 이하여야 합니다(설치·예산 질문 제외).

**근거:** PI 본인 시운전 전에, 총괄이 PI의 요청 세트 5건을 trial 인스턴스에 보내 리허설했습니다(2026-10-04, PI 결정으로 이 근거에 선언). PI 본인 시운전 결과는 #373에 이어 붙입니다.

| 요청 | 결과 | 카드(제외 대상 뺌) | 비용 |
|---|---|---|---|
| 1. GSE10072 전체 분석(12차) | 보고서는 나왔으나 리뷰 지적이 남아 실패 표시. 원인(수정 라운드가 위 단계의 새 결과를 못 받음)은 #368로 고침 | 1(오탐 3장은 #364로 고침) | $19.9 |
| 2. GSE19804 DE·GSEA | 리뷰 revise → 수정 → accept | 0(설치 2·예산 1, PC 멈춤으로 생긴 이어 가기 1) | $17.4 |
| 3. 메타데이터 표 | accept | 1(오탐, #371로 고침) | $2.6 |
| 4. 문헌 5편 표 | accept | 0 | $2.7 |
| 5. 정규화 점검 | accept | 0 | $2.5 |

## 할 수 있는 것
- 웹 사무실이나 `labhq send`로 요청을 보내면 CSO가 계획을 세우고, 직원(Claude·Codex)이 단계를 나눠 실행하고, 다른 회사 모델이 리뷰한 뒤 보고서가 나옵니다.
- 위험한 일(작업 폴더 밖 쓰기, 재귀 삭제, 통제 데이터, 설치, 예산 초과)은 PI 결정 카드로 묻습니다.
- `labhq open`이 웹 사무실을 로그인된 채 엽니다.
- 연구 lane(설정 `research`, 기본 꺼짐)은 다음 순서로 진행합니다. 모의 시운전 10차에서 끝까지 통과했습니다.
  - 계획을 CP1에서 동결합니다.
  - 단계 결과를 claim·근거 계약으로 받습니다.
  - CP2에서 PI가 근거를 승인합니다.
  - 과학 리뷰를 거쳐, 수치마다 근거 앵커가 달린 보고서를 냅니다.
  - `labhq verify`가 산출 파일 hash와 앵커를 다시 검사합니다.
- 예시: [공개 폐선암 데이터 분석](../examples/gse10072/README.md)

## v0.2 이후 바뀐 것(요약)
- **안전:**
  - 같은 계정에서 돌 때 PI 개인 경로를 막습니다. Claude·Codex 직원은 전용 설정 폴더를 씁니다.
  - 게이트 오탐을 줄였습니다(리다이렉트, 따옴표 안 텍스트, Git Bash 경로, 긴 인라인 스크립트).
  - 놓치던 쓰기를 잡습니다(플래그 뒤 경로, tee, Export-Csv).
- **실행:**
  - 턴 상한에 걸린 단계는 같은 세션에서 마무리합니다.
  - 리뷰 수정 라운드는 다시 계산된 위 단계 결과를 아래 단계에 넘깁니다.
  - CSO는 이 PC의 소프트웨어(R·Python 패키지·docker 등)를 보고 계획합니다. 빌드가 필요한 패키지가 실패하면 대체안으로 갑니다.
  - 단계가 실패하면 1회 다시 계획합니다.
  - 실행 중인 요청에 PI가 메모를 보낼 수 있습니다.
  - Windows 기관 TLS 검사 환경에서는 OS 신뢰 저장소를 씁니다.
- **증거:**
  - CP2 근거 결합이 조상 단계 산출까지 인정합니다.
  - 일반 단계 결과 계약과 보고서의 도구 실패 경고를 넣었습니다.
- **어휘:** PI가 검토한 산출 데이터 종류 41개를 넣었습니다(EDAM 34개와 연결). 생명정보 전반으로 넓히는 일은 #375에서 합니다.
- **연구 pack:** 벌크 발현 종양 대 정상 `bulk_tumor_normal@1`을 넣었습니다.
- **문서:** README를 처음 쓰는 사람용으로 줄였습니다. "무엇이 다른가"와 어노테이션·시맨틱·온톨로지 세 층 설명을 넣었고, 매뉴얼·PI 문답·개발 문서는 따로 나눴습니다.

## 알려진 한계
- 자기 작업 폴더 안의 재귀 삭제도 아직 PI에게 묻습니다(셸의 현재 위치를 게이트가 모름).
- 일반 요청에서 리뷰 지적이 수정 1회 뒤에도 남으면, '해결되지 않은 리뷰 지적' 절을 단 보고서를 내고 요청은 실패로 표시합니다.
- 연구 lane이 리뷰에서 revise로 멈추면 새 요청으로 처음부터 다시 해야 합니다. CP3·CP4는 없습니다.
- R이 없는 PC에서는 직원이 Python으로 대체 구현합니다(limma 대신 직접 구현 등). 보고서에 그렇게 적힙니다.
- 실행 중 메모는 그 뒤에 시작하는 단계·리뷰·보고서만 읽습니다. 이미 도는 단계를 바꾸지는 못합니다(#59).

전체 변경 이력: [패치노트](../../patch_notes/README.md)

# 단계 실패 재계획과 선언 산출 규칙

- 결론: 일반 단계와 리뷰 수정 단계의 실패를 기본 1회 재계획하며, 모든 대체 경로는 같은 선언 파일명에 씁니다.
- 바뀐 것: `max_failure_replans`와 이전 설정 호환, 경로 독립 파일명·척도 및 출처 기록 규칙, 리뷰 수정 실패 복구.
- 실행한 것: 봇 지적 회귀는 수정 전 2 failed·1 passed, 수정 뒤 관련 pytest 242 passed. 공개·패치노트·목차·diff 검사 통과.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`, `labhq/settings.py`, `tests/test_cso.py`, `tests/test_output_types.py`, `tests/test_output_types_research.py`, `docs/manual.md`.

# 실행 중 요청에 PI 메모 (#373 질문 6)

**결론:** 실행 중 CSO 요청의 메모를 첫 계획부터 전달하고, 다음 turn이 없는 direct 요청은 거절합니다.

| 곳 | 바뀐 것 |
|---|---|
| gateway·CLI | 미종료 CSO 요청에만 메모를 저장하고 `request.note`를 냅니다. direct 요청은 409와 **끝난 뒤 이어 묻기** 안내를 돌려줍니다 |
| orchestrator | 메모 뒤 시작하는 plan·replan·단계·continuation·review·synthesis가 읽습니다. 연구 lane은 CP1 뒤 동결 계획을 바꾸지 않습니다 |
| 웹 | 진행 중 CSO 요청의 입력 기본값은 **이 요청에 메모**입니다. direct 요청에는 메모 선택을 보이지 않습니다 |

**검증:** 첫 구현 전 Python 6 failed·Node 1 failed. 리뷰 회귀는 수정 전 4 failed, 수정 뒤 pytest 8 passed·Node 2개 파일 통과. 공개 저장소·패치노트·목차 검사 통과.

**미해결:** 실행 중 session steer·pause는 #59 범위입니다.

# PR #376 — runner 로컬 소프트웨어를 반영한 CSO 계획

- 결론: CSO가 runner의 R·Python 패키지·계산 도구 상태를 알고 계획하며, 빌드 실패는 준비한 대체안으로 넘깁니다.
- 바뀐 것: runner hello 요약, 일반·연구 계획과 재계획의 runner별 한 줄, binary wheel·대체안 규칙, prompt hash와 manual.
- 실행한 것: 새 회귀 수정 전 4 failed, 수정 뒤 관련 pytest 287 passed. 공개·patch note·목차·diff 검사 통과.
- 미해결: 없음.
- 근거: `labhq/doctor.py`, `labhq/runner/daemon.py`, `labhq/orchestrator/cso.py`, `tests/test_doctor.py`, `tests/test_cso.py`.

# 출력 데이터 종류 어휘 PI 검토 반영

**결론:** PI가 검토한 41키를 그대로 반영했다. 정규화 값은 count·expression·transformed로 나뉘고 `features`는 `genomic_features`로 바뀌었다.

| 항목 | 결과 |
|---|---|
| 어휘·입력 적합성 | 새 3키와 이름 변경, 45키 상한, 분석별 fit·mismatch 반영 |
| EDAM 1.25 | 고정 release에서 재생성. 34키 연결, 7키 local-only. `variant_annotations`에 맞는 살아 있는 data 용어가 없어 `null` 유지 |
| 옛 선언 | hash가 있는 선언은 기존 `vocab_changed` 판정을 쓴다. hash가 없는 legacy `normalized_counts`·`features`도 `vocab_changed`로 막았다 |
| semantics fixture | PI 어휘 검토로 `normalized_counts` 뜻이 바뀌어 q02·q06 후보를 `unknown(vocab_changed)`로 고정했다 |
| 고정값 | PLAN schema·prompt와 `single_cell_de@2`·`bulk_tumor_normal@1` 내용·hash는 바뀌지 않았다 |

**검증:** Windows 전체 pytest 3676 passed·53 skipped. semantics pilot 110 passed, shadow 제거 5 passed. EDAM subset 재생성·검사 통과.

# 웹 사무실 진입: `labhq open` (PI 방문 2026-10-04)

**결론:** PI가 일반 `gateway` 시작 뒤 웹의 '게이트웨이 토큰' 칸에서 무엇을 넣을지 몰라 시운전을 못 했습니다. 이제 세 곳에서 길을 알립니다.

| 곳 | 바뀐 것 |
|---|---|
| `labhq open [--3d]` | 설정의 `gateway.client_token`을 주소에 실어 기본 브라우저로 엽니다. 웹이 토큰을 저장하고 주소에서 지웁니다. 토큰은 터미널·로그에 찍지 않습니다. 브라우저를 못 열면 설정 파일 위치와 키 이름을 알립니다 |
| `gateway` 시작 줄 | `웹 사무실: http://…/ (다른 창에서 labhq open)` 한 줄, 토큰 없음 |
| 웹 토큰 칸 | "설정 파일의 gateway.client_token 값, 또는 labhq open" 안내 |

**검증:** 새 test 2개(주소에만 토큰, 3D·브라우저 실패 안내), 관련 test 238 passed.

# 게이트 오탐 G7: 작업 폴더 cd 뒤 긴 인라인 스크립트 (13차 리허설)

**결론:** 직원이 `cd <자기 작업 폴더> && python3 -c "<긴 스크립트>"`를 돌리면, 스크립트 단어 수백 개가 경로 후보로 세어져 상한(256)을 넘고 개인 경로 카드가 났습니다. 이 카드를 없앴습니다. 링크·`..`·개인 경로 아래로의 cd는 그대로 묻습니다.

| 경로 | 고침 |
|---|---|
| `_cd_reaches_private` 단어 대조 | cd 대상 아래(링크 포함)에 개인 경로가 없고 단어에 `..`가 없으면 단어를 그 폴더 기준으로 대조하지 않음. 단어는 적힌 대로 읽히므로 그 아래만 가리킨다 |
| `touches_resolved` 링크 해석 | 작업 폴더(또는 cd 대상)에 첫 성분이 실제로 없는 상대 단어는 링크일 수 없어 적힌 대로만 대조하고 상한에 세지 않음 |
| 그대로 세는 것 | 첫 성분에 glob·brace·`~`(8.3 이름)·`:`·`\`·`$`·`%`·`^`·`!`·backtick, `.`·`..` |

**#131 성질:** 실제로 있는 미끼 257개 뒤의 링크는 여전히 "not all resolved"로 묻고, 없는 이름 미끼 뒤의 링크는 해석해서 묻습니다(테스트를 두 경우로 나눔).

**검증:** `pytest -q` 3674 passed, 53 skipped. 새 테스트는 옛 게이트에서 실패.

# 리뷰 수정 라운드의 upstream 갱신 (12차 모의 시운전)

**결론:** 12차(일반 lane)가 `review_unresolved`로 끝난 원인을 고쳤습니다. 수정 라운드에서 보고서 단계가 위 단계의 새 결과를 받지 못해 철회된 수치를 그대로 두었습니다.

| 원인 | 고침 |
|---|---|
| 지적받은 단계는 자기 지적만 받음(`with_downstream_revisions`) | 위 단계도 다시 계산됐으면 같은 안내를 자기 지적 뒤에 붙임 |
| 이어 가는 세션은 updates만 받아 새 upstream 결과를 못 봄 | 위 단계가 이번 라운드에 다시 돌았으면 새 upstream 결과를 updates에 넣음 |
| 수정 답이 변경분만 담아("나머지는 그대로") 이전 결과 전체를 대체 | 수정 지시에 "전체 결과를 다시 내라"는 규칙 |
| 일반 합성 보고서 앞 군말 | 프롬프트에 "첫 heading부터"만. 일반 보고서는 답이 heading 앞에 올 수 있어 labhq는 아무것도 떼지 않는다(봇 리뷰) |

| 라운드 중 재시작 뒤 끝난 upstream 수정이 pending에서 빠짐(봇 리뷰) | 이어 가는 수정 단계에는 항상 현재 upstream 결과를 넣음 |

**검증:** `pytest -q` 3592 passed, 53 skipped. 새 테스트: 이어 가는 수정 단계의 prompt, 재시작 뒤 수정, 일반 보고서 보존. 기존 테스트 둘은 새 동작으로 기대값을 바꿈(지적받은 단계도 upstream 안내를 받음, 이어 가는 수정에는 upstream 결과가 들어감).

## 2026-10-04 · #58 — 관찰 산출물 tool_use_id와 웹 감사 번들

- 결론: Claude 쓰기 도구의 PostToolUse 기록을 관찰 산출물에 보수적으로 연결하고, 인증된 웹 요청 상세에서 산출 파일 없는 감사 번들을 받게 했다.
- 바뀐 것: `tool_use_id` manifest·verify·`artifacts.json`, `GET /api/requests/{id}/audit-bundle`, 2.5D·3D 링크, README와 HANDOFF의 #58 ①~⑥ 완료 상태.
- 실행한 것: 수정 전 5 failed 확인. 관련 pytest 156 passed·4 skipped, Node 5개 통과.
- 미해결: 없음. 이 변경으로 #58의 표 ①~⑥이 모두 끝난다.
- 근거: `labhq/hooks/tool_use.py`, `labhq/runner/daemon.py`, `labhq/evidence/audit.py`, `labhq/gateway/server.py`, `labhq/web/`, `tests/test_observed_outputs.py`, `tests/test_labhq_verify.py`.

## 2026-10-04 · 연구 lane — 벌크 두 조건 DE pack

- 결론: `bulk_tumor_normal@1`의 core 통계 계약과 scale·pairing·model 조합을 기계 판정한다.
- 바뀐 것: 분석 단위·다중검정·민감도 분석은 중복 pack 필드를 없애고 core 값을 정본으로 삼았다. 모형 규칙은 허용 조합표 하나로 닫았다.
- 실행한 것: 두 번째 봇 지적용 test는 수정 전 10 failed, 수정 뒤 관련 test 151 passed. 기존 rule id와 pack hash test는 새 단일 표와 pack 내용 변경 때문에 갱신했다.
- 미해결: 없음.
- 근거: `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/research/packs/bulk_tumor_normal.yaml`, `tests/test_research_bulk_pack.py`.

## 2026-10-04 · v0.25 전 작은 결함 세 건

- 결론: Bash process substitution 오탐, 결정 답변 뒤 도구 실패 누락, CA 변수 한쪽 누락을 고쳤다.
- 바뀐 것: `<(`·`>(` 토큰은 인자로 남기고 내부 redirect는 검사한다. 결정 전후 `tool_errors`를 중복 없이 합친다. CA 변수 하나만 정했으면 다른 변수도 같은 파일을 쓴다.
- 실행한 것: 새 회귀는 수정 전 6 failed·3 passed, 수정 뒤 관련 pytest 344 passed. 공개 저장소 검사 통과.
- 미해결: 없음.
- 근거: `labhq/policy.py`, `labhq/orchestrator/cso.py`, `labhq/runner/daemon.py`, `tests/test_shell_write_quotes.py`, `tests/test_cso.py`, `tests/test_system_ca.py`.

## 2026-10-04 · 게이트 — Git Bash 경로

- 결론: 12차 모의 시운전(일반 lane, v0.25 사전 점검)에서 data_steward가 자기 작업 폴더 `.tmp`에 쓸 때마다 게이트가 PI에게 물었다(같은 단계 3장). Claude의 Bash는 Windows에서 Git Bash라 경로를 `/c/Users/...`로 쓰는데, 허용 루트는 `C:/Users/...`여서 비교가 안 됐다.
- 바뀐 것: `labhq/policy.py` `_evaluate_tool` — Bash이고 루트가 Windows 드라이브 경로면 셸 쓰기 대상을 `_git_bash_path`로 바꿔 비교한다(Claude 쓰기 도구가 이미 쓰는 변환). 바꿀 수 없는 경로는 그대로 둬 묻는다. POSIX 러너의 `/c/...`는 POSIX 폴더 그대로다.
- 실행한 것: 관련 test 478 passed. 새 test 2건은 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_shell_write_quotes.py::test_git_bash_drive_paths_are_read_as_windows_paths`.

## 2026-10-04 · #58 ①④ — 일반 단계 결과 계약과 보고서 경고

- 결론: 일반 단계 답을 네 블록으로 보존하고, 모으지 않은 Evidence 경로와 실패한 도구 호출은 요청을 거부하지 않고 최종 보고서 경고로 올린다.
- 바뀐 것: 일반 STEP·직원 규칙, `TaskResult` 파싱 필드, runner의 도구 오류 첫 줄 수집, SYNTH·`_finish` 경고 절. 연구 result v2·CP2 프롬프트는 그대로다.
- 실행한 것: 새 회귀는 수정 전 import 실패. 수정 뒤 관련 pytest 417 passed·1 skipped, 공개 저장소·목차·diff 검사 통과.
- 미해결: #58의 tool_use_id와 웹 감사 번들 다운로드는 다음 PR 범위다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/runner/daemon.py`, `labhq/models.py`, `tests/test_general_evidence.py`.

## 2026-10-03 · 문서 — v0.5 진행 상태

- 결론: README §11 v0.5 행과 HANDOFF 작업 큐를 오늘 시운전 결과에 맞췄다. 10차가 연구 lane을 claim 앵커 보고서까지 완주했고(`labhq verify` exit 0), 8차·11차는 리뷰 P1으로 revise였다.
- 바뀐 것: README v0.5 "지금"·"남은 것", HANDOFF v0.25 시운전 행(완료), v0.5 행(1/2, 남은 것에 revise 이어 가기), 병합된 #346 행 삭제.
- 실행한 것: patch-notes·public 검사.
- 미해결: 없음.
- 근거: #298 시운전 보고 댓글.

## 2026-10-03 · 연구 최종 보고서 — 앞 군말 제거

- 결론: 10차 모의 시운전이 연구 lane을 처음 끝까지 통과했다(research_reported, 앵커 58·문제 0, verify exit 0). 보고서가 "최종 보고서를 작성 중입니다… ---"로 시작하는 작은 결함이 있어 고쳤다.
- 바뀐 것: `labhq/orchestrator/cso.py` `report_body()` — 첫 markdown 제목 앞의 짧은 글(앵커 없음, 400자 이하)을 빼고 그 뒤를 검사·저장한다. RESEARCH_SYNTH_PROMPT에 "서문 없이 첫 제목부터".
- 실행한 것: 전체 test 3552 passed. 새 test는 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_research_report.py::test_a_lead_in_before_the_first_heading_is_not_part_of_the_report`.

## 2026-10-03 · 결과 계약 — 결합 문제를 첫 교정에

- 결론: 9차 모의 시운전이 `research_failed`로 끝났다. biologist 단계(s05)가 근거 행 id를 슬롯 이름으로 붙이고 `slots` 필드를 빠뜨렸는데, 결합 검사가 필드 검증 통과 뒤에만 돌아 슬롯 누락이 교정 2회가 끝난 뒤에야 드러났다. 하류 6단계가 skip됐다.
- 바뀐 것: `labhq/research/contract.py` — `research_result_errors`가 필드 오류가 있어도 원본 JSON으로 claim·슬롯 결합 문제를 함께 낸다(`_raw_binding_errors`). 슬롯 이름을 단 행이 `slots`를 안 적었으면 `add "slots": ["<slot>"]`를 덧붙인다. salvage는 그대로다(필수 슬롯 누락은 건지지 않음).
- 실행한 것: 전체 test 3534 passed. 새 test 2건은 수정 전 실패.
- 미해결: 10차 시운전으로 확인.
- 근거: `tests/test_research_cp2.py::test_a_missing_slot_is_reported_with_the_field_errors_and_names_the_fix`, `::test_the_first_correction_asks_for_the_slot_with_the_field_error`.

## 2026-10-03 · runner — Windows OS 신뢰 저장소를 직원 Python에

- 결론: 9차 모의 시운전에서 Codex engineer가 Enrichr 접속 TLS 검증 실패로 멈췄다. 원인은 기관 TLS 검사 장비(발급자 SOOSAN INT)의 루트가 Windows 저장소에만 있고 certifi에는 없는 것이다. runner가 두 쪽을 합친 CA 묶음을 직원 env에 넣는다. 검증은 끄지 않는다.
- 바뀐 것: `labhq/runner/system_ca.py`(certifi + Windows ROOT·CA 저장소 PEM), `Runner._system_ca_env` — workspace 루트에 `.labhq-system-ca.pem`을 한 번 쓰고 `SSL_CERT_FILE`·`REQUESTS_CA_BUNDLE`을 지정한다. PI가 runner 환경이나 `engines.*.env`에 둘 중 하나를 정했으면 건드리지 않는다. `runner.system_ca_bundle`(기본 true, Windows에서만 동작).
- 실행한 것: 전체 test 3539 passed. 새 runner test 6건은 수정 전 실패. 이 PC에서 내보낸 묶음으로 maayanlab.cloud·data.broadinstitute.org·string-db.org 검증 통과를 직접 확인했다.
- 미해결: 없음.
- 근거: `tests/test_system_ca.py`.

## 2026-10-03 · 연구 단계 — 동결 protocol을 단계 프롬프트에

- 결론: 8차 모의 시운전 리뷰가 P1로 revise했다. DE 단계가 동결 protocol과 다른 저발현 필터를 쓰고(양성 대조 NEK2·TTK 탈락) 이를 적지 않았다. 원인은 단계 프롬프트에 protocol이 들어가지 않은 것이다. 지시는 "저발현 필터 뒤"뿐이었다.
- 바뀐 것: `labhq/orchestrator/cso.py` `_research_protocol_digest()` — 연구 단계 프롬프트에 질문·범위·protocol·pack 값을 자르지 않고 넣는다. 리뷰·보고서용 계획 digest도 protocol은 그대로 두고 단계 목록만 자른다(봇 P1). 이탈하면 method_changes에 적고 사전 규칙을 지켰다고 쓰지 않게 한다.
- 실행한 것: 전체 test 3530 passed. 새 test는 수정 전 실패.
- 미해결: 9차 시운전으로 리뷰 통과 → claim 앵커 보고서까지 확인.
- 근거: `tests/test_research_cp2.py::test_research_step_prompt_carries_the_frozen_protocol`.

## 2026-10-03 · CP2 근거 결합 — 조상 단계 산출물 인정

- 결론: 8차 모의 시운전 CP2에서 거부된 근거 8건 중 6건은 해석·보고 단계가 조상(직접 상류가 아닌) 단계의 선언·hash된 산출물을 인용한 경우였다. 결합 규칙을 조상 전체로 넓혔다.
- 바뀐 것: `labhq/orchestrator/cso.py` — `step_ancestors()`(run_dag의 조상 계산을 함수로 뺌), CP2 결합의 upstream을 조상 전체로. 계획 사슬 밖 단계는 여전히 거부한다.
- 실행한 것: 전체 test 3530 passed. 새 test는 수정 전 실패(조부모 인용이 거부됨).
- 미해결: 상류 단계의 미선언 파일 인용(나머지 2건)은 설계대로 거부한다.
- 근거: `tests/test_research_cp2.py::test_cp2_binds_an_ancestors_output_but_not_an_unrelated_steps`.

## 2026-10-03 · 게이트 — 플래그 뒤 경로와 tee, Get-FileHash 옆 따옴표 행

- 결론: 8차 모의 시운전 오탐(Get-FileHash 옆 manifest 행의 `<GSM>/suppl/`을 리다이렉트로 읽음)을 고쳤다. 같이 찾은 놓친 쓰기도 막았다. `Set-Content -Encoding utf8 C:/밖/x`처럼 플래그가 경로보다 앞이거나, bash `tee`, `sc`·`Export-Csv`·`Tee-Object`로 작업 폴더 밖에 쓰는 경우다.
- 바뀐 것: `labhq/policy.py` `_named_write_targets`·`_ps_writer_targets`. 모르는 플래그(접두 약어 포함)의 다음 단어도 목적지로 보고(과보고 쪽), `-Value`·`-Encoding` 같은 텍스트 인자만 뺀다. Get-FileHash·Import-Csv·Export-Csv를 데이터 명령에 넣었다.
- 실행한 것: 전체 test 3526 passed. 새 test 15건 중 13건은 수정 전 실패(나머지 2건은 과보고 방지 확인).
- 미해결: 변수·splatting·별칭 전부는 여전히 읽지 않는다(문서화된 한계, 경로 가드는 sandbox가 아님).
- 근거: `tests/test_shell_write_quotes.py`.

## 2026-10-03 · 연구 단계 턴 상한 — 같은 세션에서 한 번 마무리

- 결론: 7차 모의 시운전에서 qc_reviewer가 상류 수치를 모두 재현한 뒤 40턴이 끝나 요청 전체가 research_failed가 됐다. 이제 연구 단계는 같은 세션에서 절반 상한으로 한 번 마무리하고, 수습 turn은 2턴에서 4턴으로 올렸다(2턴 수습은 세 번째 턴에서 멈춰 아무것도 저장하지 못했다).
- 바뀐 것: `research.finish_turns`(기본 1) — `run_step`이 연구 단계(`kind: step`)의 error_max_turns 뒤 마무리 turn을 부른다. 마무리 turn이 건드리지 않은 파일은 첫 turn의 hash를 유지해 CP2 근거 결합이 깨지지 않는다. 일반 lane·교정 turn은 그대로다. 수습 turn은 원래 상한보다 높이지 않는다.
- 실행한 것: 전체 test 3507 passed. 새 test는 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_cso.py`(finish 3종), `tests/test_research_cp2.py::test_a_research_step_past_its_turn_limit_finishes_in_its_session`.

## 2026-10-03 · 게이트 오탐 — 리다이렉트 대상을 명령 목적지로 읽음

- 결론: `cp a outputs/ 2>/dev/null`의 /dev/null을 cp 목적지로 읽던 오탐을 고쳤다(7차 모의 시운전). PowerShell `Copy-Item ... 2>$null`도 같다.
- 바뀐 것: `labhq/policy.py` `_shell_write_targets`가 명령 이름 기반 검사 전에 리다이렉트 구간을 지운다. 리다이렉트 대상은 기존 리다이렉트 검사가 그대로 판정한다.
- 실행한 것: 관련 test 451건 통과, 새 test 6건은 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_shell_write_quotes.py`.

## 2026-10-03 · PR 준비 — 연구 결과 계약 위반 행 분리(#90, #298)

- 결론: 교정 뒤에도 남은 행 단위 위반은 해당 행·파생 행·link만 빼고 CP2로 넘긴다. 구조 위반은 계속 단계를 실패시킨다.
- 바뀐 것: 결과 교정 기본값을 2회로 올리고, 거부 행과 unsupported claim을 CP2 카드·receipt·최종 보고서·감사 번들에 남겼다.
- 실행한 것: 새 회귀는 수정 전 실패했다. 관련 pytest 378건·Node 4건, schema·prompt hash, 공개 저장소·diff 검사가 통과했다.
- 미해결: 없음.
- 근거: `labhq/research/contract.py`, `labhq/orchestrator/cso.py`, `labhq/evidence/audit.py`, `labhq/web/ui/decide.js`, `tests/test_research_cp2.py`, `tests/test_research_report.py`.

## 2026-10-03 · PR 준비 — 연구 결과 계약 교정 turn(#90, #298)

- 결론: 연구 단계 결과 JSON이 계약 검증에 걸리면 원 분석을 다시 돌리지 않고 한 번 고쳐 받아, 통과하면 요청을 계속한다.
- 바뀐 것: `research.result_corrections`(기본 1), 같은 session·workdir의 결과 교정 turn, validator 전용 필드 규칙 prompt를 추가했다. 일반 lane과 `evidence_checkpoint: false` 흐름은 그대로다.
- 실행한 것: 새 회귀 5건이 수정 전 실패했다. 수정 뒤 관련 pytest 365건, 공개 저장소 검사, diff 검사가 통과했다.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`, `labhq/evidence/claims.py`, `labhq/research/contract.py`, `labhq/settings.py`, `tests/test_research_cp2.py`, `tests/test_step_prompt_rules.py`.

## 2026-10-03 · Codex 직원 output schema strict 변환

- 결론: 전체 연구 schema에서 나던 `invalid_json_schema` 400을 고쳤다. 원본 계약과 hash는 그대로다.
- 바뀐 것: arbitrary-key 사전은 `{key,value}` 배열로 왕복한다. Codex가 거부한 type 제약은 transport schema에서 빼고, 중첩 nullable array는 `anyOf(array, null)`로 바꾼다. 응답은 원래 형태로 되돌린 뒤 기존 검증으로 보낸다.
- 실행한 것: 이분 탐색에서 type 제약과 `Claim.comparisons[].assumptions`의 nullable array 구조를 확인했다. 실제 gpt-5.6-luna로 STEP·pack 연구 계획·PLAN·REPLAN·REVIEW·연구 리뷰가 모두 기존 검증을 통과했고, 관련 pytest 109건과 공개 검사가 통과했다.
- 미해결: 없음.
- 근거: `labhq/util.py`, `labhq/research/contract.py`, `tests/test_openai_strict_schema.py`, `tests/test_research_protocol.py`.

## 2026-10-03 · trial3-fixes — 사용 불가 직원 배정·배경 실행 뒤 종료 차단

- 결론: adapter preflight에 실패한 직원은 계획 대상에서 빠지고, Claude 직원은 배경 작업 없이 명령 결과를 확인한 뒤 turn을 끝낸다.
- 바뀐 것: runner roster가 doctor와 같은 preflight를 쓰며, 일반·연구·재계획의 직원 판정을 맞췄다. Claude env 기본값과 쓰기 직원 공통 규칙도 추가했다.
- 실행한 것: 새 회귀 5건은 수정 전 실패했고 수정 후 통과했다. 관련 pytest 302건과 공개 저장소 검사가 통과했다.
- 미해결: 없음.
- 근거: `labhq/adapters/__init__.py`, `labhq/runner/daemon.py`, `labhq/orchestrator/cso.py`, `labhq/adapters/claude_code.py`, `labhq/adapters/base.py`, `tests/test_cso.py`, `tests/test_role_footer.py`.

## 2026-10-03 · README를 읽는 사람별로 나눔 (v0.25 때 병합, 초안 PR)

- 결론: README는 처음 쓰는 사람용(11KB)으로 줄이고, 본문은 `docs/manual.md`, PI 문답과 결정은 `docs/pi-qa.md`, 개발 규칙은 AGENTS·HANDOFF로 나눴다. 병합은 v0.25 릴리즈 때다.
- 바뀐 것: `README.md`, `docs/manual.md`, `docs/pi-qa.md`, `docs/media/*.svg` 4장, `AGENTS.md`·`CLAUDE.md`·`HANDOFF.md`, 절 번호 참조를 쓰던 코드 주석, `scripts/integrations.py`(배지는 README, 도구 표는 매뉴얼), `scripts/patch_notes.py`(매뉴얼 갱신도 문서 갱신으로 셈).
- 실행한 것: 관련 test 72건 통과(2건 skip), `scripts/integrations.py --check`, `scripts/check_public.sh`, 문서 내부 링크·앵커 깨짐 0건.
- 미해결: 없음. 로드맵 결정 ⑥은 PR #347로 반영돼 매뉴얼 로드맵 절에 들어 있다.
- 근거: `docs/manual.md`, `docs/pi-qa.md`, Refs #298.

## 2026-10-03 · #346 후속 — 범위 판정 보존, bench, CI

- 결론: 뒤 계획에 범위 판정이 없으면 첫 판정을 쓰고, bench는 범위 카드를 진행으로 답하며, 범위 카드 Node test를 CI에서 돌린다.
- 바뀐 것: `labhq/orchestrator/cso.py`(`scope_first`), `labhq/bench.py`, `tests/test_web_3d.py`.
- 실행한 것: 관련 test 87건 통과, 새 test 2건은 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_scope_gate.py`.

## 2026-10-03 · 로드맵 ⑥ — 통과 기준과 버전 나누기

- 결론: PI 결정 ⑥ 나(#298). 버전마다 통과 기준을 붙이고, v0.75는 HPC에서 공개 데이터, v0.9는 통제 데이터, v1 앞에 v0.95(다른 사람의 설치·온보딩)를 넣었다.
- 바뀐 것: README §11 표(통과 기준 열, v0.9·v0.95 행), HANDOFF 작업 큐와 PI 결정 표.
- 실행한 것: `scripts/check_public.sh`, 패치노트·목차 검사.
- 미해결: 통과 기준 숫자는 PI가 바꿀 수 있다.
- 근거: `README.md`, `HANDOFF.md`.

## 2026-10-03 · #36 — 범위 밖 요청은 실행 전에 PI에게 묻기

- 결론: CSO가 계획에 범위 판정(in·borderline·out)을 같이 내고, out이면 단계를 돌리기 전에 폰 카드로 진행·중단을 묻는다. 중단하면 단계 실행 비용 없이 끝난다(브리핑·계획 1회 비용은 든다). 진행하면 받은 계획을 다시 계획하지 않고 그대로 돌린다.
- 바뀐 것: `PLAN_SCHEMA`의 scope 판정과 계획 프롬프트의 랩 범위, 새 승인 종류 `scope`(웹 카드의 '범위 확인'·진행·중단 버튼), 설정 `lab.scope`(두 example yaml), mock CSO의 `[out-of-scope]`·`[borderline]` 토큰. 보고서 메타데이터에 'Scope verdict' 한 줄. `orchestrator.wait_for_clarification=false`이면 out이어도 묻지 않고 실행하며 decision은 `not_asked`로 남는다.
- 실행한 것: `tests/test_scope_gate.py`·`test_output_types.py`·`test_step_prompt_rules.py` 61건과 `tests/web_scope_card.cjs`, `scripts/check_public.sh` 통과.
- 미해결: bench 실제 엔진이 scope 카드를 거절하는 것, 웹 test의 CI 등록, out+질문 동시 응답, 재계획 응답에서 scope 누락, wait_for_clarification=false 결합, 재시작 resume 카드 문구, 이슈 원문과 다른 판정 시점(계획 안). PR 본문 "남은 지적".
- 근거: `labhq/orchestrator/cso.py`, `labhq/settings.py`, `labhq/web/ui/decide.js`, `tests/test_scope_gate.py`.

## 2026-10-03 · PR #344 후속 — 구조화된 결과의 PI 질문, 재계획의 환경 단계

- 결론: #344가 더한 PI 질문 text 탐색이 구조화된 결과를 낸 직원에게도 걸려, 로그 속 예시 JSON을 질문으로 읽을 수 있었다. 구조화된 결과가 없을 때만 text를 찾는다. 재계획 프롬프트에도 환경 단계 규칙을 넣었다.
- 바뀐 것: `blocking_question()`, `REPLAN_PROMPT`, 직원 공통 쓰기 규칙 문구("환경 단계가 있으면"), README §11 v0.5 행.
- 실행한 것: 관련 test 234건 통과, 새 test는 수정 전 실패.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`, `labhq/adapters/base.py`, `tests/test_cso.py`.

## 2026-10-03 · 모의 시운전 2차 — 요청마다 분석 환경 단계 하나, PI 질문 객체 우선

- 결론: 2차 모의 시운전에서 단계마다 venv를 따로 만들었고, 다른 단계의 venv에는 패키지를 더 넣지 못했다. 계획 단계에서 환경 단계를 하나 두게 하고, 직원은 그 interpreter를 경로로 쓰고 승인된 추가 패키지는 자기 작업 폴더 `./.pylib`에 설치한다. 시운전에서 실제로 통한 방식을 규칙으로 고정한 것이다.
- 바뀐 것: `ENV_STEP_RULE`을 일반·연구 계획 프롬프트에, 직원 공통 쓰기 규칙(`WORKSPACE_WRITE_RULES`)에 환경 사용 줄을 넣었다. PI 질문은 응답 안에 줄바꿈이 든 다른 큰 JSON이 함께 있어도 `blocking_decision` 키가 있는 객체를 골라 읽는다(PR #343 봇 지적).
- 실행한 것: 관련 test 259건 통과, 새 test 2건은 수정 전 실패. 프롬프트 해시 기대값 두 개를 의도한 변경으로 갱신.
- 미해결: 요청 단위 공유 환경(여러 단계가 한 venv에 쓰기)은 동시 설치 경합 때문에 하지 않았다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/adapters/base.py`, `tests/test_cso.py`, `tests/test_role_footer.py`.

## 2026-10-03 · #84·#57 — 다듬기 후속: PI 질문 줄바꿈·질문 길이 규칙·결정 카드 줄바꿈

- 결론: #342 봇 지적 두 건과 pre-trial-polish 메모의 미해결 세 건을 닫았다. 일반 lane 직원이 PI 질문 JSON 문자열 안에 실제 줄바꿈을 써도 질문이 사라지지 않고, 재계획·연구 계획 프롬프트에도 질문 길이 규칙이 들어갔다. 결정 카드는 summary 줄바꿈을 그대로 보여 준다.
- 바뀐 것: `extract_json`에 `strict=True` 인자를 더했다. 기본값은 그대로라 다른 호출자는 엄격하고, `blocking_question`만 `strict=False`로 읽는다. STEP_PROMPT에 줄바꿈은 `\n`으로 쓰라는 문장을 더했다. `PI_CARD_QUESTION_RULE` 상수를 PLAN_PROMPT와 REPLAN_PROMPT가 함께 쓴다(PLAN_PROMPT 바이트는 그대로). 연구 계획 프롬프트의 질문 줄에 500자 이하·질문 먼저를 더했고 `RESEARCH_PROMPT_SHA`를 의도한 변경으로 갱신했다. 2.5D `.ap .sum`과 3D 패널에 `white-space:pre-wrap`을 넣었다. CI가 돌리지 않던 Node test 8개를 `tests/test_web_3d.py` 목록에 등록했다.
- 검증: `test_step_prompt_rules.py`·`test_output_types_research.py`·`test_web_3d.py` 61 passed 1 skipped, `tests/web_decision_summary.cjs`(신규) fail 0, `scripts/check_public.sh` 통과. 전체 pytest는 CI에 맡겼다.
- 미해결: `strict=False`는 마지막 "가장 큰 객체" 탐색에서 날 줄바꿈이 든 관련 없는 큰 객체도 후보로 받아, 펜스 없는 텍스트에 그런 객체와 정상 escape된 blocking JSON이 같이 있으면 질문을 놓친다. 더 큰 객체가 이기는 부류는 main에도 있었다. `blocking_question`이 `blocking_decision` 키를 가진 후보를 먼저 고르면 막힌다.
- 근거: `labhq/util.py`, `labhq/orchestrator/cso.py`, `labhq/web/index.html`, `labhq/web/lab3d/index.html`, `tests/test_step_prompt_rules.py`, `tests/test_output_types_research.py`, `tests/test_web_3d.py`, `tests/web_decision_summary.cjs`.

## 2026-10-03 · #84 — 직원·CSO 프롬프트 규칙 묶음

- 결론: 직원 공통 규칙, 단계 프롬프트, 계획 프롬프트, 분석가·QC·과학 리뷰어 지침에 VB·MD 코드 흐름 리뷰에서 가져온 규칙을 넣었다. 코드 흐름은 그대로이고 문구만 바뀐다. 모의 시운전 2차(#298 댓글 5962361067)에서 리뷰어가 잡은 같은 환자 표본을 독립으로 센 점, 일부 유전자만 고른 필터가 분석가·리뷰어 규칙의 근거다.
- 바뀐 것: `ROLE_FOOTER`에 실패한 조회는 음성 결과가 아니라 실패로, 0건 검색은 출처·질의·범위·필터와 함께, 방법을 낮췄으면 이유와 함께 보고하라는 두 줄을 더했다. `STEP_PROMPT`에 조용한 방법 하향 금지(디버깅을 이어 가고, 포기하면 시도한 것과 이유)와 blocking_decision 형식(700자 이하, 첫 문장이 질문, 선택지는 "- " 줄마다 하나)을 더했다. 일반 lane `PLAN_PROMPT`의 질문 줄에는 700자 이하·질문 먼저를 더했다. analyst는 donor 구조 반영·seed 고정과 기록·연관으로 서술, qc_reviewer는 같은 결함을 WARN·FAIL로 잡고 자기 무작위 단계의 seed도 고정, sci_reviewer는 과잉 일반화·cherry-picking·추측과 지적마다 근거 문장 인용을 점검한다. 연구 lane 계획 프롬프트는 그대로다(RESEARCH_PROMPT_SHA 유지). #336·#338·#339에 이미 있는 문장과 VB의 "반복 횟수"·"항상 절대경로"는 넣지 않았다.
- 검증: `tests/test_role_footer.py`, `tests/test_step_prompt_rules.py`(신규), `tests/test_agent_prompts.py`(신규), `tests/test_output_types.py`를 함께 돌려 52 passed. PLAN_PROMPT 해시는 의도한 변경이라 갱신했다. `scripts/check_public.sh` 통과. 전체 pytest는 CI에 맡겼다. #40 bench 한 회는 돌리지 않았다.
- 미해결: ① blocking_decision이 JSON 문자열 안의 줄바꿈 요구와 충돌해, 모델이 실제 줄바꿈을 쓰면 일반 lane의 `extract_json`이 실패해 PI 질문이 사라진다. 선언 출력이 있는 단계는 "missing outputs"로 실패하고, 없는 단계는 질문이 결과로 하류에 넘어간다. 연구 lane은 structured output이 줄바꿈을 인코딩해 해당 없다. ② 폰 카드 규칙(700자 이하, 질문 먼저)은 `PLAN_PROMPT`에만 넣었고 `REPLAN_PROMPT`의 clarifying_questions는 빠졌다. ③ 이슈 표의 "첫 문장 굵게"는 결정 카드가 textContent로 그려 `**`가 그대로 보여서, "선택지 bullet"은 계획 질문의 선택지가 a/b/c/d 버튼이라서 뺐다.
- 근거: `labhq/adapters/base.py`, `labhq/orchestrator/cso.py`, `agents/core/analyst.yaml`, `agents/core/qc_reviewer.yaml`, `agents/core/sci_reviewer.yaml`, `tests/test_step_prompt_rules.py`, `tests/test_agent_prompts.py`, `tests/test_role_footer.py`, `tests/test_output_types.py`.

## 2026-10-03 · #57 — 결정함: 새 승인이 와도 쓰던 답·메모가 지워지지 않음

- 결론: 결정함에서 답이나 메모를 쓰는 중에 새 승인이 오거나 화면이 다시 그려져도 커서가 사라지지 않는다.
- 바뀐 것: `syncDecisionCards`가 렌더마다 모든 카드를 `replaceChildren`으로 다시 끼워, 입력 중인 textarea가 DOM에서 잠깐 빠져 포커스가 body로 돌아갔다. 이제 끝난 카드만 빼고 새 카드만 끼우며, 제자리 카드는 건드리지 않는다. 2.5D와 3D가 같은 함수를 써서 둘 다 고쳐진다. 입력값 보존은 PR #74에서 이미 됐고 이번에는 포커스다.
- 검증: `tests/web_decision_focus.cjs`(신규, 포커스 fixup을 흉내 낸 DOM으로 2.5D·3D 검사)와 `web_live`·`web_command_center`·`web_clarify_options`·`web_issue184`·`test_web_3d.py`가 통과(Node 5개 파일 모두 실패 0, pytest 30 passed·1 skipped). 기존 Node test의 가짜 DOM에는 `insertBefore`·`removeChild`를 더했다. headless Edge에서 포커스가 사라지는 것을 먼저 확인했다. `scripts/check_public.sh` 통과.
- 미해결: 2.5D test는 공유 `syncDecisionCards`만 직접 부르고 `index.html`의 `renderApprovals`는 돌리지 않는다. 나중에 그 함수가 `#approvals`나 상위 요소를 다시 만들면 이 test는 못 잡는다. 지금은 `index.html`의 2.5D 경로가 `syncDecisionCards`만 부른다.
- 근거: `labhq/web/ui/decide.js`, `tests/web_decision_focus.cjs`, `tests/web_live.cjs`, `tests/web_command_center.cjs`, `tests/web_clarify_options.cjs`, `tests/web_issue184.cjs`, `tests/test_web_3d.py`.

## 2026-10-03 · #298 모의 시운전 — 따옴표·주석·heredoc 안의 `>`를 쓰기 대상으로 보지 않음

- 결론: 게이트의 셸 쓰기 검사가 문자열·주석·heredoc 본문 속 `>`를 리다이렉트로 읽어 이유 없이 승인 카드를 내던 오탐을 줄였다. 진짜 리다이렉트는 그대로 묻는다. 2차 모의 시운전의 두 건(PowerShell `.Replace('…<s1 venv>…')`, `python - <<'EOF'` 본문 속 `<s1 venv>`)이 더는 뜨지 않는다.
- 바뀐 것: `_shell_write_targets`가 리다이렉트·명령 이름을 찾기 전에 문자열·주석·heredoc 본문·PowerShell here-string·이스케이프를 같은 길이로 가린다. 가리는 것은 줄의 모든 명령이 인용문을 텍스트로만 읽을 때뿐이다(echo·cat·printf·grep·cd·ls, 하위 명령이 데이터뿐인 git, PowerShell Write-Output·Get/Set-Content·문자열 메서드, 인라인 python·Rscript·node 코드에 프로세스 호출이 없을 때). 그 밖의 명령, 셸이 다르게 읽을 여지(짝 없는 따옴표, 큰따옴표 안의 `$( )`, 구분자에 `$`가 있는 heredoc, `\r`, 괄호 안 `<<`)는 예전처럼 원문을 훑는다. 원문 훑기도 `>`마다 대상을 읽는다.
- 실행한 것: `tests/test_shell_write_quotes.py` 104건 통과(회귀 40건은 수정 전 모두 실패). Git Bash·Windows PowerShell에서 실제로 실행해 표식 파일이 생긴 명령 128개를 대조했고 수정 뒤 가려지는 것은 0개다(수정 전 bash 20, PowerShell 12). `scripts/check_public.sh` 통과.
- 미해결: 이름을 쪼갠 `b''ash`, 명령 위치가 아닌 곳의 따옴표 이름(`env "bash" -c`), alias, python·node가 `os.system`·`subprocess`로 셸을 부르는 경우는 못 잡는다. 셸 문법상 리다이렉트가 아닌 `>`(`[[ a > b ]]`, PowerShell `(1 > 2)`)는 오탐으로 남는다. `"$(cat <<'EOF' … EOF)"` 커밋 메시지 형태는 원문 훑기로 돌아가 카드가 뜬다.
- 근거: `labhq/policy.py`, `tests/test_shell_write_quotes.py`.

## 2026-10-03 · #58 — labhq verify와 요청별 감사 번들, 내장 MCP 실패 의미(④⑥)

- 결론: `labhq verify <요청>`이 runner PC에서 산출 파일이 기록 때와 같은지 다시 확인하고, 연구 요청은 보고서 앵커도 다시 검사한다. `--bundle`은 감사 번들 zip을 만든다. 내장 MCP(hpc·ask)의 실패는 isError로 돌아가고 "이 실패는 증거도 부재 증명도 아닙니다"가 붙는다.
- 바뀐 것: 새 `labhq/evidence/audit.py`가 gateway 요청 기록과 작업 폴더를 읽어 #334의 outputs walker로 산출을 다시 해시한다. 결과는 ok·mismatch·missing·unreadable·unchecked·unrecorded로 나뉘고, exit 0은 문제 없음, 1은 불일치·없는 파일·읽지 못함·확인 못함·앵커 문제, 2는 요청이나 작업 폴더 없음이다. 보고하지 않은 산출(unreported_outputs)은 경고로만 보인다. 번들은 README.md·claims.json·artifacts.json만 담고 산출 파일은 넣지 않는다. 기록된 해시가 없는 산출도 지금 없으면 missing이다. approval broker 실패는 deny로 두고 같은 문구만 붙였다. ask는 broker 연결 실패와 HTTP 400 외 오류만 isError로 돌리고 400(질문 형식 오류)은 rejected 답으로 남겼다.
- 검증: `tests/test_labhq_verify.py`(신규), `tests/test_hpc_failures.py`(17개 param에 문구 단언, ask broker 실패 test 추가), `tests/test_ask_review4.py`를 함께 돌려 170 passed, 1 skipped. 해시 기록이 없는 산출의 삭제 test 3건은 수정 전에 실패했다. `scripts/check_public.sh` 통과. 전체 pytest는 CI에 맡겼다.
- 미해결: #58 ① 일반 단계 결과 계약, tool_use_id, 일반 보고서의 도구 실패 경고. max_turns wrap-up이 첫 실행의 output_sha256을 두고 outputs만 합쳐 고쳐 쓴 산출이 mismatch로 나올 수 있다(기록 쪽, #334). 비어 있는 선언 폴더는 missing으로 나온다.
- 근거: `labhq/evidence/audit.py`, `labhq/cli.py`, `labhq/runner/workspace.py`, `labhq/tools/_mcpcompat.py`, `labhq/tools/hpc_mcp.py`, `labhq/tools/ask_mcp.py`, `labhq/tools/approval_mcp.py`, `tests/test_labhq_verify.py`, `tests/test_hpc_failures.py`, `tests/test_ask_review4.py`.

## 2026-10-03 · trial2-followups — 모의 시운전 F5와 직원 지침의 승인 카드 줄이기

- 결론: 리뷰가 상한 뒤에도 수정을 요구한 요청이 단계별 결과 나열 대신 CSO 최종 보고서를 남기고, 쓰기 직원이 임시 파일·변수 경로로 승인 카드를 띄우는 일을 줄인다.
- 바뀐 것: 미해결 리뷰도 합성을 돌리고 보고서에 "해결되지 않은 리뷰 지적" 절을 둔다. 요청은 failed와 `outcome=review_unresolved`를 유지하고, 재시작도 같은 경로를 탄다. 합성 실패나 예산 거부는 기존처럼 단계별 결과로 끝난다. 쓰기 직원 지침(`role_footer`)에 `.tmp/` 임시 폴더와 상대 경로 규칙 두 줄이 붙고 read-only task에는 붙지 않는다.
- 검증: 관련 test 4개 파일 452 passed, 1 skipped. 공개 저장소 검사 통과.
- 미해결: 합성 프롬프트가 리뷰를 3000자로 잘라 넘겨 그 너머의 지적은 CSO에게 보이지 않는다. 합성이 성공하면 보고서·이벤트에 "왜 failed인지"가 기계적으로 남지 않는다. `req["review"]`에는 전체가 있다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `labhq/adapters/base.py`, `tests/test_cso.py`, `tests/test_state.py`, `tests/test_private_paths.py`, `tests/test_role_footer.py`.

## 2026-10-03 · trial2-fixes — 2차 모의 시운전 결함 3건

- 결론: 수정 재실행 순서, Codex 직원 cache 권한, 직원 간 Codex 로그인 파일 노출을 고쳤다.
- 바뀐 것: `run_dag`가 이번 재실행의 전이적 조상을 기다린다. 쓰기 가능한 Codex task는 작업 폴더 안 cache를 쓰며 PI env가 우선한다. 다른 엔진 task에는 직원 `CODEX_HOME`을 개인 경로로 막는다.
- 검증: 새 회귀 테스트는 수정 전 3 failed/2 passed, 수정 후 5 passed. 관련 파일은 154 passed, 34 passed/1 skipped, 235 passed/1 skipped, 30 passed. 공개 저장소 검사도 통과했다.
- 미해결: 리뷰에서 지적되지 않은 중간 단계는 자동 재실행하지 않는다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/adapters/codex.py`, `labhq/private_paths.py`, `tests/test_cso.py`, `tests/test_read_only_followups.py`, `tests/test_private_paths.py`.

## 2026-10-03 · PR 준비 — 연구 lane CP2 뒤 리뷰·보고서 완주와 claim 앵커 검사(#90, #58 ③⑤)

- 결론: CP2에서 승인하면 리뷰 한 번 → CSO 보고서 → claim 앵커 검사까지 간다. 결과는 `research_reported`, `report_incomplete`, `research_review_revise`, `research_review_unparsed` 중 하나로 끝난다.
- 바뀐 것: 연구 전용 리뷰 스키마 `RESEARCH_LANE_REVIEW_SCHEMA`와 리뷰·합성 프롬프트를 더했다. 앵커 검사는 `labhq/evidence/report_check.py`, 결과는 `research_contract.report_check`, 리뷰는 `research_contract.review`(재시작 뒤 재사용)에 남는다. 실패한 조회는 보고서 메타데이터에 따로 붙는다. generic 리뷰·합성과 `evidence_checkpoint` off의 프롬프트는 그대로다.
- 실행한 것: 새 test 15건 중 수정 전에 돌린 14건이 모두 실패했고, 수정 뒤 15건 모두 통과했다. 관련 pytest 416 passed, 공개 저장소 검사 통과.
- 미해결: `reviewer_agent`가 없으면 리뷰·보고서 없이 `evidence_approved`로 끝난다. 웹 피드는 연구 리뷰 이벤트를 따로 받지 않는다(웹 변경은 범위 밖).
- 근거: `labhq/orchestrator/cso.py`, `labhq/evidence/report_check.py`, `labhq/research/packs.py`, `tests/test_research_report.py`, `tests/test_report_check.py`.

## 2026-10-03 · #298 ⑤ — Claude 직원 전용 설정 폴더와 긴 출력 다시 읽기

- 결론: PI 결정 "나". Claude 직원은 Codex 직원처럼 전용 설정 폴더(`engines.claude_code.env.CLAUDE_CONFIG_DIR`, init 기본 `~/.labhq/claude-staff`)를 쓴다. PI `~/.claude`는 통째로 막힌 채 두고, 직원 폴더도 기본 개인 경로에 들어가되 그 task 몫의 `projects/<작업 폴더 slug>/`만 읽기로 열린다. 그래서 Claude가 저장한 긴 도구 출력을 직원이 다시 읽는다.
- 바뀐 것: 게이트는 열린 폴더를 담은 개인 경로를 표기(`..` 정리)와 실제 경로가 모두 그 안일 때만 Read·Grep·Glob에 허용한다. Claude deny 규칙은 그 경로의 Edit·Write를 통째로 막고, Read는 설정 폴더 최상위 항목만 하나씩 막는다(`projects`는 뺀다. `.credentials.json`·`.claude.json`·`history.jsonl`은 아직 없어도 넣는다). `projects/` 아래 다른 task 폴더는 규칙 없이 게이트가 거부하므로 task가 쌓여도 `--settings` 명령줄이 늘지 않는다. runner는 열린 폴더를 `LABHQ_PRIVATE_OPEN_READS`로 게이트에 넘기고 Claude read root에 더한다(Edit rule 없음). 직원 지침의 "`~/.claude` 아래 긴 출력은 안 열릴 수 있음" 줄은 열린 폴더 안내로 바뀐다. doctor `claude staff config` 행: 미설정 warn, PI `~/.claude`이거나 그 안 fail, 로그인 없음 warn(+로그인 명령). init은 PI `~/.claude`가 있으면 폴더와 env를 넣고 로그인 명령만 보여 준다. `CLAUDE_CONFIG_DIR`가 없으면 전과 같다.
- 실행한 것: slug 규칙을 실제 Claude 2.1.282(Windows 11)로 확인했다. 빈 `CLAUDE_CONFIG_DIR`로 `claude -p` 를 돌리면 로그인 없이도 `projects/<slug>/`가 생긴다. 영숫자 밖 문자는 `-`, 200자를 넘으면 앞 200자 + `-` + base36 |djb2|(fixture `claude_project_slug.json`). `claude auth status`는 빈 폴더에서 rc 1. 새 test 29건(slug, 열린 폴더 Read 허용, 로그인 파일·다른 slug·history·쓰기·`..`·대소문자·admin share·link 우회 거부, deny 규칙, read root, isolation, runner, 승인 서버, doctor 8건)과 init 3건. 반영 전에는 새 파일이 import에서, init 2건이 단언에서 실패했다. 형제 폴더 300개를 두면 폴더별 deny 규칙이 명령줄을 수정 전 157,554 UTF-16 units(한도 32,000)로 키워 task가 시작되지 못했다. 회귀 test 1건으로 막았다. 관련 test 5개 파일 416 passed/1 skipped, `scripts/check_public.sh` 통과.
- 미해결: 실제 Claude 직원 실행으로 긴 출력 다시 읽기는 확인하지 않았다(PI 로그인 필요). `CLAUDE_CONFIG_DIR`는 `engines.claude_code.env`만 본다(runner 자체 env의 값은 열지 않음). Codex 직원 `CODEX_HOME`(`~/.labhq/codex-staff`)은 Claude 직원에게 개인 경로가 아니다(기존 동작).
- 근거: `labhq/private_paths.py`, `labhq/policy.py`, `labhq/doctor.py`, `labhq/init_wizard.py`, `tests/test_claude_staff_home.py`, `tests/fixtures/real/claude_code/claude_project_slug.json`.

## 2026-10-03 · PR 준비 — 관찰된 산출물 manifest(#58 ②)

- 결론: 직원 보고와 별개로 run 전후 `outputs/` 차이와 sha256을 기록하고, CP2가 묶인 artifact hash를 receipt에 남기게 했다.
- 바뀐 것: `runs.<task_id>.observed_outputs`, `TaskResult.output_sha256`·`unreported_outputs`, `runner.output_hash_max_bytes`를 추가했다. CP2 detail·receipt에는 `artifact_sha256`과 단계별 미보고 산출물이 들어간다.
- 실행한 것: 새 회귀 5건이 수정 전 실패했고 수정 뒤 통과했다. 관련 pytest는 101 passed·3 skipped, 공개 저장소 검사와 diff 검사를 통과했다.
- 미해결: 엔진별 `tool_use_id` 연결은 범위 밖이다.
- 근거: `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `labhq/models.py`, `labhq/research/contract.py`, `labhq/orchestrator/cso.py`, `tests/test_observed_outputs.py`, `tests/test_research_cp2.py`.

## 2026-10-03 · PR #333 (#331) — 모의 시운전 작은 결함 네 건

- 결론: 시운전 1차에서 나온 작은 결함 네 건(ask 500, 실패 보고서 크기, 접근 로그 서식 오류, 보기 표시 중복)을 고쳤다.
- 바뀐 것:
  - `labhq_ask`의 `wait`가 MCP schema에서 `short`·`hibernate` enum이 됐다. runner broker는 `/ask`·`/approval` 본문 검증에 실패하면 500 대신 400과 `fix and call again: <필드>: <이유>`를 돌려주고, ask 도구는 이것을 연결 실패가 아닌 rejected 답으로 전한다.
  - 실패한 요청의 보고서는 `report_results`가 따로 만든다. 성공 단계는 결과 요약 1,200자와 산출 경로(10개까지), 실패 단계는 원인 한 줄·뿌리 원인(3개까지)·`Next:`만 싣는다. 지시문은 160자로 자른다. reviewer·synthesis 입력(`format_results`)은 그대로다.
  - 로그 token 가림 필터는 인자별로 가려도 같은 줄이 나오면 `args`를 남긴다. uvicorn `AccessFormatter`가 인자 다섯 개를 그대로 받는다. token이 서식과 인자에 걸치거나 예외가 붙은 레코드만 전처럼 한 줄로 편다.
  - 보기 글 앞의 `a)`·`(a)` 표시는 `normalize_questions`·`ClarifyingQuestion`과 결정함 `decide.js`에서 뗀다. `A. thaliana`처럼 괄호 없는 글은 그대로 둔다.
- 실행한 것: 새 test 7건(ask 400 세 경우, MCP schema·400 전달, 12단계 실패 보고서, 접근 로그 서식, 보기 중복 Python·Node)이 수정 전 실패하고 수정 뒤 통과했다. 12단계 실패 test 보고서는 666,732자에서 11,152자가 됐다. 관련 test 파일 36개 1,202 passed·16 skipped, Node 3개 통과.
- 미해결: 보고서의 `Step status and output paths` 절은 산출 경로 수를 자르지 않는다(원인 문구만 200자로 자른다).
- 근거: `labhq/runner/approvals.py`, `labhq/tools/ask_mcp.py`, `labhq/orchestrator/cso.py`, `labhq/security.py`, `labhq/intake.py`, `labhq/web/ui/decide.js`, `tests/test_ask.py`, `tests/test_cso.py`, `tests/test_security.py`, `tests/test_intake_questions.py`, `tests/web_clarify_options.cjs`.

## 2026-10-03 · PR #332 (#330) — turn이 끝난 뒤 남은 직원 프로세스를 유예 뒤 정리

- 결론: 직원 CLI가 마지막 turn 이벤트를 낸 뒤 `runner.exit_grace_s`(기본 45초) 안에 끝나지 않으면 runner가 프로세스 트리를 끝내고, 이미 받은 결과로 단계를 마친다. 모의 시운전에서 Codex lit_scout가 turn을 끝내고도 `codex exec`가 남아 s3이 46분 넘게 `running`이었던 문제다.
- 바뀐 것: `labhq/adapters/base.py` `run()`에 exit guard. adapter가 `result_seen`을 켠 뒤 프로세스가 유예 안에 끝나지 않으면 `agent.log` warn 한 줄을 남기고 기존 `_kill`(Windows `taskkill /T /F`, POSIX 프로세스 그룹)로 정리한다. 이때 종료 코드는 0으로 보고 결과를 유지한다. Claude·Gemini·Antigravity·cli adapter도 같은 guard를 받고, 유예 안에 끝나는 정상 경로는 그대로다.
- Codex: `turn.failed`도 turn 종료로 본다. MCP stdio 서버 env 가운데 Codex 프로세스 env에 같은 값이 있는 키(broker URL·token·task id 등)는 `-c mcp_servers.<name>.env_vars=[…]`로 이름만 넘기고, 나머지(PYTHONPATH 등)만 `env={…}`에 남긴다. Codex는 이미 runner에게서 같은 env를 받으므로 새로 드러나는 곳은 없다.
- 실행한 것: 새 `tests/test_exit_grace.py` 6건(남는 fake Codex·Claude가 유예 1초 뒤 결과와 함께 끝나고 종료 로그 한 줄, 유예 안에 끝나는 fake와 exit 3 fake는 영향 없음, broker token이 argv에 없고 fake env에는 있음). 반영 전 3건 실패, 회귀 3건 통과를 확인했다. 관련 test 16개 파일 544 passed.
- 미해결: 프로세스는 끝났는데 손자 프로세스가 stdout을 잡고 있는 경우는 guard 대상이 아니다(`task_timeout_s`가 푼다). `env_vars`는 실제 Codex로 확인하지 않았다(codex.exe 안에 필드명은 있음). 실제 Codex로 MCP 호출 한 번을 돌려 봐야 한다.
- 근거: `labhq/adapters/base.py`, `labhq/adapters/codex.py`, `labhq/settings.py`, `tests/test_exit_grace.py`.

## 2026-10-03 · #328 — Codex 판본이 바뀌면 직원 sandbox 준비를 다시 확인

- 결론: 직원 `CODEX_HOME`의 elevated sandbox 준비가 지금 쓰는 Codex 판본에서 확인됐는지 labhq가 기록하고 비교한다. 10-03 00:27 앱 업데이트 뒤 marker 파일만 보던 doctor는 ok였지만 engineer 단계는 `ELEVATED_SETUP_ERROR`로 멈췄다.
- 바뀐 것: `labhq/runner/codex_sandbox.py`를 새로 두었다. Windows elevated 실행이 성공하면 runner가 `CODEX_HOME/.labhq-sandbox-ok.json`에 판본·실행 파일 표시·시각을 쓴다. 판본은 runner 프로세스마다 한 번 읽는다. doctor는 `setup_marker.json`이 있는 home마다 `codex sandbox version` 행을 낸다. 기록과 현재 `codex --version`이 같으면 ok, 다르거나 기록이 없으면 warn과 PowerShell 명령(`CODEX_HOME`, `$env:TEMP` 아래 probe 폴더, `$env:LOCALAPPDATA` 기준 codex.exe, `exec -s workspace-write -c 'windows.sandbox="elevated"'`, `Test-Path`)을 보인다. `labhq init`도 Windows에서 로그인 명령 뒤에 같은 명령을 출력한다.
- 알림: 준비 오류(UAC helper 취소, 또는 Codex의 "sandbox setup required" 메시지)로 단계가 실패하면 runner마다 한 번만 `agent.log` alert와 결정함 카드(`codex_sandbox_setup`, 승인·거절 어느 쪽이든 닫기만 함)를 낸다. alert는 피드에만 떠서 결정함 카드를 함께 쓴다.
- `bin: auto`: codex.exe가 있는 앱 폴더만 후보로 보고, 각 `codex.exe --version`(바이너리마다 한 번)이 모두 읽히면 판본이 가장 높은 곳, 하나라도 못 읽으면 가장 최근 폴더를 고른다. doctor engine 행에 고른 폴더와 이유를 적는다.
- 실행한 것: 새 test 14건(`tests/test_codex_sandbox_version.py`) 중 새 모듈만 둔 main 코드에서 doctor·auto·스트림·runner 연결 10건이 실패하고 이 branch에서 모두 통과했다. 관련 test 파일 20개 750 passed, `tests/web_state.cjs` 통과. 실제 Codex CLI는 돌리지 않았다(fake만).
- 후속 수정(6730e43): 기록은 실행이 띄운 launcher(`RunContext.started_command`)로 판본을 읽고, 셸 명령이 exit 0으로 끝난 실행만 적는다. 실행 중 `bin: auto`가 새 빌드로 바뀌거나 명령 없는 턴이 성공해도 깨진 준비를 정상으로 적지 않는다. "sandbox setup required"는 `turn.failed`·`error`나 Codex의 spawn 오류 출력에서만 준비 오류로 본다. 준비 명령은 앱 폴더 밖 bin이나 `prefix_args`면 `'<engines.codex.bin>'`을 보이고, probe의 `ok.txt`를 먼저 지운다. 새 test 8건이 수정 전 코드에서 7건 실패하고 지금 통과한다.
- 미해결: Codex가 sandbox 준비 유효성을 무인으로 판정하는 공식 방법은 찾지 못해 판본 기록으로 대신했다. PI가 명령으로 다시 준비한 뒤에도 다음 Codex 직원 작업이 성공할 때까지 doctor는 warn이다.
- 근거: `labhq/runner/codex_sandbox.py`, `labhq/adapters/base.py`, `labhq/doctor.py`, `tests/test_codex_sandbox_version.py`.

## 2026-10-03 · PR #327 — 레지스트리 접근을 PI 승인으로 (#325)

- 결론: 개인 경로(`policy.private_paths`)가 켜진 task에서 Claude 직원의 Bash·PowerShell 명령이 레지스트리에 손대면 키와 상관없이 승인 게이트가 PI에게 묻는다. PI `GITHUB_TOKEN`은 직원 프로세스 환경에서는 지워지지만(#301) 같은 계정의 레지스트리(`HKCU\Environment`)에는 남아 있어서다. 판정을 조이기만 하고 느슨하게 만들지 않는다.
- 바뀐 것: `labhq/private_paths.py`에 `REGISTRY_ACCESS` 정규식과 `registry_access`. `labhq/policy.py` `_private_decision`의 셸 판정 끝에 연결. README §8 개인 경로 절에 한 줄.
- 잡는 표기: hive 이름(`HKCU`·`HKLM`·`HKU`·`HKCR`·`HKCC` 단어, `HKEY_…` 전체 이름), PowerShell Registry provider(`Registry::`·`-PSProvider Registry`·`New-PSDrive`/`ndr`/`mount`와 Registry), Git Bash `/proc/registry`, `reg`/`reg.exe` 하위 명령 전부·`regedit`·`regini`, `winreg`·`Microsoft.Win32.*`·`RegistryKey`·`[Registry]`·WMI `StdRegProv`, `Win32_Environment`·`wmic environment`, `[EnvironmentVariableTarget]::User`, `GetEnvironmentVariable(s)`의 Process 아닌 대상. 대조 전에 줄 이어쓰기(PowerShell backtick·cmd `^`·sh `\` + LF/CRLF)를 붙이고, 따옴표·backtick·`^` 쪼개기를 지운다.
- 경과: 처음에는 `Environment` 키 표기만 셌다. 리뷰 세 차례가 같은 부류의 더 좁은 표기(`..` 우회, 다른 이름의 PSDrive, hive 이름 속 줄 이어쓰기)를 냈고, 레지스트리 접근 전체로 넓혀 그 부류를 닫았다.
- 활성 경로 0개(설정한 경로가 하나도 없거나 모두 작업 폴더를 담아 빠진 경우)도 켜진 것으로 본다(PR #327 리뷰). runner가 `LABHQ_PRIVATE_PATHS_ENABLED`로 켜짐 여부를 따로 넘기고, 셸 미리 허용 해제·Read 좁히기·레지스트리 검사가 그대로 돈다. `policy.private_paths: []`만 모두 끈다.
- 그대로 허용: `$env:X`·`%X%`·`set`·`printenv`·`os.environ`·`Get-ChildItem Env:`·Process 대상 `GetEnvironmentVariable`, `git`·`python script.py`·평범한 grep, `outputs/hkcu` 같은 경로 조각.
- 받아들인 오탐: 커밋 메시지·grep 패턴에 이 단어가 들어가면 묻는다. 다른 키를 읽는 `reg query HKCU\Software\Python`·`echo HKCU`도 이제 묻는다.
- 실행한 것: `tests/test_private_paths.py` 174 passed·1 skipped(새 test 59건, 승인 표기 36건은 반영 전 실패 확인), 관련 test 7개 파일 572 passed. 리뷰 반영 뒤 `tests/test_private_paths.py` 208 passed·1 skipped(활성 경로 0개 test 6건은 반영 전 실패 확인), 관련 test 22개 파일 838 passed. 레지스트리 전체로 넓힌 뒤 `tests/test_private_paths.py` 234 passed·1 skipped(새 test 30건 중 18건은 반영 전 실패 확인), 관련 test 6개 파일 632 passed.
- 미해결: 글자 대조라 실행 중 조립한 레지스트리 접근(문자열 조립, workdir에 쓴 스크립트)은 못 잡는다. 실제 격리는 #298에 적은 OS 수준 선택지가 필요하다. token 범위를 필요한 저장소로 좁히는 것이 1차 방어다.
- 근거: `labhq/private_paths.py`, `labhq/policy.py`, `tests/test_private_paths.py`.

## 2026-10-03 · readme-concepts — README 핵심 개념·버전 로드맵

- 결론: 새 독자는 README §0-2 표 하나로 본문 용어를 따라가고, 로드맵과 작업 큐는 PI 버전 순서(v0.25 → v1.25)를 따른다.
- 바뀐 것: README에 핵심 개념 25행 표(§0-2)와 버전 표(§11)를 넣었다. HANDOFF 작업 큐에서 병합된 PR을 빼고 버전 순으로 다시 묶었으며 #273·#275를 PI 결정으로 옮겼다. 사실 확인 뒤 CP2 조건(`evidence_checkpoint`)을 밝히고 #325와 10-03 실행 계정 결정을 넣었다.
- 실행한 것: `scripts/check_public.sh`, `patch_notes.py check`, `notes_index.py --validate`, `tests/test_integrations.py`.
- 미해결: 개인 경로 차단(PR #324)이 병합되면 핵심 개념 표에 `policy.private_paths` 행을 더한다. P3 HPC·거버넌스·bench 시점은 PI 결정 대기.
- 근거: `README.md`, `HANDOFF.md`, `docs/research_protocol.md`.

## 2026-10-03 · PR #324 — 같은 계정 실행 기본과 개인 경로 차단

- 결론: gateway·runner·직원 CLI를 PI 계정에서 돌리는 것을 기본으로 한다(PI 결정 2026-10-03 "가"). PI 개인 파일은 `policy.private_paths`로 Claude 직원에게 세 겹(파일 도구 거부, 셸은 모두 게이트로, 지침), 다른 엔진 직원에게는 지침 한 겹으로 막는다.
- 바뀐 것: `labhq/private_paths.py` 새 모듈, 승인 게이트·runner·adapter 역할 지침·doctor `private paths` 행, README §8 "PI 개인 경로", runner 전용 계정 문서는 고급 선택지로 표시.
- 리뷰 반영 1·2차: 셸 `ask` 규칙, 8.3 짧은 이름·`\\localhost\C$\`·`\\?\`·따옴표로 쪼갠 표기·`/./`·`..`, 예시 설정 동기화, recruit task의 `~/.claude` 제외, 홈 밖 항목 라벨, doctor 힌트와 README §10.
- 리뷰 반영 3차: Claude 규칙은 글자 그대로 비교해 대소문자를 바꾼 표기나 link alias가 ask·deny를 지나갔다. 그 부류를 한 번에 닫았다. 개인 경로가 켜진 task는 셸 규칙을 미리 허용하지 않고, Read·Grep·Glob 미리 허용은 작업·프로젝트·참고 폴더의 `Read(//폴더/**)`로 좁힌다. 그래서 셸 명령과 폴더 밖 읽기는 게이트가 정규화·실제 경로로 판정한다(2.1.282 실측: bare `Read`는 junction alias를 읽고, 좁힌 규칙은 승인으로 보냄). 개인 경로가 없는 셸 명령은 게이트가 바로 허용한다. POSIX 대소문자 구분 볼륨에서는 대소문자를 지킨다. plugin 폴더는 `engines.claude_code.env`로 펼친다(runner·doctor 같음).
- 리뷰 반영 4차(실측 probe): `cd <홈> && cat .sec/key.txt`를 게이트가 허용했다. 이제 `cd`·`pushd`·`Set-Location` 대상을 차례로 따라가 상대경로를 그 폴더 기준으로 다시 읽는다(글자 대조와 link 모두). `python -c`의 `os.path.join` 조립 경로와 작업 폴더에 써 둔 스크립트가 여는 경로는 글자 대조로 닫을 수 없다. 모든 인터프리터 호출을 승인으로 보내면 평소 작업(`python script.py`)이 막히고, Claude는 Windows 네이티브에 sandbox가 없다. 그래서 README에 "작정한 직원은 못 막음, 필요하면 전용 계정"으로 적고 PR 남은 지적에 올렸다.
- 실행한 것: `tests/test_private_paths.py` 93 passed·1 skipped(3차 test 17건, 그중 6개 함수는 반영 전 실패), 관련 기존 test 19개 파일 포함 통과, 공개 검사 통과.
- 미해결: Codex·Gemini·Antigravity·cli 직원의 파일·셸 읽기는 막지 못한다(지침뿐). Claude 직원도 glob·실행 중 만든 경로(조립한 경로, 직접 써 둔 스크립트)는 놓친다. 셸과 작업 폴더 쓰기가 다 있는 직원은 두 단계로 모든 개인 경로를 읽을 수 있다. `approval`이 없는 Claude 직원은 개인 경로가 켜지면 셸이 모두 거부된다(작업 로그 경고). `~/.claude` 아래 큰 도구 출력(MCP 포함) 저장본은 다시 읽지 못할 것으로 보나 실측 전이다.
- 근거: `labhq/private_paths.py`, `labhq/policy.py`, `labhq/runner/daemon.py`, `labhq/doctor.py`, `tests/test_private_paths.py`, `tests/fixtures/real/claude_code/claude_read_scope_alias.json`.

## 2026-10-02 · PR #323 — consult ref 격리(#86 #165)

- 결론: consult에는 staging 복사본만 보이고, 같은 질문도 ref가 다르면 cache를 재사용하지 않는다.
- 바뀐 것: runner가 source 경로를 manifest·prompt에서 지운다. ref는 workspace root부터 폴더 handle을 따라 상대 open하며, 검증 뒤 부모가 link·junction으로 바뀌면 제외한다.
- 실행한 것: 새 회귀 3건이 수정 전 실패하고 수정 후 통과했다. `tests/test_ask.py` 20건과 공개 검사를 통과했다.
- 해결: source 절대 경로 노출, ref 부모 TOCTOU, ref가 다른 ask의 cache 충돌을 막았다.
- 미해결: CI와 봇 재검토 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/adapters/held_dir.py`, `labhq/orchestrator/cso.py`, `labhq/runner/daemon.py`, `tests/test_ask.py`.

## 2026-10-02 · 소유 경로 후속(#217 #226)

- 결론: 소유 경로 경쟁 방지에 더해 공백 포함 home 계정 가림과 HPC 준비의 검증 순서를 바로잡았다.
- 바뀐 것: 외부 parent traversal을 `jobs/logs` 생성 전에 검사하고, link 교체 오류를 `RuntimeError`로 통일했다. bare `~`는 뒤에 경로 구분자가 있을 때만 공백 포함 account component를 가린다.
- 실행한 것: 새 가림 회귀의 수정 전 실패를 확인했다. 수정 뒤 관련 pytest 143건 통과·31건 skip, 공개 저장소 검사·compile·diff 검사를 통과했다. README는 87,936 bytes에서 88,049 bytes로 113 bytes 늘었다.
- 미해결: POSIX 전용 두 회귀는 이 Windows PC에서 skip됐다. GitLab 탐색 URL P2는 이번 PR에서 고치지 않는다.
- 근거: `labhq/intake.py`, `labhq/tools/hpc_mcp.py`, `tests/test_intake_references.py`, `tests/test_mcp_servers.py`, `tests/test_runner_safety.py`, `tests/test_workspace_boundary.py`.

## 2026-10-02 · 작은 후속 세 건(#241 #244 #305)

- 결론: 오류 출력 주석, pack rule 동시 검증, input-fit 집계 키를 바로잡았다.
- 바뀐 것: extra field와 독립적인 rule 위반을 함께 알리고, input-fit을 `관계표12/vocab12`로 나눈다.
- 실행한 것: 회귀 2건의 수정 전 실패를 확인했고, 관련 pytest 91건과 공개 저장소 검사를 통과했다.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어서 확인한다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/research/contract.py`, `labhq/research/semantics_shadow.py`, `tests/test_research_protocol.py`, `tests/test_semantics_input_fit.py`.

## 2026-10-02 · AGENTS.md 간결 운영 규칙

- 결론: 에이전트 규칙을 PI 결정(2026-10-02)에 맞췄다. 로컬은 관련 test만, 전체 suite는 CI, P2는 PR 본문 "남은 지적" 한 줄.
- 바뀐 것: `AGENTS.md` 두 줄.
- 실행한 것: 공개정보 검사, 기록 검사. 코드 변경 없음.
- 미해결: 없음.
- 근거: `AGENTS.md`.

## 2026-10-02 · Windows bench 취소 test 불안정 수정

- 결론: Windows가 `taskkill` 생성 중 `MemoryError`를 내도 이미 수집한 PID로 process tree를 정리한다.
- 바뀐 것: `_kill()`의 직접 종료 fallback에 Windows 자원 부족을 포함하고 해당 실패를 회귀 test에 주입했다.
- 실행한 것: 대상 test 30회 통과, `tests/test_bench.py` 55건 통과, 공개 저장소 검사 통과.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어받는다.
- 근거: `labhq/adapters/base.py`, `tests/test_bench.py`.

## 2026-10-02 · 인용·pack 후속(#196 #197 #218)

- 결론: accession version 재인용, URI 혼합 후보, `pack_dirs` 시작 검증을 보강했다.
- 바뀐 것: 여러 표기의 base key를 원장에 넣고, 여러 표기 체계의 URI가 정확한 ID와 다른 accession을 함께 반환하면 충돌로 판정한다.
- 실행한 것: 수정 전 회귀 3건 실패를 확인했고, 관련 pytest 131건과 공개 저장소 검사를 통과했다.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어서 확인한다.
- 근거: `labhq/evidence/claims.py`, `labhq/evidence/verify.py`, `labhq/research/packs.py`, `tests/test_evidence_verify.py`, `tests/test_research_protocol.py`.

## 2026-10-02 · 그림자 후속(#211 #212)

- 결론: 이전 epoch의 breaker 저장 실패가 새 epoch를 끄지 않으며, `derived_from`은 A·B 모두 최대 64 artifact hop까지만 `yes`다.
- 바뀐 것: breaker 저장 실패에 epoch 조건을 붙이고, N=64·65·66 계보 경계를 `yes`·`unknown`·`unknown`으로 고정했다.
- 실행한 것: 수정 전 두 회귀 실패를 확인했다. 수정 뒤 관련 pytest 160건과 공개 저장소 검사·diff 검사를 통과했다. 기존 17문항 판정은 바뀌지 않았다.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어받는다.
- 근거: `labhq/research/semantics_shadow.py`, `labhq/research/semantics.py`, `tests/test_semantics_shadow_breaker.py`, `tests/test_semantics_pilot.py`.

## 2026-10-02 · HPC 승인·추적 후속(#215 #224)

- 결론: 시험 job이 세 번 연속 사라지면 `unknown_finished`로 끝내고, 스케줄러 우회와 오래된 job 취소를 막았다.
- 바뀐 것: 예약 명령과 줄바꿈 `scontrol`을 승인 대상으로 넣고, Slurm 지시문을 셸 규칙대로 읽는다. 원격 PBS 제출은 항상 PI 승인을 받는다.
- 실행한 것: 수정 전 회귀 테스트 19건 실패 확인. 수정 뒤 관련 pytest 215건 통과, 2건 skip. 공개 저장소 검사 통과.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어받는다.
- 근거: `labhq/hpc_consult.py`, `labhq/tools/scheduler.py`, `labhq/runner/daemon.py`, `labhq/runner/hpc_jobs.py`, `labhq/settings.py`, `tests/test_hpc_consult.py`, `tests/test_hpc_followups.py`.

## 2026-10-02 · #231~#234 — direct 산출 후속

- 결론: direct와 wrap-up 산출을 같은 규칙으로 모으고, 볼륨의 대소문자 구분에 맞춰 labhq 결과 사본을 뺀다.
- 바뀐 것: 대소문자 구분 판정, direct wrap-up 전체 산출 수집, 라운드 기록의 `outputs/` 경로 200개 상한 설명을 추가했다. #232는 main의 fd 기반 순회와 교체 회귀 테스트로 이미 해결돼 있었다.
- 실행한 것: 수정 전 새 회귀 3건 실패, 수정 뒤 관련 pytest 40건 통과·2건 skip. 공개정보 검사와 diff 검사를 통과했다. README는 86,587 bytes에서 86,686 bytes로 99 bytes 늘었다.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어서 확인한다.
- 근거: `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `labhq/orchestrator/cso.py`, `tests/test_direct_outputs.py`, `tests/test_resume_accounting.py`, `README.md`.

## 2026-10-02 · PR #313 — 3D 화면 후속 세 건

- 결론: 전문 보기 클릭 유실, terminal report snapshot 팽창, 이어 묻기 글자 수 단위 불일치를 고쳤다.
- 바뀐 것: 3D 요청 카드를 key로 재사용하고, 보고서 전문은 상세 API에서 받으며, 표시 길이는 Unicode 코드포인트로 센다.
- 실행한 것: 수정 전 Node·gateway 회귀 실패를 확인했다. 수정 뒤 관련 pytest 62건, Node 회귀, 공개 저장소 검사를 통과시켰다.
- 미해결: 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/web/lab3d/src/live.js`, `labhq/gateway/server.py`, `labhq/web/state.js`, `labhq/web/index.html`, `tests/web_issue126.cjs`, `tests/test_followup.py`.

## 2026-10-02 · #312 — 긴 직원 prompt 후속

- 결론: POSIX의 인자별 UTF-8 한도를 넘는 prompt도 TASK pointer로 바꾸며, Claude에 Read가 없으면 실행 전에 거부한다.
- 바뀐 것: spawn `OSError`를 실패 결과로 돌리고 #240·#242 회귀 테스트를 추가했다.
- 실행한 것: 수정 전 3건 실패를 확인했다. 관련 pytest 18건, Node test 12개 파일, `scripts/check_public.sh`를 통과시켰다.
- 미해결: 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/adapters/base.py`, `labhq/adapters/claude_code.py`, `tests/test_adapters_fake_cli.py`.

## 2026-10-02 · #199 #203 — 승인 시간 초과 정리와 stale 알림 범위

- 결론: 러너에서 만료된 승인은 gateway와 화면에서도 즉시 끝나며, stale 알림은 누른 화면에만 보인다.
- 바뀐 것: broker가 `approval.timed_out`을 보내면 gateway가 승인 기록을 지우고 `approval.resolved(state=timed_out)`를 배포한다. `approval.stale`은 해당 WebSocket에 직접 보낸다.
- 실행한 것: 수정 전 회귀 test 2건 실패 확인. 수정 뒤 관련 pytest 287건 통과, 1건 skip. 공개 검사와 diff 검사 통과.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어서 확인한다.
- 근거: `labhq/runner/approvals.py`, `labhq/runner/daemon.py`, `labhq/gateway/server.py`, `tests/test_approval_timeout.py`, `tests/test_web.py`.

## 2026-10-02 · PR #310 — 쓰기 게이트 경로 판정 후속

- 결론: 파일 도구의 환경 변수 표기는 펼치지 않고, 링크·junction으로 적힌 작업 폴더의 셸 쓰기는 원래 표기 안에서 허용한다.
- 바뀐 것: 파일 도구 경로 정규화만 환경 변수 미전개로 바꾸고, `LABHQ_WORKDIR` 원문을 셸 쓰기 루트에 추가했다.
- 실행한 것: 수정 전 회귀 테스트 2건 실패를 확인했다. 수정 뒤 관련 pytest 101건과 `scripts/check_public.sh`를 통과시켰다.
- 미해결: 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/policy.py`, `labhq/tools/approval_mcp.py`, `tests/test_claude_write_paths.py`.

## 2026-10-02 · PR #309 — consult·대기 후속(#205~#209)

- 결론: session 대기는 원장 반복 조회 없이 상태 변화에 깨어나며, 완료된 최신 turn을 잇는다. 직원 재실행과 재시작 전 facilities 질의도 같은 점유·route 규칙을 따른다.
- 바뀐 것: task·runner 상태 신호, 최신 session 추적, 고아 CSO 결과 반영, runner별 마지막 roster, 직원 step의 공통 `_free_session` 판정을 추가했다. #206은 main에서 이미 해결된 구현과 회귀를 확인했다.
- 실행한 것: 수정 전 4 failed/1 passed, 수정 후 관련 pytest 203 passed. 공개 저장소 검사와 diff 검사를 통과했다.
- 미해결: 없음. 전체 pytest와 CI는 지시대로 GitHub Actions에 맡긴다.
- 근거: `labhq/gateway/server.py`, `labhq/orchestrator/cso.py`, `tests/test_consult_restart.py`, `tests/test_cso.py`.

## 2026-10-02 · #308 · 느린 test 줄이기

- 결론: 본 pytest job은 90초 MCP 성질을 축소 시계로 검사하고, 실제 90초 실측은 별도 `pytest-slow` job(ubuntu, `LABHQ_SLOW_TESTS=1`)이 나란히 돌린다.
- 바뀐 것: 제거 test 둘은 한 번 만든 pristine lab state를 각자 복사해 쓴다. mock HPC polling과 종료 대기는 조건 기반으로 줄였다. `ci_skip.py`의 skip 판정에 `pytest-slow`를 넣었다.
- 실행한 것: 지정 5개 파일 58 passed, 1 skipped(168.75→72.31초). 수정 뒤 제거·integrations·ci_skip 35 passed(역순 실행도 통과), opt-in 실측 2 passed(94초), 공개 검사 통과. 전체 pytest는 CI에 맡긴다.
- 미해결: 없음.
- 근거: `.github/workflows/test.yml`, `scripts/ci_skip.py`, `tests/test_long_mcp_call.py`, `tests/fixtures/fake_mcp_client.py`, `tests/test_semantics_shadow_remove.py`, `tests/semantics_shadow_lab.py`, `tests/test_e2e_mock.py`.

## 2026-10-02 · PR #307 · 연구 CP2 증거 검토

- 결론: 연구 규약 pilot에 CP1 다음 checkpoint인 CP2 증거 검토를 선택적으로 연결했다.
- 바뀐 것: `evidence_checkpoint`를 켜면 frozen PLAN에 맞는 claim·evidence 결과만 받고, PI가 승인·수정 요청·거부를 선택값으로 고른다(메모는 읽지 않고, 선택값이 없으면 승인하지 않고 다시 묻는다). 모은 산출에 묶이지 않은 artifact를 인용한 evidence는 거부해 카드와 receipt에 이유를 남긴다. 단계가 PI 결정을 기다리면 원장 검사 전에 묻고 다시 실행한다. 재개는 요청에 고정된 실행 여부와 CP2 receipt를 유지한다. 연구 요청은 일반 재계획·리뷰에 들어가지 않고 실패하면 `research_failed`로 끝난다. 끄면 CP1에서 멈춘다.
- 실행한 것: 연구 규약 56건과 CP2 25건을 포함한 cso·재계획·승인·ask·CLI·웹 관련 테스트 1023건, 웹 `node tests/*.cjs`, 공개 저장소 검사를 통과시켰다.
- 미해결: CP2 수정 재실행, CP3·CP4, reviewer·감사 bundle 연결은 다음 조각이다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/research/contract.py`, `labhq/gateway/server.py`, `labhq/cli.py`, `labhq/web/ui/decide.js`, `tests/test_research_cp2.py`, `tests/web_cp2_evidence.cjs`.

## 2026-10-02 · #293 — plan-only 재개 대기 범위

- 결론: `plan_only` 요청은 plan 저장 뒤 gateway가 재시작돼도 실행하지 않을 직원을 기다리지 않고 계획만 끝낸다.
- 바뀐 것: plan 저장 전에는 CSO·briefing 담당만 기다리고, 저장 뒤에는 recovery agent를 요구하지 않는다. 두 경계를 회귀 테스트로 고정했다.
- 실행한 것: 수정 전 저장된 plan의 worker·reviewer·CSO를 기다리는 실패를 확인했다. 관련 pytest 70건과 `scripts/check_public.sh`, patch-notes·목차 검사를 통과시켰다.
- 미해결: 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/gateway/server.py`, `tests/test_state.py`.

## 2026-10-02 · PR #303 — 한 PC 여러 labhq 인스턴스(#37 결정 ②-1 A)

- 결론: `labhq init --instance <이름>`으로 한 PC의 연구소를 포트·경로·token까지 나누고, 모든 명령에서 같은 이름을 고를 수 있다. 기본 동작은 그대로다.
- 바뀐 것: 이름별 config·state·runs·talent·runner id·빈 gateway/broker 포트·무작위 token, `--instance`/`--config` 충돌 거부, 웹 제목·홈 화면 이름, 로그인·사용량 공유 경고.
- 실행한 것: 관련 pytest 58 passed, Node CJS 13개, `scripts/check_public.sh`, `git diff --check` 통과. 전체 pytest는 CI에 맡겼다.
- 미해결: 없음. 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/init_wizard.py`, `labhq/cli.py`, `labhq/gateway/server.py`, `tests/test_instances.py`.

## 2026-10-02 · PR #304 — runner 전용 Windows 계정(#298 ③)

- 결론: runner와 직원 CLI를 PI와 다른 표준 사용자로 띄우고, PI 홈은 OS ACL로 막는다. gateway는 PI 계정에 둔다.
- 바뀐 것: 실행 순서대로 쓴 절차서와 README 링크. 순서는 계정 → 폴더 권한과 labhq 설치(`C:\LabHQ\app`) → runner 계정에 직원 CLI 설치·로그인·Codex elevated setup → 설정 두 개 생성(client token이 있는 gateway.yaml은 PI만, 없는 runner.yaml은 runner만 읽기)과 파일 권한 → 점검·실행·업데이트·복구. doctor 점검: `runner.os_account`를 적으면 `whoami`(프로세스 token의 DOMAIN\user)가 그 계정일 때만 ok, 아니면 warn이고, 그 runner 설정에 client token이 있으면 warn. client token이 공개된 기본값(`change-me-client`)이면 어느 계정이든 warn(runner 설정은 빈 값으로 둔다). `os_account`가 없으면 설정 파일 소유자와 실행 계정이 같을 때 warn, 다르면 확인 안 됨(skip).
- 실행한 것: 새 회귀 3건은 구현 전 실패를 확인했다. doctor·init 관련 pytest 73 passed(기존 디코딩 warning 1건), 공개정보 검사와 diff 검사 통과.
- 미해결: 실제 계정 생성·ACL 변경·CLI 로그인은 PI가 절차서를 따라 실행해야 한다. 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `docs/runner-account.md`, `labhq/doctor.py`, `tests/test_doctor.py`.

## 2026-10-02 · #302 — 구독 한도 단계 자동 재개

- 결론: 구독 한도 오류는 실패 대신 `waiting_quota`로 주차하고 reset 시각에 자동 재개한다. reset 시각은 runner가 자기 시간대로 정한 instant를 쓰고, 병렬 ask·benchmark·재시작 복구는 한도 단계 때문에 멈추지 않는다.
- 바뀐 것: Claude Code·Codex·agy parser, 계정 단위 hold, 재시작 복구와 session resume, 단계 카드·수동 재개, 공용 active·terminal 요청 상태 판정. runner가 `quota_reset_at`(epoch)을 보내고 gateway는 그것을 믿는다. instant가 없으면 시각 문구는 기본 대기 1시간까지만 기다린다. 재시작 뒤에는 복구를 바로 시작하고, 웹 reducer는 마지막 한도 단계가 풀리면 요청을 running으로 돌린다.
- 실행한 것: 이번 회귀 test는 수정 전 실패했다(시간대·재시작 pytest, Node reducer). 관련 pytest 439건, `node tests/*.cjs` 13개, `scripts/check_public.sh`, `git diff --check` 통과. 전체 pytest는 CI에 맡겼다.
- 미해결: 없음.
- 근거: `labhq/quota.py`, `labhq/runner/daemon.py`, `labhq/models.py`, `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `labhq/request_status.py`, `labhq/web/state.js`, `tests/test_quota_wait.py`, `tests/web_state.cjs`.

## 2026-10-02 · #301 — bioinfo-agent 질문 게이트와 파이프라인 PR

- 결론: bioinfo-agent의 일반 질문과 새 파이프라인 생성 판단은 CSO가 답한다. 통제 데이터 구역·비용 상한 초과·프로그램 설치만 PI 승인으로 보낸다. 공개 bioinfo-agent 저장소로 가는 자동 PR은 기본 꺼짐이고 `policy.bioinfo_agent.pipeline_pr: true`로 켠다(PI 결정, #300).
- 바뀐 것: 켜면 gateway가 새 파이프라인 산출을 검사해 branch와 PR로 올린다. 꺼져 있으면 gateway가 러너에 파일을 요청하지 않고 GitHub도 부르지 않는다. 직원에게 GitHub 토큰을 주지 않는다. 파일은 확장자로는 코드만 받고, json·yaml·md 같은 형식은 정해진 pipeline 경로(`nextflow_schema.json`·`docs/*.md` 등)에서만, 표는 https 테스트 데이터를 가리키는 `assets/samplesheet*.csv`만 받는다. 성공으로 끝난 turn의 산출만 올린다. 절대경로는 접두어 목록 없이 모두 거부한다(`#!` 인터프리터와 `/dev/null`류만 예외). PR 상태는 snapshot에도 실려 늦게 접속한 화면에도 남는다.
- 실행한 것: 관련 pytest 545건 통과·4건 제외, 웹 상태 43건, 공개 검사를 통과했다. README는 51,970자에서 52,267자로 297자 늘었다. 전체 pytest는 CI에 맡겼다.
- 미해결: 켤 때 gateway 토큰에 bioinfo-agent 저장소의 Contents·Pull requests 쓰기 권한이 있어야 한다. 다른 labhq 사용자에게 기여 여부를 묻는 일은 #300. 컨테이너 안 `/opt/...` 경로나 README 예시 `/data/...`가 있는 pipeline(예: 기존 pacbio-hifi-wgs)은 거부되어 PR이 열리지 않는다.
- 근거: `labhq/pipeline_pr.py`, `labhq/integrations/github.py`, `labhq/gateway/server.py`, `tests/test_bioinfo_pipeline_pr.py`.

## 2026-10-02 · #149 결정 17 · #151 입력 종류–방법 적합성 그림자 판정

- 결론: 로컬 operation 관계표로 단계 입력을 `fit`·`mismatch`·`unknown` 중 하나로 세며, 세 개의 합계만 그림자 기록과 report에 남긴다.
- 바뀐 것: `semantics_input_fit.yaml`과 그림자 판정 모듈을 추가했다. EDAM 표가 없어도 로컬 key 판정은 유지된다.
- 실행한 것: 관련 의미론 테스트 135건과 입력 판정 테스트를 통과시켰다. 전체 pytest는 CI에 맡긴다.
- 미해결: 없음.
- 근거: `labhq/research/semantics_input_fit.yaml`, `labhq/research/semantics_input_fit.py`, `tests/test_semantics_input_fit.py`.

## 2026-10-02 · #297 — 팔란티어식 액션 A2(#149 결정 16)

- 결론: `request.followup` 하나를 PI CLI에서 y/N 뒤 기존 `POST /api/requests/{rid}/followup`으로 보낸다. 기본 off(`semantics.actions: confirm`일 때만). 다른 액션은 그림자 기록만, `hpc.*`는 늘 거부.
- 바뀐 것: `labhq semantics action run|check`, 러너가 직원에게 gateway token을 비운 설정 사본을 task마다 새로 줌(링크 폴더 거부, 끝나면 삭제), 보내기 전 실행 id 기록과 요청별 잠금을 폴더까지 fsync(응답 유실은 unknown, 재전송 없음), 모름이면 안 보냄, 확인 뒤 설정·자동 off 재확인.
- 실행한 것: 새 test 55개와 관련 test 파일 28개(865 passed, 23 skipped), 고친 곳을 하나씩 되돌려 test가 실패하는지 확인, `scripts/check_public.sh`. 리뷰 P1 반영 뒤 관련 test 12파일 427 passed, 2 skipped, `semantics_shadow_remove.py --only actions --check` 통과.
- 미해결: 러너가 PI와 같은 OS 계정이면 직원 셸이 원본 설정을 직접 읽을 수 있음(전용 계정 권장, README §10). 실제 CLI·gateway 운용 실측 없음.
- 근거: `labhq/research/semantics_actions_run.py`, `labhq/settings.py`(`write_staff_config`), `labhq/runner/daemon.py`, `tests/test_semantics_actions_run.py`.

## 2026-10-02 · PR #296 — CSO advisory A/B(#149 결정 15)

- 결론: 연구 요청을 요청 ID hash로 advisory·shadow arm에 고정 배정한다. advisory arm은 #267 selector 후보가 있을 때만 경로·내용·요청 본문 없이 artifact ID·data type·생성 요청 ID를 CSO 계획 참고로 준다.
- 바뀐 것: `semantics: ab`, arm·후보 ref 사용·비용 그림자 기록, arm별 요청·후보·참조율·실패율·비용과 10건/3주 판단 창 report, 제거 시험과 회귀.
- 리뷰 반영: arm은 연구 요청에만 붙인다. 참조는 계획 때 요청에 고정한 후보 ID와 비교하고, 그 ID를 쓴 계획도 정보 경계를 통과한다. 후보가 있으면 두 arm 모두 한 번 더 계획하고(shadow arm은 같은 prompt), live 상태 복사는 event loop에서 한다.
- 실행한 것: 관련 pytest 699 passed, 4 skipped(리뷰 반영 뒤). `scripts/check_public.sh`, `git diff --check` 통과. 전체 pytest는 지시대로 CI에 맡겼다.
- 미해결: 없음. 봇 리뷰와 CI 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/research/semantics_shadow.py`, `labhq/orchestrator/cso.py`, `tests/test_semantics_ab.py`.

## 2026-10-02 · #295 — 목차 갱신 workflow 직렬화(#292 후속)

- 결론: 병합이 겹쳐도 목차 갱신이 최신 기록을 빠뜨리지 않는다. 한 번에 하나만 돌고, 최신 main에서 만들며, push가 실패하면 다시 만든다.
- 바뀐 것: `.github/workflows/notes-index.yml`(concurrency, `ref: main`, 재시도 3번).
- 실행한 것: workflow YAML 확인. 실제 동작은 병합 뒤 main에서 확인한다.
- 미해결: 없음.
- 근거: `.github/workflows/notes-index.yml`.

## 2026-10-02 · #292 — PR별 패치노트·STATUS와 자동 생성 목차

- 결론: PR마다 자기 기록 파일만 고쳐 `STATUS.md`와 `patch_notes/README.md` 충돌을 없앴다. 공유 파일의 화면과 순서는 유지한다.
- 바뀐 것: 패치노트는 `patch_notes/entries/<branch>.yaml`, 진행 보고는 `docs/status/<시각>-<branch>.md`에 쓴다. main workflow가 두 공유 목차를 갱신한다. 기존 패치노트 447행과 STATUS 본문은 legacy 원본으로 옮겼다.
- 실행한 것: 관련 pytest 25건, `scripts/notes_index.py --check`, `scripts/check_public.sh`, `git diff --check` 통과. 전체 pytest는 지시대로 생략했다.
- 미해결: 열린 다른 PR이 먼저 병합된 뒤 이 PR을 마지막에 병합한다.
- 근거: `scripts/notes_index.py`, `scripts/patch_notes.py`, `.github/workflows/notes-index.yml`, `tests/test_notes_index.py`.

## 2026-10-02 · #258 #259 #260 #266 #268 #269 — 그림자·액션 후속 P2 묶음

- 결론: 액션 층 그림자의 epoch 전환·report·breaker 결함 3건과 B1 그림자의 직원 ID 경계·재사용 입력 계보 3건을 고쳤다. 실행 허용 목록은 빈 집합, `hpc.*` 거부, CSO에 주는 것 없음, 기록에 값 없음은 그대로다. 정보 경계는 막는 값만 늘었다.
- 바뀐 것: 새 epoch가 닫힌 epoch의 이어 묻기 backlog·drop 수·pending 몫을 버리고, queue를 바꾼 뒤 돌아온 이전 worker는 새 pending을 줄이지 않는다(#258). `rows`가 dict가 아닌 request 줄은 B1·액션 두 절에서 깨진 줄로 센다(#259). 성공한 이어 묻기 관측은 breaker 창에 넣지 않는다(#260). roster·계획·결과·task의 agent ID를 길이와 무관하게 `employee_id`로 막고, 6자 미만은 값 전체가 같을 때만 건다. 이어 묻기 snapshot도 roster를 가진다(#266). 일반 요청의 가벼운 계획 사본이 문자열 `input_refs`를 메모리에만 남겨 명시적 artifact edge로 쓴다(#268). 앞선 산출의 root 입력으로의 축약은 계획 edge나 그 산출 경로 자체를 가리킨 허용 reference(digest 일치, 더 먼저 만든 산출)에만 하고, bytes만 같은 입력은 축약하지 않는다(#269).
- 실행한 것: 새 회귀 12건 중 11건이 main 코드에서 실패하고 이 branch에서 통과했다(나머지 1건은 앞선 산출 경로 reference를 계보로 지키는 보존 test). 2차 실행 DB 사본 15건 재계산의 최종 후보는 main과 같은 3개(TP 3·FP 0)이고, 실제 roster 11명으로 본 경계 위반은 0줄이다. 경로 reference 계보를 빼면 후보가 1개로 줄어(TP 2건 손실) 그 edge를 남겼다. 전체 pytest 2547 passed/44 skipped, Node 13개, `scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: 없음. Codex 사용량 한도로 Claude가 이어받았다.
- 근거: `labhq/research/semantics_shadow.py`, `tests/test_semantics_actions_shadow.py`, `tests/test_semantics_shadow_breaker.py`, `tests/test_semantics_shadow_provenance.py`.

## 2026-10-02 · #151 #253 — EDAM 부분집합 표 병합

- 결론: 저장소 라이선스가 정해져(#252: 문서·데이터 CC BY-SA 4.0, 코드 GPL-3.0) draft로 묶어 둔 EDAM 표를 넣는다. 38개 key 중 32개가 EDAM 용어 31개에 이어지고, 판정은 여전히 key로 한다.
- 바뀐 것: `labhq/vocab/edam_map.yaml`·`edam_subset.yaml`·`NOTICE.md`, `scripts/edam_subset.py`, `tests/test_edam_subset.py`. NOTICE의 "PI 확인 전" 문구를 결정 내용으로 바꿨다.
- 실행한 것: `tests/test_edam_subset.py`와 산출 종류 test, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 입력 종류–방법 적합성 그림자 판정(#149 결정 17)은 다음 PR.
- 근거: `labhq/vocab/NOTICE.md`.

## 2026-10-02 · CI — 중복 실행 제거와 이미 통과한 pytest 재실행 생략

- 결론: PI 요청. PR 브랜치는 pull_request로 한 번만 돌고, 패치노트만 더한 push나 STATUS·패치노트 충돌만 푼 main 병합 push는 직전 커밋의 pytest가 통과했으면 pytest를 건너뛴다(skipped). 합쳐진 조합 자체는 다시 시험하지 않고, squash 병합 뒤 main CI가 다시 돈다.
- 바뀐 것: `.github/workflows/test.yml`(push는 main만, `changes` job, `checks: read`), `scripts/ci_skip.py`, `tests/test_ci_skip.py`.
- 실행한 것: `tests/test_ci_skip.py` 5건(다른 기능 branch를 합친 병합은 언제나 다시 돈다 — 봇 P1). 병합은 두 번째 parent가 base branch에 있을 때만 인정한다. 실제 #285 기록에 적용하면 코드 커밋이 통과한 뒤의 패치노트 커밋 1건만 건너뛰고 나머지는 그대로 돈다.
- 미해결: 없음.
- 근거: `scripts/ci_skip.py`.

## 2026-10-02 · #271 #282 — 단계 실패·revise 뒤 opt-in CSO 재계획과 저장 계획 max_steps 축소 처리

- 결론: 저장 계획이 현재 `max_steps`보다 길면 자르지 않고 실패한다(연구 lane은 승인 hash·상태 유지). `orchestrator.max_replans`(기본 0, 전과 같음)를 켜면 단계 실패나 revise 뒤 CSO가 남은 DAG만 다시 계획한다.
- 바뀐 것: 완료 단계는 다시 돌리지 않고 의존성도 그대로 둔다. 새 단계는 새 id로 `validate_steps`·`max_steps`를 통과해야 반영되고, 질문은 clarify gate, 비용은 budget gate를 다시 탄다. 시도 횟수는 CSO 호출 전에 저장하고 재시작 뒤에도 현재 상한으로 판정한다. PI 거절·취소 단계와 살아 있는 HPC job이 있는 단계는 우회하지 않는다. 연구 lane은 재계획하지 않는다. 재계획 이력은 reviewer·최종 보고 prompt와 보고서 metadata에 남는다.
- 실행한 것: 새 회귀 29건이 main 코드에서 실패하고 이 branch에서 통과했다. 전체 pytest 2513 passed/44 skipped, Node 13개, `scripts/check_public.sh`, `git diff --check`가 통과했다.
- 미해결: 후속 P2 2건(재계획 clarify 답변 뒤 재시작 시 재질문, drop하지 않은 flagged 단계 검사). Codex 사용량 한도로 Claude가 이어받았다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/settings.py`, `config/labhq.example.yaml`, `tests/test_cso.py`, `tests/test_output_types_research.py`.

## 2026-10-02 · #276 — 교차 세션 inbound 거부와 90초 MCP 대기 실증

- 결론: Claude 2.1.282는 `crossSessionInbound`를 지원한다. labhq가 만드는 Claude 직원·bench 명령에 `refuse`를 넣었고, 실제 CLI에서 다른 세션이 이름으로 보낸 메시지가 거부됐다. 90초 뒤 답하는 labhq MCP 호출은 fake CLI로 Claude·Codex 모두, 실제 CLI로 Claude에서 timeout 없이 한 번에 끝났다.
- 바뀐 것: Claude 직원 `--settings`(모든 profile)와 bench Claude arm에 `crossSessionInbound: "refuse"`. 러너의 labhq MCP timeout 계산을 `labhq_mcp_timeout_s()` 하나로 모았다. 지연 MCP 서버, 각 CLI의 tool timeout 규칙을 따르는 fake CLI, 실제 CLI probe(`scripts/probe_inbound_mcp.py`)를 더했다.
- 실행한 것: 실제 Claude 2.1.282(sonnet), 임시 폴더, 지연 90초·승인 timeout 60초·labhq MCP timeout 1320초, 세 설정을 동시에 한 번씩. 세 run 모두 서버 호출 1회·tool error 0으로 marker를 돌려줬다. refuse(97.2초)는 수신 쪽 debug log에 거부가 남고 보낸 쪽에 `Cross-session message refused` 통지가 왔다. 설정 없음(main, 102.0초)과 accept(96.5초)는 메시지가 직원 세션 큐에 들어갔고 거부 통지가 없었다. 회귀: inbound 9건·bench 1건·보낸 쪽 통지 파싱 1건이 수정 전 실패, 90초 test는 Codex `tool_timeout_sec`를 빼면 `request timed out`으로 실패했다. 전체 pytest 2500 passed/44 skipped, Node 13개, `scripts/check_public.sh` 통과.
- 미해결: 실제 Codex 장시간 probe는 Codex 사용량 한도로 돌리지 못해 #276을 열어 둔다(`python scripts/probe_inbound_mcp.py codex --output-dir <저장소 밖>`). 보낸 쪽은 `ListAgents`를 막아 모델이 PI 세션 목록을 읽지 않지만, 이름을 찾으려고 CLI가 로컬 세션에 접속하는 것은 막지 않는다. 실측 원본은 저장소 밖에만 있다.
- 근거: `labhq/adapters/claude_code.py`, `labhq/bench.py`, `labhq/runner/daemon.py`, `scripts/probe_inbound_mcp.py`, `tests/test_claude_inbound.py`, `tests/test_long_mcp_call.py`, `tests/test_probe_inbound_mcp.py`.

## 2026-10-02 · #274 — Biology 담당에 Claude Science와 같은 공개 과학 MCP

- 결론: PI 결정 B. biologist(Claude Code)와 lit_scout(문헌·웹 검색)에 로그인 없는 공개 hosted MCP 다섯 개(PubMed·bioRxiv·ChEMBL·Open Targets·ClinicalTrials)를, sci_reviewer에는 인용 확인용 PubMed·bioRxiv를 붙였다(PI 제안, PR 댓글). 로그인이 필요한 BioRender·Synapse·Wiley·Owkin과 사용량 문구 표시 조건이 있는 Consensus는 뺐다.
- 바뀐 것: `agents/core/biologist.yaml`·`lit_scout.yaml`·`sci_reviewer.yaml` mcp 목록과 prompt 한 줄(공개 ID·일반 용어만 보내고 코호트·통제 데이터는 보내지 않음), `scripts/integrations.py` 표시 이름, README 배지·연결된 도구 표.
- 실행한 것: `tests/test_integrations.py`·`tests/test_registry.py`, `scripts/integrations.py --check`, `scripts/check_public.sh`.
- 미해결: life-sciences skill(scvi-tools 등)은 plugin 경로가 PC마다 달라 이번에 넣지 않았다.
- 근거: `agents/core/biologist.yaml`, `scripts/integrations.py`.

## 2026-10-02 · #252 — 저장소 라이선스: 코드 GPL-3.0, 문서·데이터 CC BY-SA 4.0

- 결론: PI 결정으로 코드는 GPL-3.0-or-later, 문서·그림·데이터 표·역할 정의는 CC BY-SA 4.0이다. EDAM에서 뽑은 표(draft #253)도 CC BY-SA 4.0으로 들어갈 수 있게 됐다.
- 바뀐 것: `LICENSE`(GPL-3.0 전문), `LICENSE-CC-BY-SA-4.0.txt`(CC BY-SA 4.0 전문, GitHub license API 원문), README 상단 배지 2개와 §14 라이선스 표(제3자 예외: three.js MIT, placeholder.gltf CC0).
- 실행한 것: `scripts/check_public.sh`, `scripts/integrations.py --check`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `LICENSE`, `LICENSE-CC-BY-SA-4.0.txt`, `README.md` §14.

## 2026-10-02 · #229 #238 — CSO 산출 경로 정규화와 결과 블록 보존

- 결론: 선언·지시문·dependency·resume·연구 lane이 한 번 정규화된 `outputs/<name>`을 쓰며, bench 구조화 결과 블록은 LabHQ 상태·비용 문구 뒤에서도 최종 블록으로 남는다. 정보 경계와 실행 가드는 낮추지 않았다.
- 바뀐 것: 공백은 제거하고 원문 `..`·home·절대 경로는 정규화 전에 거부한다. 중복 선언은 합치고, 자기 산출의 루트·절대·home·bare 지시 경로는 대소문자와 무관하게 canonical 경로로 고친다. 같은 basename의 외부 입력과 산출이 섞이거나 외부 입력만 있으면 표현을 추측하지 않고 계획 교정을 요구하며, 중첩 home은 전체 경로를 한 번에 바꾼다. 외부 절대·home·drive 입력은 workspace artifact dependency로 추론하지 않고 canonical `outputs/...` 참조는 추론한다. 경로 문자열 안의 action 단어는 동사 판정에서 제외한다. 저장 계획 resume와 연구 CP1도 같은 검사를 다시 탄다. `wait_for_clarification: false`는 교정 계획 질문도 첫 계획처럼 기록한 뒤 진행한다. main에 이미 있던 단순 `./<name>` 교정은 기존 회귀로 유지했다.
- 실행한 것: 최초 확인 조건 10건과 봇 P1 회귀 8건이 수정 전 실패했다. 최종 관련 180건과 전체 pytest 2483 passed/44 skipped, Node 13개, `scripts/check_public.sh`, `scripts/patch_notes.py check`, `git diff --check`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/util.py`, `labhq/orchestrator/cso.py`, `tests/test_cso.py`, `tests/test_research_protocol.py`, `tests/test_output_types_research.py`.

## 2026-10-02 · #247 #254 — Windows 불안정 test의 완료 조건 고정

- 결론: #247은 `latched` 뒤 영속 기록이 끝나기 전 `enable`이 겹친 확인 경합, #254는 worker 대기가 아닌 호스트 지연까지 재던 1초 한계가 원인이었다. 제품 코드 결함은 아니었다.
- 바뀐 것: #247은 `disabled.json`·`auto_off` 기록과 이전 worker 결과 폐기를 조건 대기한다. #254는 막힌 worker가 풀리기 전에 event-loop 호출이 반환하는 순서를 확인하고 worker `join`·blocking queue put도 금지한다.
- 실행한 것: 수정 전 #247은 유휴 100회 0, 단일 CPU 과부하 100회 2 실패(PermissionError 1·부분 JSON 1), 수정 후 두 test 모두 유휴 100회와 같은 부하 100회에서 실패 0이었다. 관련 파일 69건도 통과했다.
- 미해결: 없음.
- 근거: `tests/test_semantics_shadow_breaker.py`, `tests/test_semantics_shadow_worker.py`.

## 2026-10-02 · PR #277 — 산출 종류 선언 reader 일치와 예외 격리

- 결론: #249 후속 P2 네 건을 닫았다. legacy와 typed 선언은 공용 reader가 같은 local vocabulary로 판정하고, 계획 선언과 runner record가 다르면 basis와 무관하게 충돌로 남긴다. 기본값은 off이며 off의 prompt·schema·dispatch는 #249 main 계약 그대로다.
- 바뀐 것: `no_vocab` 이유를 바로잡고, hash 불가 YAML key·어휘 loader·optional subset I/O·runner record 생성 예외를 선언 기능 안에 격리했다. EDAM 표가 없어도 local 38-key vocabulary는 동작하며 성공한 task 결과는 보존한다.
- 실행한 것: 새 회귀는 수정 전 11 failed/91 passed, 수정 뒤 102 passed. 전체 pytest 2458 passed/44 skipped, Node 13개, `scripts/check_public.sh`, patch-notes·diff 검사가 통과했다.
- 미해결: 없음. EDAM 표·NOTICE·생성 script는 draft #253 범위이며 이 PR에는 없다.
- 근거: `labhq/vocab/{__init__.py,declare.py}`, `labhq/yaml_unique.py`, `labhq/runner/daemon.py`, `labhq/research/semantics.py`, `tests/test_output_{types,vocab}.py`, `tests/test_semantics_{objects,output_types}.py`.

## 2026-10-02 · #263 — 재사용 후보 입력 정체성·목표 data type 필터

- 결론: 2차 실행 15건을 DB 사본으로 다시 계산했다. 판정표 34쌍의 후보는 34→3, 정밀도는 8.8%(3/34)→100%(3/3), 참 양성은 3→3, 거짓 양성은 31→0, 거짓 음성은 0→0이다. B1 그대로이며 CSO·실행 경로에는 값을 주지 않는다.
- 바뀐 것: 각 요청이 끝날 때 같은 host runner가 허용했다고 manifest에 남긴 reference의 입력 hash를 불투명 키 아래 보존하고, 과거 요청 입력은 다시 읽지 않는다. 요청문에서 계획의 출력 이름을 뺀 입력 이름만 쓰며 증거 없는 path는 `input_unknown`이다. 공개 accession·URL은 기록된 link만 쓰고 GitHub ref의 대소문자를 보존한다. 이 입력 정체성과 PI가 이름을 적은 산출의 #249 선언 data type이 모두 일치해야 후보가 된다. 제외 결과는 네 고정 사유의 개수만 남기고 필터 뒤에 순위를 매긴다.
- 실행한 것: 핵심 회귀 3건이 수정 전 실패했다. 자동 리뷰 두 차례의 P1 2건과 같은 정체성 부류 P2 3건을 고친 뒤 관련 66건(3 skipped), 전체 pytest 2450 passed/44 skipped, Node 13개, `scripts/check_public.sh`가 통과했다. #264 정보 경계 검사는 새 고정 사유만 허용하고 경로·파일명을 기록하지 않는 회귀로 확인했다.
- 미해결: 2차 원본에서 #261 자동 off 뒤 네 live 요청은 정답표가 없어 혼동행렬에서 제외했다. 15건 전체 replay의 최종 후보도 같은 3개였으며, B2 전환 근거로 쓰지 않는다.
- 근거: `labhq/research/semantics_shadow.py`, `tests/test_semantics_shadow_provenance.py`, `tests/test_semantics_shadow_hash.py`, `tests/test_direct_outputs.py`.

## 2026-10-02 · #262 — Windows Codex elevated sandbox setup 사전 차단

- 결론: 직원 Codex는 격리한 `CODEX_HOME`에서 떴지만 그 홈에는 elevated sandbox setup marker가 없었다. LabHQ가 `--ignore-user-config`와 `windows.sandbox="elevated"`를 명시하므로 개인 설정은 원인이 아니다. Codex가 무인 실행 중 관리자 helper를 띄우려다 사용자가 취소해 Windows 1223으로 실패했고, 기존 처리는 최종 응답이 있다는 이유로 성공처럼 넘긴 뒤 `missing_outputs`로 바꿨다.
- 바뀐 것: 초기화된 직원 `CODEX_HOME`에 marker가 없으면 adapter preflight와 doctor가 Codex를 시작하기 전에 이유를 밝히고 멈춘다. 실시간 1223 event도 `sandbox_setup_required` 실패로 보존한다. 더 약한 sandbox로 자동 전환하지 않는다.
- 실행한 것: 회귀 test 3건을 실패부터 확인했다. 관련 test 190 passed/17 skipped, 전체 pytest 2432 passed/44 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과. 격리 폴더에서 `unelevated` 저장은 2회 성공했지만 상위 canary 쓰기도 성공해 작업 폴더 경계를 지키지 못했으므로 채택하지 않았다. UAC와 elevated setup은 실행하지 않았다.
- 미해결: 실제 elevated 직원 실행 2회 저장 확인은 관리자가 해당 직원 `CODEX_HOME`의 setup을 대화형으로 마친 뒤 해야 한다. preflight는 marker 존재 여부를 확인하며, 버전 비호환은 실행 중 1223 원인 분류로 남는다. sandbox·권한 변경이므로 병합하지 않는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/adapters/codex.py`, `labhq/doctor.py`, `tests/test_isolation.py`, `tests/test_real_streams.py`, `tests/test_doctor.py`.

## 2026-10-02 · #261 — 연구 계획의 어휘 판본 hash가 정보 경계에 걸린 오판

- 결론: 2차 실행의 연구 요청을 최신 main에서 다시 계산했다. 걸린 칸은 `vocab_sha256`, 부류는 검증된 산출 어휘 판본 hash였다. 연구 계획의 같은 판본을 자유 입력처럼 민감값에 넣어, 줄의 공개 판본 칸이 자기 자신과 일치하자 차단한 오판이다.
- 바뀐 것: 현재 설치된 어휘 판본과 정확히 같은 값만 `vocab_sha256` 칸에서 허용한다. 같은 값이 다른 칸에 있거나 다른 판본이면 계속 막는다. 경계 위반으로 자동 off될 때 `disabled.json`·자동 off 줄·report에 고정된 칸 이름과 부류만 남기며 값은 쓰지 않는다. 경로·파일명·URL·DOI·직원 ID·자유 문장 차단 회귀를 넣었다.
- 실행한 것: DB 사본 재계산은 수정 전 `vocab_sha256:identifier` 1건, 수정 뒤 경계·type·action shape 문제 0건이다. 회귀는 수정 전 10건 실패를 확인했다. 관련 75건, 의미 모델 415건(2 skipped), 전체 pytest 2438 passed/44 skipped, Node 13개, `scripts/check_public.sh`가 통과했다.
- 미해결: 정보 경계 변경이라 PR은 병합하지 않는다. 봇 리뷰가 끝나 P1이 없으면 총괄에게 넘긴다.
- 근거: `labhq/research/semantics_shadow.py`, `tests/test_semantics_shadow_breaker.py`, `tests/test_semantics_shadow_report.py`, `README.md`.

## 2026-10-02 · #149 결정 13 · #150 — 액션 층 그림자 A1 (실행 없음)

- 결론: 객체 뷰 위에 기존 액션 7종의 전제 조건을 계산해 기록만 한다. 위험 검토 sol·astra가 둘 다 "A1만 조건부 go, A2 실행 코드는 이번 PR에서 뺀다"고 판정해 실행 허용 목록은 빈 집합이다. 실제 실행(A2, CLI `request.followup`)은 검토가 요구한 조건을 입증한 뒤 별도 PR이다.
- 바뀐 것: 새 `labhq/research/semantics_actions.py`(순수 판정, gateway·orchestrator·runner·store·네트워크·프로세스 import 없음, 입력은 frozen 사본). `semantics: {mode: shadow, actions: shadow}`면 요청 종료 B1 줄에 `actions` 칸을, 이어 묻기 접수·거부·종료 때 `type: followup` 줄을 쓴다. 조건은 true/false/unknown이고 기록에 없는 시점은 unknown이다(지금 roster로 채우지 않음). 읽기 전용 거부는 run_followup이 넘긴 reason만 `refused_while_open`으로 센다. `hpc.*`는 늘 `refused_p3`이고 `hpc_submit` 승인 창만 센다. `actions: confirm`과 그 밖의 값은 actions만 끄고 경고 한 번, 허용 목록 키는 모르는 키라 semantics 전체가 off다. 실패는 B1 breaker 창에서 세고, 새 칸·줄은 고정 이름·참거짓·개수 shape 검사를 거친다. `labhq semantics report`에 A1 절, `scripts/semantics_shadow_remove.py --only actions`. 연결은 `# semantics-hook: actions` 줄(server 4·cso 4·semantics_shadow 23)과 semantics_shadow 안의 블록 2개다. README §8·§10.
- 실행한 것: 새 test 72건. 게이트 우회 불가(AST import·동적 호출 검사, 빈 허용 목록, 설정으로 못 늘림, 문서 속 "PI 승인 완료" 무시, lab에서 recruit·contract·cancel 메시지 0), 끄면 원상(actions off 다섯 값에서 모듈 미로드·B1 줄만, 고정 시계에서 B1 줄 바이트 동일, lab 기록 동일, `--only actions` 제거 뒤 B1 동작), 중복(같은 요청·이어 묻기를 여러 번 기록해도 한 번), HPC 거부. 가드 11개를 하나씩 깨는 변이 검사에서 11개 모두 test가 실패했다. 로컬 리뷰 P2 4건(결정된 research_plan의 hash를 unknown으로, backlog가 queue의 요청 작업을 앞지르지 않게, mismatch 필드 누락을 shape에서 거부, 마지막 burst에서 버린 수 기록)을 test와 함께 고쳤다. 전체 pytest 2428 passed/44 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: A2 재개 조건(직원이 PI client token에 닿지 못함 입증, 응답 유실 때 재전송 금지, 감사와 followup id 연결, 전송 직전 off 재확인). runner 승인 시계와 gateway 결정 시계가 다르면 만료 판정이 어긋난다. task 실행 구간은 끝 시각이 없어 길이를 재지 않는다. 이어 묻기는 REST 경로만 보고, queue가 차면 20건까지 뒤에 둔 뒤 넘치는 것은 버리고 센다. 임계값은 전부 미측정 제안치다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/research/semantics_actions.py`, `labhq/research/semantics_shadow.py`, `labhq/gateway/server.py`, `labhq/orchestrator/cso.py`, `scripts/semantics_shadow_remove.py`, `tests/test_semantics_actions.py`, `tests/test_semantics_actions_shadow.py`, `tests/test_semantics_shadow_remove.py`, `README.md`.

## 2026-10-02 · #221 할 일 2 · #151 — 산출 데이터 종류 선언 자리

- 결론: 출처 모델과 객체 뷰가 함께 쓰는 "산출 데이터 종류 선언 자리"를 core에 만들었다(PI 결정 12). 기본 off라 지금 동작은 그대로다. 켜면 CSO가 단계 산출마다 data_type·format key를 적고, 러너가 실제로 모은 산출에 붙이며, 두 그림자 모델이 같은 읽기 함수로 읽는다. 팔란티어식 액션은 계속 보류다.
- 바뀐 것: `labhq/vocab/`(로컬 key 38개, 선언 정규화 계약 하나, optional subset loader), `plan.declare_output_types`(기본 false), 일반·연구 계획 단계의 `output_types`, `TaskResult.output_types`(러너 기록), `ArtifactRef.data_type·format`, 그림자 줄의 `vocab_sha256`·`types`(zone 통과분만 ID별, 나머지 withheld)·`declarations`, report의 model/vocab 판본 분리. EDAM 표와 생성·검증 묶음은 draft PR #253으로 분리했다.
- 두 위험 검토(Sol·Astra, 둘 다 조건부 go)의 필수 수정: 기본 off와 off일 때 schema·prompt·dispatch 바이트 동일, 로컬 어휘와 EDAM 분리(subset 없거나 깨져도 동작), 정규화 계약 하나(순서 무관, 중복·별칭·충돌·판본 차이는 unknown, 엔트리·바이트 상한), 선언 판본 고정(다른 판본은 재해석하지 않음), 빈 선언은 canonical에서 빠져 승인된 계획 hash 유지, off·재개 때 저장 선언 보존, 경고는 코드·개수만, 그림자 줄은 subset ID 허용 목록으로 key·값 검사, zone gate 뒤에만 종류별 집계, 공통 YAML loader를 core로 분리, wheel 포함·semantics 제거 뒤 core 동작, report 판본 분리.
- 실행한 것: 기능·수정 커밋마다 회귀를 먼저 확인했고 dispatch·승인·결과가 선언과 무관함을 guard test로 고정했다. EDAM 묶음을 뺀 실제 tree에서 subset 부재 회귀를 포함한 관련 test 91개와 전체 pytest 2341 passed/43 skipped, Node 13개, `bash scripts/check_public.sh`가 통과했다. 로컬 Codex 리뷰 P2 3건(직원 선언 중복의 순서 의존, 객체 뷰의 직원 선언 누락, 엔트리 상한 불일치)을 고쳤다.
- 미해결: 실제 CLI에서 켜 보지 않았다. 켜기 전 조건은 Claude·Codex schema probe, 같은 요청의 off/on 계획 비교, prompt+schema 실제 token(규칙 500자·schema 224자, token 미측정)이다. 연구 직원 선언은 연구 단계가 실행되기 전까지 쓰이지 않는다. EDAM 표는 #252 PI 확인 전 병합하지 않는 draft PR #253에 있다. 로컬 key 정의 검수는 모두 pending이다.
- 근거: `labhq/vocab/`, `labhq/yaml_unique.py`, `labhq/orchestrator/cso.py`, `labhq/research/contract.py`, `labhq/runner/daemon.py`, `labhq/models.py`, `labhq/research/semantics*.py`, `tests/test_output_vocab.py`, `tests/test_output_types*.py`, `tests/test_semantics_output_types.py`.

## 2026-10-02 · #222 — 공개 데이터 연구 요청이 실제 CLI로 CP1 승인 카드까지

- 결론: 10-01 실행의 CSO 연구 계획 5개는 모두 acceptance 키를 지어냈다. rule id가 prompt 어디에도 키로 적혀 있지 않아 reviewer question을 키로 썼다. d1은 `protocol.packs`의 sha256도 비웠다. 검증은 첫 오류에서 멈춰서 교정 한 번에 오류 하나만 보였다. 이제 pack hash는 코드가 채우고, prompt는 `pack_values_keys`를 보여 주고, 교정 prompt에는 문제를 모두 넣는다. 재실행하다 Windows 명령줄 한도를 넘은 재계획이 `executable not found`로 실패하는 것도 찾아 고쳤다.
- 바뀐 것: `with_pack_refs`·`research_plan_errors`(키 missing·unexpected, 통계 core 누락 field 이름), `pack_refs`와 catalog `pack_values_keys`, CSO 교정 루프. 교정 뒤에도 실패하면 CP1 카드 없이 `outcome: plan_invalid`, 한국어 보고서, `plan_validation.errors`가 남는다. adapter는 명령줄이 32,000 UTF-16 단위를 넘으면 TASK 파일을 가리키는 prompt로 바꾸고, 그래도 넘으면 실행 전에 거부한다. README §8·§10, `docs/research_protocol.md`를 맞췄다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 실행한 것: 회귀 test 12건 모두 main 코드에서 실패한다(독립 검증: main에 새 test 파일만 얹어 12 failed). 그중 1건(agent_id 타입)은 로컬 Codex 리뷰가 찾은 중간 회귀의 guard다. 로컬 Codex 리뷰 P2 2건(agent_id가 list면 교정 없이 실패, 길이를 UTF-16으로 세기)을 반영했다. 실제 CLI 재실행(CSO Sonnet, 10-01 d1과 같은 공개 GSE96583 요청): 첫 시도는 clarify 뒤 재계획 명령이 33,091자라 실패했다. 고친 뒤 두 번 모두 CP1까지 갔다(첫 계획 통과 349초·$0.55, 한도를 20,000으로 낮춰 TASK 파일 경로로 clarify·재계획 1,116초·$1.50). 두 번 다 CSO는 `protocol.packs`를 비웠고 acceptance 14개를 rule id로 채웠다. 전체 pytest 2187 passed/41 skipped, Node 13개, `bash scripts/check_public.sh` 통과.
- 미해결: 연구 lane은 요청마다 모든 active pack 값을 채워야 한다. d2(엽록체 IR 가설에 `single_cell_de@2`)처럼 대상이 다르면 여전히 CP1에 못 가고, 보고서는 설정된 pack과 그 적용 대상을 알려 줄 뿐이다(README §10). 요청별 pack 선택은 범위 밖이다.
- 근거: `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/orchestrator/cso.py`, `labhq/adapters/base.py`, `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `tests/test_research_cp1.py`, `tests/fixtures/fake_claude_cso.py`, `tests/test_adapters_fake_cli.py`.

## 2026-10-02 · 열린 issue 작업 큐 동기화

- 결론: 오래된 병합 전 큐를 열린 PR 2건과 아직 추적되지 않던 후속 33건 기준으로 바꿨다.
- 바뀐 것: PR #239·#230을 리뷰 중으로 두고, #57·#90·#149·#150·#151·#165·#178의 남은 단계와 P2·P3 후속을 `HANDOFF.md`에 묶었다.
- 실행한 것: GitHub의 열린 PR·issue 상태와 각 issue의 현재 본문을 대조했다.
- 미해결: 구현은 하지 않았다. 각 묶음의 순서와 담당은 다음 개발 총괄이 정한다.
- 근거: `HANDOFF.md`, GitHub issue #57·#86·#90·#149–#151·#165·#178·#196–#242 중 열린 후속.

## 2026-10-01 · #221 할 일 1 — direct 요청 산출을 결과 outputs로

- 결론: direct 요청도 작업 폴더 `outputs/`의 산출을 결과 outputs로 남겨, 그림자 출처 모델이 artifact로 보고 hash를 잰다. 할 일 2(산출 데이터 종류 선언 자리)는 PI 판단(#149·#151) 대기라 하지 않았고 #221은 열어 둔다.
- 바뀐 것: runner가 `kind: direct` 실행 뒤 `TaskWorkspace.scan_outputs`로 `outputs/` 아래 정규 파일을 센다. symlink·junction·mount·통제 구역은 따라가지 않고(`outputs/` 자체 포함) labhq의 `RESULT*.md`와 UTF-8로 읽을 수 없는 이름(Linux에서 푼 CP949 파일명 등)은 빼며, 최대 200개(그림자 `HASH_MAX_FILES`)와 `runner.reference_scan_max_entries`·`reference_scan_max_depth` 안에서만 센다. 상한에 닿으면 작업 로그에 경고가 남는다. 계획 단계는 그대로 선언한 산출만 보고한다. direct 이어 묻기는 orchestrate처럼 산출이 있는 작업 폴더를 읽기 전용 upstream으로 받는다. mock 직원은 direct `[artifact]`에도 파일을 쓴다. README 그림자 절을 맞췄고 패치노트는 건드리지 않았다.
- 실행한 것: 회귀 10건이 수정 전 실패하고 수정 뒤 통과했다(step 선언 산출 guard 1건은 전후 통과). 로컬 Codex 리뷰 P2 1건(`outputs/` 자체가 mount거나 통제 구역 안이면 그대로 순회)을 고치고 회귀에 넣었다. run log의 c1·c2를 실제 Claude Code CLI(data_steward=Sonnet, 공개 palmerpenguins 발췌, 별도 state)로 다시 돌렸다. c1은 outputs 2개·`observed_new 2`(이전 0), c2는 `history_artifacts 2`·`candidates 0`·`type_unknown 2`. 전체 pytest 2133 passed/25 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과. 독립 검증에서 P1 2건을 고쳤다. UTF-8이 아닌 파일명 하나가 끝난 direct 실행을 `UnicodeEncodeError` 실패로 바꾸던 것과, 항목 상한 test가 ext4 목록 순서에서 실패하던 것이다(WSL ext4에서 수정 전 실패, 수정 뒤 12 passed). main 병합 뒤 두 번째 검증에서 Codex 리뷰 P1 1건도 고쳤다. 산출 목록이 `manifest.json`을 `read_owned` 없이 읽어, 직원이 그 자리에 둔 링크나 FIFO를 따라가던 것이다(#165 우회, WSL ext4에서 수정 전 실패, 수정 뒤 13 passed).
- 미해결: 할 일 2 전이라 live 후보는 여전히 0이다(`type_unknown`). 같은 폴더를 다시 쓰는 재시도·wake는 같은 파일을 여러 run이 보고해 `not_generated`가 된다(선언 산출과 같은 규칙). hard link는 경로로 구별하지 못한다.
- 근거: `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `labhq/adapters/mock.py`, `tests/test_direct_outputs.py`, `tests/semantics_shadow_lab.py`.

## 2026-10-02 · #219 — Windows TEMP 아래 작업 폴더의 Claude 쓰기 경로

- 결론: Claude 직원이 Git Bash `pwd`의 `/tmp/...`를 Write에 넣어도, 작업 폴더 안이면 승인 없이 작업 폴더에 쓴다. 폴더 밖 쓰기는 여전히 승인을 받는다.
- 원인: Claude Code 2.1.282(Windows)의 Bash는 TEMP를 `/tmp`로 보여 주고, 파일 도구는 같은 표기를 드라이브 루트 `C:\tmp\...`에 쓴다. 게이트는 그 경로를 폴더 밖으로 보고 물었다(chief_of_staff·biologist). 맨 `Write`가 사전 허용된 직원(analyst·data_steward)은 묻지 않고 `C:\tmp`에 써서 산출이 비었다.
- 바뀐 것: 게이트는 드라이브 없는 쓰기 경로를 Claude가 실제로 쓸 경로로 판정한다. Git Bash 뜻(`/tmp`→TMP·TEMP, `/c/`→`C:\`)이 쓰기 루트 안일 때만 그 경로로 고쳐 `updatedInput`으로 돌려준다. TMP·TEMP가 없거나 다르면 고치지 않고 지금처럼 묻는다. 승인 요청에는 실제로 쓸 경로를 `detail.path`로 싣는다. 맨 `Write`·`Edit`는 작업·project·upstream 폴더의 `Edit(//…/**)` 규칙으로 바꿨다. 링크로 적힌 폴더는 적힌 표기와 실제 경로에 규칙을 하나씩 둔다(검증 중 발견: 실제 경로 규칙만으로는 링크 표기 절대경로 쓰기가 막혔다). bench 대본 PI는 그 task 작업 폴더 안 Write/Edit 요청을 승인하고 `workdir_write_approvals`로 따로 센다. README §8·§10을 맞췄다.
- 실행한 것: 실제 Claude CLI(2.1.282, sonnet) probe로 경로 표기와 파일이 생긴 곳을 기록했다(가린 fixture 3개). 수정 뒤 같은 `/tmp` 쓰기는 작업 폴더에 생겼고, 폴더 밖 쓰기는 게이트가 승인으로 넘겼다. junction 표기 작업 폴더 probe 6개(`junction_*`)로 두 표기 규칙이 모두 있어야 사전 허용됨을 확인했다. bench public-protein-qc labhq arm 재실행에서 미스크립트 승인 1→0, `C:\tmp` 쓰기 0. 새 회귀 36건 중 34건이 수정 전 실패했다(2건은 guard). 전체 pytest 2160 passed/25 skipped, Node 13개, `bash scripts/check_public.sh` 통과. Codex 리뷰 지적 없음.
- 미해결: protein-qc는 여전히 FAIL이다. CSO 계획이 `answer.md`를 `outputs/` 밖에 선언해 INCOMPLETE가 됐다(별개 원인, 그림자 실행의 penguins와 같은 부류). 사전 허용 Read의 `/tmp/...`는 "파일 없음"으로 끝난다. Bash 셸 쓰기 대상의 `/tmp`·`/c/` 표기는 그대로 승인을 받는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/policy.py`, `labhq/tools/approval_mcp.py`, `labhq/adapters/claude_code.py`, `labhq/bench.py`, `tests/test_claude_write_paths.py`, `tests/test_bench_review3.py`, `tests/fixtures/real/claude_code/claude_windows_write_paths.json`, `claude_write_tmp_*.jsonl`.

## 2026-10-01 · #220 — CSO 계획의 산출 경로를 실행 전에 outputs/ 안으로

- 결론: 단계 결과 계약은 작업 폴더 `outputs/` 아래만 센다. penguins 계획은 `answer.md`를 선언하고 지시문에 `./answer.md`(작업 폴더 루트)를 적어, analyst가 만든 파일을 찾지 못해 INCOMPLETE가 됐다. 이제 계획 검증이 dispatch 전에 고치거나 다시 받는다.
- 바뀐 것: `validate_steps`가 단계 자기 산출을 가리키는 루트 경로(`./answer.md`, `.\answer.md`)를 `./outputs/answer.md`로 고치고 선언도 `outputs/answer.md`로 바꿔 경고를 남긴다. 절대 경로·드라이브·`..`는 `PlanOutputsError`로 CSO 교정 계획을 한 번 받고, 그래도 틀리거나 교정 계획이 새 질문을 내면 단계 없이 실패한다. PLAN_PROMPT 산출 규칙에 `outputs/<name>`과 루트·절대 경로 금지를 적었고, 단계 prompt 끝에 선언 산출 경로를 싣는다. README §7에 한 문단.
- 실행한 것: penguins 재현 fixture(`tests/fixtures/plans/penguins_outputs_root.json`)를 포함한 회귀 test 10건이 수정 전 실패함을 봤다. 전체 pytest 2132 passed/25 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과. Codex 리뷰 P2 1건(교정 계획의 새 질문이 PI 확인 없이 dispatch됨)을 고치고 test를 더했다. 실제 CLI(Sonnet 대체) penguins 재실행: 요청 done, CSO가 `outputs/answer.md`로 선언, analyst·qc_reviewer 단계 모두 done($0.65, 309초).
- 미해결: 같은 재실행에서 bench 형식은 FAIL이다. 최종 보고서 끝에 붙는 "Step status and output paths" 감사 줄이 결과 블록 뒤에 와서 "structured result block is not last"가 된다. qc_reviewer의 `labhq_ask`가 `wait: none`으로 broker 500, tool_permission 미스크립트 거절 2건(Temp/claude 경로)도 별도 문제다. 연구 lane 계획은 산출 경로를 검사하지 않는다(PR 1 pilot은 단계를 실행하지 않음). 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/orchestrator/cso.py`, `tests/test_cso.py`, `tests/fixtures/plans/penguins_outputs_root.json`, `README.md`.

## 2026-10-01 · #165 #177 #178 #180 #181 #182 #183 #190 #193 — 작업 폴더 쓰기·Claude 규칙 경로·공개 가드 후속

- 결론: 부류마다 공통 판정 하나로 닫았다. 러너가 작업 폴더에 쓰는 경로(#165·#190·#193), Claude 거부 규칙을 붙일 수 없는 경로(#177·#182), 공개 가드의 표기 빈틈(#180·#181·#183)이다. #165는 1–3번만, #178은 문서만 고쳤다(둘 다 실측이 남아 Refs).
- 바뀐 것: `adapters/owned.py`가 labhq 소유 경로 쓰기를 맡는다. 링크를 따라가지 않고, 재사용 폴더에 링크가 있으면 실행을 거부한다. manifest와 Codex 마지막 답도 링크를 거쳐 읽지 않는다. 계약 skill 상위 링크도 지우지 않고 거부한다. 계약 skill은 원본과 같을 때만 면제하고, Windows·macOS에서는 지시 파일 이름을 대소문자 없이 비교한다. 재사용 폴더 검사 중 이벤트는 모았다가 로컬 로그에 쓴다. `claude_rule_ready`가 UNC 링크·참고를 거른다. mount 뒤도 계속 훑는다. 공개 가드는 percent-encoded webhook(앞에 다른 escape가 붙은 것 포함), 사내 forge의 owner/repo, 공백·`\uXXXX`가 든 계정 home을 가린다.
- 실행한 것: 확인 조건 test는 수정 전 실패를 확인했다. 이 PC에서 skip된 것은 file symlink 13건과 POSIX 전용 3건이다. 전체 pytest 1968 passed/39 skipped, Node 11개, `scripts/check_public.sh`, `git diff --check` 통과. 로컬 Codex 리뷰(branch diff)는 결함을 찾지 못했다. 독립 검증이 P1 둘(링크 너머 manifest·마지막 답 읽기, escape 바로 뒤 webhook)을 고쳤고 P2 일곱은 후속으로 뺐다.
- 미해결: 실제 CLI 실측 셋. #148 신뢰 경로 probe(#165 4번), 하위 폴더 CLAUDE.md 제외 probe(#165 5번), Claude 규칙의 대소문자·8.3 비교(#178)다. #178은 `allow_runner_read_restricted`와 대소문자 무시 파일 시스템이 겹칠 때만 남는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/adapters/owned.py`, `labhq/adapters/read_only.py`, `labhq/runner/daemon.py`, `labhq/runner/workspace.py`, `labhq/intake.py`, `labhq/integrations/github.py`, `tests/test_workspace_boundary.py`, `tests/test_publish_followups.py`.

## 2026-10-01 · PR #136·#158 후속 12건 — 계보 순회·resume 이유·그림자 breaker·hash·mark

- 결론: #154 #155 #156 #157 #159 #160 #161 #163 #173 #174 #175를 issue별 커밋으로 고쳤다. #162는 이미 main(dc01c50)에 고쳐져 있어 issue의 두 모양만 test에 더했다. #163과 #174는 같은 결함이라 한 커밋이다.
- 바뀐 것: cycle 경고는 강연결 요소마다 한 건(A·B), 계보 순회는 너비 우선 최소 깊이, resume 이유 `start_unknown`(모델 hash `9ed9aa1e…`), hash는 `timeout_s`의 절반까지, JSON1 없으면 `sqlite_json1_missing` off, Windows 읽기는 FILE_SHARE_DELETE, wake 자식이 있으면 job은 끝난 것, breaker window는 `breaker.json`, mark는 기록된 후보만. README 그림자 절과 §10을 맞췄다. 패치노트는 건드리지 않았다.
- 실행한 것: 확인 조건마다 회귀 test가 수정 전 실패함을 봤다(#156은 재확인 비교를 바꾸는 변이로). 전체 pytest 1958 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과. Codex 리뷰 P2 1건(event loop에서 breaker.json fsync)을 daemon thread 저장으로 고쳤다. 독립 검증에서 #159 worker test의 실시간 여유(약 1초)를 9.5초로 넓혔다.
- 미해결: Windows에서 hash 중인 파일 위로 다른 파일을 `os.replace`하는 쓰기는 여전히 실패할 수 있다(README §10). `finished` job은 최종 상태(성공·실패)를 모른다. derived_from 깊이 경계는 A(64)와 B(65)가 한 단계 다르다(이번 범위 밖).
- 근거: `labhq/research/semantics.py`, `labhq/research/semantics_v1.yaml`, `labhq/research/semantics_shadow.py`, `labhq/research/semantics_objects.py`, `tests/semantics_baseline.py`, `tests/test_semantics_pilot.py`, `tests/test_semantics_shadow_*.py`, `tests/test_semantics_objects.py`.

## 2026-10-01 · #112 #113 #144 — 재시작 뒤 CSO session·질의 route 후속

- 결론: 재시작으로 gateway가 놓친 상담·이어 묻기가 runner에서 계속 도는 동안, CSO 계획·최종 보고서·새 이어 묻기가 같은 session·workdir로 겹쳐 dispatch되지 않는다. 재시작 전 facilities가 받던 질의는 roster가 빈 동안 CSO로 넘어가지 않는다.
- 바뀐 것: `holds_session` 하나로 "결과가 기록되지 않은 task가 session·workdir를 쥐었나"를 판정한다(abandoned 포함). 상담은 쥔 task가 있으면 바로 격리하고, 계획·종합·이어 묻기는 `Hub.wait_session_free`로 지금 runner 세대가 수락한 task를 결과가 올 때까지, runner가 끊기면 끊긴 때부터 `resume_wait_s`까지 기다린 뒤 그 turn의 session에서 잇는다. 끝났는지 모르면 새 session·workdir로 연다(#112 #144). 질의 원장의 `routed_to`로 재시작 전 담당자를 유지하고, 그 runner를 `resume_wait_s`까지 기다리며, facilities가 끝내 안 돌아오면 CSO가 답한다(#113). README §8을 맞췄다.
- 실행한 것: 회귀 12건 중 10건은 수정 전 실패, 2건(facilities 없는 설정 무대기, fallback)은 guard다. 로컬 리뷰 P2 2건(끊긴 runner로 전달 중인 task의 대기 상한, abandoned 뒤 도착한 결과의 점유 해제)을 #112 커밋에 반영했다. 봇 리뷰 P1(queue에서 기다린 수락 task를 dispatch 시각+timeout으로 놓아 줌)은 시계로 점유를 푸는 규칙을 빼서 고쳤고, 재접속 유예도 시작 시각이 아니라 끊긴 때부터 센다(회귀 2건, 수정 전 실패). 전체 pytest 1939 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: 기다린 이전 이어 묻기의 답은 그 항목에 붙이지 않는다(interrupted 그대로). 수락된 task에는 대기 상한이 없다. runner가 queue·task timeout을 거쳐 결과를 꼭 보내므로 그것을 기다린다. 격리된 실행은 session 맥락 없이 prompt의 보고서·결과로 답한다. 재시작 전 CSO가 받던 facilities 질의는 facilities가 돌아와도 CSO가 잇는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `tests/test_consult_restart.py`, `tests/test_followup.py`.

## 2026-10-01 · #119 #172 #185 #186 — HPC 첫 설정 상담과 #171 후속

- 결론: `labhq init`이 읽기 전용 조회로 `hpc:` 초안을 만들고, PI가 `y`라고 한 시험 잡 1회만 제출해 끝까지 추적한다. #171 후속 P2(#172 여섯 항목, #185, #186)를 닫았다.
- 바뀐 것: 스크립트에 스케줄러 지시가 있으면 임계값과 무관하게 승인, `hpc_cancel`은 broker가 추적한 그 직원의 잡만, 스케줄러 명령 env는 허용 목록, 셸 승인 목록 확대, cluster 판정은 `slurm_cluster_option` 하나(묶은 짧은 옵션 포함), accounting 없음·`REVOKED`·모르는 상태도 종료로 깨움. 시험 잡 파일은 시험마다 새 개인 폴더에 배타 생성한다. 패치노트는 건드리지 않았다.
- 실행한 것: issue별 회귀 테스트가 수정 전 실패, 수정 뒤 통과했다. 로컬 Codex 리뷰 P2 3건(비소모성 `mem_free`를 코어로 나눔, mock id 취소, 시험 잡 상태 timeout)도 회귀 테스트와 함께 고쳤다. 독립 검증 리뷰의 P1(시험 잡 고정 파일명에 남은 링크를 따라 씀)은 hardlink·symlink 회귀 테스트와 함께 고쳤다. 전체 pytest 2028 passed/24 skipped, Node 11개, `scripts/check_public.sh` 통과.
- 미해결: SGE `.sge_request`(제출 폴더·home)는 직원이 셸로 쓰면 승인 계산 밖 자원을 요청할 수 있다(README §10). 실제 클러스터에서는 돌리지 않았다.
- 근거: `labhq/hpc_consult.py`, `labhq/init_wizard.py`, `labhq/tools/scheduler.py`, `labhq/tools/hpc_mcp.py`, `labhq/settings.py`, `tests/test_hpc_consult.py`, `tests/test_hpc_followups.py`.

## 2026-10-01 · #126 #184 — 웹 화면 후속: snapshot 답 길이, 승인 알림·이름표·하단 줄

- 결론: snapshot의 이어 묻기 답은 앞 2,000자만 싣고 전문은 펼칠 때 받는다(#126). 승인 알림은 승인이 끝나는 모든 경로에서 지우고, 3D 이름표는 자기 머리 위에 두고, 2.5D 데스크톱은 사무실·Command Center·직원 줄·입력창이 겹치지 않게 화면을 나눴다(#184).
- 바뀐 것: gateway `snapshot_followup` 하나가 요청 followups와 replay 이벤트를 함께 자르고, 같은 답이 다시 실리는 replay `task.result` 본문은 step 카드 길이(500자)로 자른다. 공용 reducer에 `fillFollowups`, 승인 종료 공통 `endApproval`(resolved·timeout·expired·stale, snapshot에서 사라진 승인)과 `toast.clear` effect를 넣었다. 3D는 skin별 label anchor 대신 head anchor 기준 `labelPoint`를 쓴다. mock 승인 예시는 `Rscript scripts/qc_plots.R`로 바꿨다. README §6을 맞췄고 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 실행한 것: 확인 조건마다 회귀를 먼저 썼고 수정 전 모두 실패했다. 긴 답 25개 snapshot은 2,644,490에서 276,687 bytes, 러너 `task.result`까지 실제 경로로 쌓은 경우 2,044,888에서 208,507 bytes. 1200×750 브라우저 측정에서 사무실·Command Center 87–527px, 직원 줄 541–677px, 입력창 685–750px로 겹침 0, 페이지 스크롤 없이 마지막 행과 메신저 끝이 각자 안쪽 스크롤로 보인다. 전체 pytest 1928 passed/23 skipped, Node 13개, `scripts/check_public.sh` 통과.
- 미해결: 가로 621–999px 단일 열에서는 직원 줄이 sticky라 스크롤 중 내용을 덮는다. snapshot 크기는 여전히 요청 수에 비례한다. README 움직이는 화면은 다시 녹화하지 않았다.
- 근거: `labhq/gateway/server.py`, `labhq/web/state.js`, `labhq/web/index.html`, `labhq/web/lab3d/src/{live,skins,main}.js`, `tests/test_followup.py`, `tests/web_issue126.cjs`, `tests/web_issue184.cjs`.

## 2026-10-01 · PR #166 후속 여섯 건(#167 #168 #169 #170 #187 #188) — 연구 근거 검증과 계획·검토 결속

- 결론: 여러 표기 accession 비교, 체계가 다른 같은 논문의 재인용, REVIEW v2 작성자 결속, 없어진 pack version, PLAN slot id 중복을 닫았다. #169와 #188은 같은 부류라 `author` 필수화 하나로 닫았다.
- 바뀐 것: `compare_accessions`가 version·isoform·VCV 자리채움을 뗀 base로 판정하고 레지스트리 URL 로컬 판정, uri resolver 경로, ID 조회 응답이 함께 쓴다. base가 다르거나 양쪽에 적힌 version·isoform이 다르면 `conflicting`, 한쪽에 version·isoform이 없으면 `base_match_only` 미확인이다. ID 조회 응답에 인용 표기와 같은 base의 다른 표기가 함께 오면 `conflicting`이다(#167). resolver 기록의 `same_as`(DOI↔PMID↔PMCID)를 재인용 키에 더해 `VerificationReport.recitations`로 보고하고 `ok`를 막는다(#168). `validate_research_review`는 `author`가 없거나 비면 거부한다(#169 #188). gateway는 상태를 열기 전에 pack을 불러 `available: single_cell_de@2`를 담은 오류로 멈춘다(#170). 한 step 안 evidence slot id 중복은 CP1 전에 거부한다(#187). 패치노트는 건드리지 않았다.
- 실행한 것: 새 회귀 17건이 수정 전 main에서 모두 실패하고 수정 뒤 통과했다. 리뷰에서 나온 P2(uri 후보에 다른 accession이 섞이면 conflicting) 1건을 고치고 회귀를 더했다. 독립 검증에서 다른 version·isoform 응답이 미확인이나 `found`로 내려가던 회귀(main은 `conflicting`)를 찾아 고치고 회귀 10건을 더했다. 전체 pytest 1947 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: 같은 기록의 다른 version(ENSG…17과 ENSG…16)을 재인용으로 보지는 않는다. 대응 ID는 resolver가 `same_as`로 줄 때만 쓰며 live resolver는 아직 없다. REVIEW v2와 pack 검사는 연구 실행 경로(#90 PR 3) 전이라 호출하는 쪽이 작성자를 넘겨야 한다.
- 근거: `labhq/evidence/verify.py`, `labhq/research/review.py`, `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/gateway/server.py`, `labhq/cli.py`, `tests/test_evidence_verify.py`, `tests/test_research_review.py`, `tests/test_research_protocol.py`, `tests/test_research_evidence.py`.

## 2026-10-01 · #191 — 재사용 workdir 통제 링크 차단

- 결론: resume·retry가 기존 workdir을 열기 전에 통제 구역 링크를 검사하고, 링크나 검사 불완전이 있으면 실행을 거부한다.
- 바뀐 것: #132의 공용 `zone_links` 경로를 재사용했다. adapter와 `TASK.md` 생성보다 검사를 앞세웠으며 패치노트는 건드리지 않았다.
- 실행한 것: 회귀 2건은 수정 전 실패, 수정 뒤 통과했다. 관련 test 97 passed/1 skipped, 전체 pytest 1923 passed/23 skipped, Node 11개와 `scripts/check_public.sh`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/runner/daemon.py`, `tests/test_intake_references.py`.

## 2026-10-01 · #189 Windows CI flaky test 조건 대기

- 결론: bench process tree, e2e 이벤트·wake 결과, semantics breaker 저장을 고정 시간 대신 상한 있는 polling으로 확인한다. 검사는 그대로 두고 실패 시 PID·프로세스 상태·이벤트 순서·breaker 상태를 출력한다.
- 실행한 것: 수정 중 e2e 6회차에서 `recruit.suggested`·HPC 이벤트 누락을 재현했다. 최종 변경 파일별 20회 실패 0, 전체 pytest 3회 각각 1889 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 바뀐 파일: `tests/test_bench.py`, `tests/test_e2e_mock.py`, `tests/test_semantics_shadow_breaker.py`, `STATUS.md`. 새 의존성과 patch note 변경은 없다.

## 2026-10-01 · PR #176 3회차 — 참고 공개 가림 부류 종료

- 결론: 같은 부류의 P1 4154275555를 닫았다. 공개 게시 전에 path와 PI 기본 GitHub 참고를 한 함수에서 정규화해 경로와 저장소 정체성을 함께 가린다. P3 4154275565의 README 중복도 합쳤다.
- 바뀐 것: `mask_published_references`가 POSIX·Windows drive·UNC·백슬래시·대소문자·home·URL 표기를 다루고, GitHub URL·`owner/name`·clone 폴더명을 가린다. 표기별 회귀 11건을 한 표로 묶었다. README 참고 항목은 2개에서 1개, 39,376자에서 38,282자로 줄었다. 패치노트는 건드리지 않았다.
- 실행한 것: 회귀 표는 수정 전 5 failed/6 passed, 수정 뒤 11 passed였다. 관련 test는 154 passed/1 skipped, 전체 pytest 1892 passed/22 skipped, Node 11개, `scripts/check_public.sh`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/intake.py`, `labhq/integrations/github.py`, `tests/test_intake_references.py`, `README.md`.

## 2026-10-01 · PR #176 봇 리뷰 P1 3건 — 짧은 home 참고와 공용 경로 scanner

- 결론: P1 3건을 닫았다. 짧은 home 참고를 모든 러너 표현에서 가리고, 경로·통제 구역 탐색은 공용 scanner로 입력 길이에 비례해 돈다.
- 바뀐 것: path mask의 길이 필터를 없앴다. `mentions_zone`·`touches`·`touches_resolved`·`sanitize`는 separator run 첫머리만 검사하는 scanner를 함께 쓴다. 패치노트는 건드리지 않았다.
- 실행한 것: 2회차 회귀 4개는 수정 전 각각 약 3초로 2초 상한을 넘었고 수정 뒤 합계 0.84초였다. 전체 pytest 1881 passed/22 skipped, Node 11개, `scripts/check_public.sh`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/intake.py`, `labhq/policy.py`, `labhq/integrations/github.py`, `tests/test_intake_references.py`, `tests/test_policy.py`, `tests/test_github_reporter.py`.

## 2026-10-01 · #36 PR A 후속 8건 — 참고 자료 정보 경계와 폴더 링크 검사를 판정 하나로

- 결론: 참고 자료의 경로·URL이 공개 보고·라운드 기록·prompt로 새는 길과, 참고·upstream·project 폴더의 링크·통제 구역 검사를 각각 공통 함수로 모았다. #123 #124 #130 #131 #132 #133 #134를 고쳤다. #125의 확인 조건은 #122에서 이미 테스트와 함께 들어가 있어 판정만 공통 함수로 옮겼다.
- 바뀐 것: `intake.path_pattern`·`url_pattern`(구분자 run, drive·Git Bash, JSON `\\`·`\/`·`\uXXXX`, 기본 포트, 대소문자)을 prompt 지우기(#133)와 게시 가림이 함께 쓴다. 프로젝트 보고와 라운드 기록은 `github.publish_clean` 하나를 쓴다(#130). 가림 대상은 `published_reference_masks`가 정한다: 모든 path 참고, PI 기본 github(URL·`owner/repo`)·url(#123). `~` 참고는 적은 그대로 저장하고 러너가 자기 home으로 푼다(#124). 공개 가드는 webhook path·session·code도 가린다(#134). 실경로 게이트는 후보 256개를 넘으면 셸·MCP·Glob을 ask로 보낸다(#131). `intake._walk`·`overlaps_zone` 위에 참고(엄격)·upstream(구역 링크가 있으면 열지 않음)·project(열되 Claude 거부 규칙) 검사를 얹었다(#132). README §4·§8·§10을 맞췄다.
- 실행한 것: issue마다 회귀 test를 먼저 썼고 수정 전 코드에서 모두 실패함을 확인했다(새 test 11개·12건, 기존 루트 검사 1건 강화). 링크 훑기 비용을 쟀다: Windows 11에서 항목 20,200개가 1,078 ms에서 33 ms로 줄었다(ismount를 POSIX에서만 부름). 전체 pytest 1555 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh`, `git diff --check` 통과. 검증에서 여섯 가지를 더 고쳤다: project 링크의 별칭(`b`가 `a`와 같은 폴더, 안쪽 별칭, 폴더 자신으로 돌아오는 링크)에도 거부 규칙을 붙이고, 훑기가 통제 구역 안을 열거나 그 안의 링크 이름을 로그에 남기지 않게 했다. 가림 정규식이 긴 줄을 위치마다 다시 훑지 않게 했다. 구분자로 여는 match는 구분자 run 첫머리에서만 시작하고, 나머지 열린 반복은 상한으로 묶었다(공백 없는 60 KB 줄 여섯 개: 267초 → 0.5초, 구분자 수에는 상한 없음). `CORPlice:pw@` 같은 도메인 계정 userinfo가 다시 가려진다(이 PR에서 생긴 회귀). 공개 가드가 통제 구역을 참고 가림보다 먼저 본다(`/refs` 참고가 `/srv/refs/vault` 줄을 가려 구역 판정을 피하던 회귀). IPv6 host URL 참고도 query가 떨어지고 가려진다. 새 test 5건, 기존 1건 강화.
- 미해결: 자체 호스팅 webhook처럼 이름 없이 path에 든 비밀값은 못 가린다(README §10). PI 기본 url이 공개 사이트면 그 host URL도 보고에서 가려진다. project 링크 거부 규칙은 Claude만 따르고 Codex·셸은 막지 못한다. Windows에서 대소문자만 다른 유지 참고가 거부 경로와 함께 지워질 수 있다. 패치노트는 PR 번호가 생긴 뒤 쓴다. 거부 규칙은 적힌 경로 비교라 8.3 짧은 이름 같은 표기는 남을 수 있다. 나머지 P2는 후속 issue로 넘긴다.
- 근거: `labhq/intake.py`, `labhq/integrations/github.py`, `labhq/integrations/rounds.py`, `labhq/policy.py`, `labhq/runner/daemon.py`, `tests/test_intake_references.py`, `tests/test_github_reporter.py`, `tests/test_round_records.py`.

## 2026-10-01 · PR #164 2회차 봇 리뷰 P1 — 읽기 전용 workspace 지시 경계 통합

- 결론: 읽기 전용 실행이 workspace에서 읽을 수 있는 지시·memory·skill·설정 이름을 한 판정 함수로 모았다. 깊이와 숨김 폴더에 관계없이 Claude Code는 차단 목록으로 제외하고, 끌 수 없는 agents-md와 Codex project 지시는 실행 전에 거부한다.
- 바뀐 것: adapter가 쓰는 `CLAUDE.md`·`CLAUDE.local.md`·`.claude/**`·`AGENTS*.md`·`.agents/**`·`.codex/**` 규칙을 `read_only.py` 한 곳에 뒀다. 계약 skill은 매 실행 원본에서 다시 만들며 원본이 없으면 stale 사본을 지우고 실행을 거부한다. POSIX directory symlink와 Windows junction은 대상 내용을 건드리지 않고 링크만 제거한다.
- 실행한 것: 새 회귀 묶음은 수정 전 6 failed/1 skipped(POSIX 전용), 수정 후 관련 64 passed/1 skipped였다. 전체 pytest 1674 passed/22 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check`가 통과했다.
- 미해결: 새 CLI가 다른 workspace 지시 파일 이름을 도입하면 중앙 목록을 갱신해야 한다(README §10).
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/claude_code.py`, `labhq/runner/workspace.py`, `tests/test_read_only_followups.py`.

## 2026-10-01 · PR #164 봇 리뷰 P1 — 재사용 workspace의 계약 skill 변조 차단

- 결론: 계약 skill은 매 실행 직전에 원본에서 새로 복사한다. 이전 writable step이 설치본 내용을 바꾸거나 skill 디렉터리를 symlink·Windows junction으로 교체해도 read-only run은 그 지침을 읽지 않는다.
- 바뀐 것: `TaskWorkspace.install_skill()`이 링크가 아닌 상위 폴더에서 임시 복사본을 만든 뒤 기존 설치본을 링크 대상까지 따라가지 않고 교체한다. 상위 폴더가 링크이거나 일반 폴더가 아니면 건드리지 않아 기존 workspace 검사가 read-only run을 거부한다.
- 실행한 것: 내용 변조와 junction 치환 회귀 2건이 수정 전 실패하고 수정 후 통과했다. 관련 테스트 48개, 전체 pytest 1562 passed/21 skipped, Node 테스트 11개, `bash scripts/check_public.sh`, `git diff --check`가 통과했다.
- 근거: `labhq/runner/workspace.py`, `tests/test_read_only_followups.py`.

## 2026-10-01 · PR #122 후속 다섯 건(#135 #145 #146 #147 #148) — 읽기 전용 실행의 env·작업 폴더 지침 파일

- 결론: 이어 묻기·상담의 허용 목록을 argv 밖까지 넓혔다. 엔진 env는 로그인·설정 위치·API 접속 변수만 받고, 러너가 물려받은 `CODEX_*` 세션 변수는 모든 직원 실행에서 빠지며, 엔진이 작업 폴더에서 지침·설정으로 읽는데 끌 플래그가 없는 파일이 있으면 읽기 전용 실행을 띄우지 않는다. 실측할 수 없던 동작(신뢰된 경로 아래 Codex project config, Claude built-in agents-md)은 거부 쪽으로 뒀다.
- 바뀐 것: `labhq/util.py`(`CODEX_ENV_PASSTHROUGH`, `strip_parent_session_env`), `labhq/adapters/read_only.py`(`READ_ONLY_ENV_KEEP`, `READ_ONLY_WORKSPACE_REFUSED`, `read_only_workspace_error`), `labhq/adapters/base.py`(`staff_env`, 뺀 env 이름을 피드에 경고), Codex preflight가 작업 폴더 `AGENTS.override.md`를 모든 실행에서 거부, 읽기 전용 Claude가 작업 폴더 CLAUDE.md류를 하위 폴더 것까지 `claudeMdExcludes`에 넣음(하위 폴더는 독립 검증에서 찾은 틈), `labhq/bench.py` baseline env 순서를 직원 실행과 맞춤. #135는 main에 이미 고쳐져 있어(12e3fba) 테스트만 더했다. B1 그림자 모드 PR(#150)의 파일(gateway/server.py, cli.py, research/semantics*.py, settings.py)은 건드리지 않았다.
- 실행한 것: 새 `tests/test_read_only_followups.py` 22개 중 20개가 수정 전 코드에서 실패했다(나머지 2개는 PI가 고른 env·Claude의 .codex 오탐 방지). 로컬 Codex 리뷰 1회 P2 1건(bench가 engine env의 CODEX_*까지 지움)을 고쳤다. 독립 검증에서 하위 폴더 CLAUDE.md 틈 1건을 고쳤다. 전체 pytest 1560 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh`, `git diff --check` 통과. 실제 CLI probe는 하지 않았다.
- 미해결: #148의 신뢰 경로 probe와 Claude agents-md가 `AGENTS.md`를 읽는지는 재지 않았다(재면 거부를 풀 수 있다). 러너를 띄운 셸의 `CLAUDE_*`·`CODEX_*` 밖 변수(`NODE_OPTIONS` 등)와 작업 폴더 상위의 `.codex/`·지침 파일은 보지 않는다. 일반 쓰기 step은 작업 폴더 `.codex/`를 그대로 둔다. 계약 skill 폴더를 앞선 실행이 고친 것은 알아채지 못한다.
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/codex.py`, `labhq/util.py`, `tests/test_read_only_followups.py`, README §8·§10.

## 2026-10-01 · #63 README 사무실 화면을 15초 움직이는 이미지로

- 결론: README의 2.5D·3D 사무실 정지 화면을 각 15초짜리 움직이는 WebP로 바꿨다. 요청 하나가 CSO 계획, 승인 카드, 단계 진행을 거쳐 끝난다. 기존 PNG는 정지 화면 링크로 남겼다.
- 바뀐 것: `docs/media/office-25d.webp`(1.31 MB, 1200×750, 15fps, 217프레임), `docs/media/office-3d.webp`(2.38 MB, 1200×750, 12fps, 177프레임), README 이미지 줄과 설명 문구. 코드·의존성·pyproject는 그대로다.
- 실행한 것: 임시 state_dir에서 mock demo(키·클러스터 없음)를 띄우고 headless Chrome을 CDP screencast로 녹화해 Pillow로 인코딩했다. 녹화 script는 PI 결정(일회성 코드)대로 저장소에 넣지 않았다. 녹화 중에만 mock 단계를 0.9초(계획 1.4초)씩 늦추고 승인은 3초 뒤 자동으로 했다. 같은 프레임의 GIF는 51.5 MB·52.3 MB라 WebP를 골랐다. 프레임에 로컬 경로·사용자명·token이 보이지 않는 것을 확인했다.
- 미해결: 3D는 headless Chrome의 하드웨어 GPU로 녹화했다. SwiftShader는 1600×1000에서 초당 4프레임 정도라 끊겼다. 화면이 바뀌면 다시 녹화해야 한다.
- 근거: `docs/media/office-25d.webp`, `docs/media/office-3d.webp`, `README.md`.

## 2026-10-01 · 연구 결과·출처 검증 후속 6건 (#114 #116 #117 #118 #128 #129)

- 결론: 연구 결과와 출처 검증에 남은 빈틈 여섯 개를 닫았다. 연구 단계 실행은 여전히 opt-in이고 기본 꺼짐이라, 바뀐 것은 schema·검사·verifier뿐이다.
- 바뀐 것: `single_cell_de@2`가 통과할 조합이 없던 `normalized_counts`를 field에서 거절한다(#114, 내장 pack hash 고정, 없어진 version을 적으면 남은 version을 알림). 결과는 선택한 단계의 `claim_ids` 밖 claim을 못 내고, 필수 evidence slot마다 `evidence.slots`로 채운 행이 있어야 한다(#128). 0건 검색 행의 출처도 해소하고, 추론·가설 행도 외부 출처면 `accessed_at`이 필요하다(#129). 조회는 동시 4개, 보고 전체 120초 deadline이고 넘긴 것은 미확인이다(#116). ID 옆 URI가 다른 곳을 가리키면 `conflicting`이고, 주요 레지스트리 URL(GEO·PubMed·PMC·identifiers.org 등)은 ID와 같은 출처로 묶인다(#117). DOI 옆 PubMed URL처럼 체계가 다르거나 version·isoform만 다른 URL은 코드가 결함으로 단정하지 않고 resolver에 넘기며, `/search`·`/docs` 같은 경로는 레코드로 읽지 않는다. 근거 행에 `result_count`를 두고, claim별 REVIEW v2(`labhq/research/review.py`)가 부재를 근거로 쓴 것을 늘 중대 결함으로 받는다(#118). 의미 모델 pilot fixture의 합성 결과 네 개에 slot을 적어 `FIXTURE_SHA256`이 바뀌었다(답은 그대로).
- 실행한 것: issue마다 회귀 test를 먼저 써서 수정 전 실패를 확인했다(#114 9개, #128 3개, #129 5개, #116 3개, #117 25개, #118 test 파일 전체·result_count 1개). 독립 검증에서 정상 출처를 결함으로 판정하던 두 경우를 고치고 회귀 test 13개(수정 전 12개 실패)를 더했다. 전체 pytest 1605 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과. Python 3.10 문법은 ast로만 확인했다.
- 미해결: live resolver가 아직 없고 deadline·동시성은 함수 인자다(설정 키 없음). 레지스트리 밖 URL을 ID와 함께 적으면 resolver가 uri 조회를 지원할 때까지 미확인이다. `result_count` 없이 "0 hits"만 적은 행은 reviewer가 잡아야 하고, REVIEW v2는 실행 경로에 연결하지 않았다. 설정에 `single_cell_de@1`이 남아 있으면 연구 계획 단계에서 오류가 난다.
- 근거: `labhq/research/packs/single_cell_de.yaml`, `labhq/research/contract.py`, `labhq/research/review.py`, `labhq/evidence/claims.py`, `labhq/evidence/verify.py`, `docs/research_protocol.md`, `tests/test_research_protocol.py`, `tests/test_research_evidence.py`, `tests/test_evidence_verify.py`, `tests/test_research_review.py`.

## 2026-10-01 · #150 B1 의미 모델 그림자 — 요청 뒤 두 모델을 계산해 로컬에만 기록

- 결론: `semantics: shadow`면 요청이 끝난 뒤 출처 의미 모델(#136)과 읽기 전용 객체·링크 뷰를 계산해 같은 줄에 나란히 남긴다. 기본은 off다. mock lab에서 off·shadow·보류 mode·오타 네 경우의 prompt·schema·계획·승인·결과·리뷰·round 기록·웹 snapshot·event가 같았다(diff 0). mock 요청의 재사용 후보는 0이었다. 산출 type 선언이 없어서이고, 이 공백을 재는 것이 B1의 목적이다.
- 바뀐 것: `Settings.semantics` 한 칸, 새 `labhq/research/semantics_shadow.py`(설정·worker·산출 hash·자동 off·report)와 `semantics_objects.py`(객체 8종·링크 12종, 액션 없음), `semantics.py`의 `records_from_rows`, `labhq semantics report|enable|mark`, `scripts/semantics_shadow_remove.py`. 연결은 `# semantics-hook` 표시 줄 24개(settings 2·server 9·cli 13)뿐이다. 기록은 `gateway.state_dir/semantics/`에 ID·종류·hash·개수만 남는다.
- 실행한 것: 기능 commit마다 새 test를 먼저 돌려 실패를 확인했다. 예외: 산출 hash의 승격 금지 test 2개와 git work tree 거부 test 1개는 앞 commit에서 이미 통과했다(후보가 원래 0이거나 worker commit의 기능). 전체 pytest 1668 passed/22 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과. 제거 시험은 임시 사본에서 지운 뒤 compile·설정 load·state 읽기와 e2e·pilot·cso test 통과. 로컬 Codex 리뷰 5회에서 P2 13건이 나왔고 모두 고쳤다(P1 없음). 이벤트 루프 부하는 1·3회차에 같은 부류로 나와 DB 읽기를 worker의 별도 읽기 전용 연결로 옮겨 닫았다. 나머지는 manifest 반영·구역 검사, observed 만료, 옵션 붙은 off, 멈춘 worker 뒤 새 epoch, 외부 off 즉시 반영, report 상태, git work tree 거부의 링크·CLI 우회, 멈춘 작업의 기록, 경로 표기였다. 리뷰는 5회에서 멈췄다. Python 3.10은 compile만 확인했다.
- 미해결: 원격 runner 산출 hash(생성 시점 hash를 provenance에 싣기)는 별도 issue가 필요하다. 판정 기한 issue(병합+90일, 중간 +30일)와 semantics CI job은 이 PR에 없다. B2(CSO advisory)·팔란티어식 액션·EDAM(#151)은 PI 결정으로 보류다. symlink test는 이 Windows 계정에 권한이 없어 skip했고 junction test는 통과했다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/research/semantics_shadow.py`, `labhq/research/semantics_objects.py`, `scripts/semantics_shadow_remove.py`, `tests/test_semantics_shadow_*.py`, `tests/test_semantics_objects.py`, `tests/semantics_shadow_lab.py`.

## 2026-10-01 · #120 Slurm 스케줄러, #153 README 생성 블록 겹침

- 결론: `hpc.scheduler: slurm`으로 HPC 직원 도구가 Slurm 클러스터에 붙는다. 제출 전 PI 승인, 실패와 '잡 없음' 구분, 제출 재시도 없음, 수면·기상, `ssh_host`·`submit_prefix`·`job_group`이 SGE·PBS와 같은 경로로 돈다. README 배지에 SLURM이 settings Literal에서 저절로 생겼다. `integrations.py --write`는 두 생성 블록이 겹치면 아무것도 쓰지 않고 실패한다.
- 바뀐 것: `labhq/tools/scheduler.py`(`sbatch --parsable`, `squeue` 다음 `sacct`, `scancel`, 배열·het 기록 묶기), `HpcSettings.slurm.sbatch_args`(기본 `--export=NONE`, 알 수 없는 placeholder는 load 때 거부), `labhq init`의 `sbatch`·`sinfo` 판별(Slurm의 Torque wrapper `pbsnodes`는 PBS로 보지 않고, 두 종류가 보이면 묻는다), doctor의 Slurm 명령 점검. 같은 부류는 공통 판정으로 닫았다: 직원이 준 job id는 세 스케줄러 모두 숫자로 시작해야 받는다(옵션 `scancel --user=…`, Torque `qdel all`, SGE 잡 이름 같은 일괄 선택 차단), 스크립트 머리의 주석·지시는 세 종류 모두 preamble 위에 둔다, 셸 직접 실행 확인 목록에 `srun`·`salloc`·`scancel`·`qrsh`·`qlogin`을 더해 Bash·PowerShell이 같은 상수를 쓴다. 제출 시간 초과·id 해석 실패는 "제출됐을 수 있으니 hpc_queue부터 보라"고 알린다.
- 실행한 것: 가짜 `sbatch/squeue/sacct/scancel`(`tests/fixtures/fake_slurm.py`)로 제출→대기·보류→실행→완료·실패·OOM·시간 초과·취소, accounting 지연, 러너 watcher의 수면·한 번 기상을 확인했다. MCP stdio fixture로 Slurm 오류 5종과 잘못된 job id가 직원 tool error로 가는지 봤다. 회귀 test는 수정 전 코드에서 실패를 확인했다(#153 중첩 순서, MCP 6, init 4, doctor 3, 수정 전 import가 없는 `test_slurm.py` 전체). 로컬 Codex 리뷰 P2 2건(압축된 array id `4100_[1,8]`이 `hpc_status`에서 거부됨, `--clusters` 제출의 추적 대상 소실)은 `squeue -r`과 `-M`/`--clusters` 거부로 고쳤다. 독립 검증에서 같은 부류가 두 군데 더 나와 고쳤다. 직원 스크립트의 `#SBATCH -M`/`--clusters`는 제출 전에 거부하고, 다른 경로로 다른 cluster에 간 제출(`id;cluster`)은 로컬 id로 추적하지 않고 오류로 알린다. job id 판정을 '옵션 모양 거부'에서 '숫자로 시작'으로 바꿨다. 전체 pytest 1645 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과.
- 미해결: 실제 Slurm 클러스터 제출은 하지 않았다. accounting이 없는 클러스터, `PrivateData=jobs`에서 다른 계정이 낸 잡, `--export=NONE`과 잡 안의 `srun`, `#SBATCH --array`가 core-hour에 안 들어가는 점은 README §10에 적었다. 패치노트 행은 PR 번호가 생긴 뒤 따로 쓴다.
- 근거: `labhq/tools/scheduler.py`, `labhq/settings.py`, `labhq/init_wizard.py`, `labhq/doctor.py`, `scripts/integrations.py`, `tests/test_slurm.py`, `tests/fixtures/fake_slurm.py`.

## 2026-10-01 · #127 출처·재사용 의미 모델 비교 pilot (PR A: opt-in으로 main에)

- 결론: 중단 기준은 충족했다. 기준선 A(메모리 SQLite + 관계 표 + 재귀 CTE)가 모델 B와 같은 의미·정확도를 냈다(base·변경 1·변경 2 모두 17/17, 잘못된 동일시 0, 소비자 불일치 0). 그러나 PI 결정(2026-10-01)으로 접지 않고 opt-in·실행 경로 미연결로 유지한다. 그림자 모드로 실데이터를 모아 #121에서 재평가한다. 합성 fixture 결과이고 실제 요청의 효과가 아니다.
- 바뀐 것: 새 파일 `labhq/research/semantics_v1.yaml`·`semantics.py`(B, 공유 read_records), `tests/semantics_baseline.py`(A), `tests/test_semantics_pilot.py`·`test_semantics_public.py`, `scripts/semantics_pilot.py`, `docs/reference/semantics_pilot.md`. 후속 6건을 고쳤다: B resume 시각 unknown(#137), A claim 반복 보고(#138), B independent_groups(#139), A 노드 단위 순회(#140), reader immutable 경쟁(#141), LLM 0 차단·script import 검사(#142). `expected.yaml`·hash·모델 YAML은 그대로다. `pyproject.toml` package-data 1줄. 설정·실행 경로·CSO·state_dir은 그대로다.
- 실행한 것: 후속마다 수정 전 실패하던 회귀 test(#137 TypeError, #138 IntegrityError, #139 group 누락, #140 16층 1.83초, #141 writer 행 놓침, #142 os 함수 실제 실행 시도). 측정 다시 실행(1× warm 200회·5 batch, 50× 복제, 20층 diamond A 1.9 ms·B 0.2 ms). Codex 리뷰 1회(새 결함 없음). 전체 pytest 1401 passed/20 skipped, `node tests/*.cjs` 8개, `bash scripts/check_public.sh`, `git diff --check` 통과. 격리 test(설정 키 0, 실행 경로 import 0, settings.py diff 0, CSO prompt 동일)도 그대로 통과한다.
- 미해결: #143(접는 커밋 순서)은 접지 않으므로 닫는다. 변경 내용을 구현 전에 알아 B에 분기를 미리 넣었으므로 변경 시간 지표는 변경 비용을 재지 못했다. A의 N+1 SQL 탓에 성능 비교는 근거가 약하다. 그림자 모드·팔란티어식 운영 객체 뷰는 다음 PR.
- 근거: `docs/reference/semantics_pilot.md`, `tests/fixtures/semantics/expected.yaml`, `scripts/semantics_pilot.py`, `tests/test_semantics_pilot.py`.

## 2026-10-01 · #63 README 맨 위 배지 — 종류별 개수 대신 대상 하나씩

- 결론: README 제목 바로 아래 배지가 "builtin MCP 3" 같은 개수가 아니라 대상 이름과 역할을 보여 준다. 누르면 그 대상의 공식 페이지나 README 해당 절로 간다. 없는 대상(Slurm #120, Gemini·Antigravity 직원, LICENSE)은 배지를 만들지 않는다.
- 바뀐 것: `scripts/integrations.py`가 `<!-- badges:start -->` 블록을 만든다. 엔진(Claude Code 직원 8명·Codex 직원 3명과 모델), 지원 스케줄러(`HpcSettings.scheduler` Literal을 ast로 읽어 SGE·PBS, mock·none 제외), labhq MCP(approval·ask·hpc 한 배지), PubMed, bioRxiv / medRxiv, bioinfo-agent plugin, Paper2Agent skill이 한 줄, CI(push·PR workflow `test`)·Python 3.10+(requires-python)·패치노트가 둘째 줄이다. logo는 shields.io에서 그려지는 것을 확인한 simple-icons slug 표(`LOGOS`)만 받는다. §2의 개수 배지는 지웠고 표는 그대로다. 손으로 적던 test·패치노트 배지는 생성 블록으로 옮겼고, `pyproject.toml`에 `[project.urls] Repository`를 넣었다.
- 실행한 것: 생성된 배지 URL을 모두 받아 제목과 logo(`<image>`)를 확인했고 링크 9개가 200이었다. `tests/test_integrations.py` 20개(없는 대상 미생성, settings Literal fixture, workflow·LICENSE 유무, 비공개 값·로컬 경로 미노출, badge anchor가 README 제목과 일치, 검증 안 된 logo 거부). 전체 pytest 1416 passed/20 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과. 로컬 Codex 리뷰는 지적 없음.
- 미해결: Codex(openai)와 bioRxiv는 simple-icons logo가 그려지지 않아 logo 없이 둔다. SGE는 하나로 정해진 공식 페이지가 없어 PBS와 함께 README §8로 링크한다. Python 3.10 실행은 못 했고 문법만 확인했다(3.10 CI가 본다).
- 근거: `scripts/integrations.py`, `tests/test_integrations.py`, `README.md`, `pyproject.toml`.

## 2026-10-01 · #36 PR A 리뷰 2회차 검증 — prefix_args 옵션, Claude·Codex 우회 실측

- 결론: 남은 통로 하나를 닫았다. 읽기 전용 실행은 PI `extra_args`를 빼지만 `prefix_args`는 그대로 앞에 붙였고, Codex가 `exec` 앞의 `--dangerously-bypass-approvals-and-sandbox`로 `-s read-only`에서 파일을 썼다. 이제 `prefix_args`에 옵션이 있으면 읽기 전용 실행을 거부한다.
- 바뀐 것: `read_only_launch_error`(`labhq/adapters/read_only.py`)를 `AgentAdapter.run`이 prepare 전에 부른다. 환경변수 전개 뒤 `-`로 시작하는 항목이 있으면 CLI를 띄우지 않는다. 일반 step은 그대로 쓴다.
- 실행한 것: 어댑터가 만든 읽기 전용 명령을 실제 CLI로 돌렸다. Claude 2.1.282: 엔진 env의 `CLAUDE_CODE_PLUGIN_DIRS` plugin은 실리지만 hook·plugin MCP는 뜨지 않았고 `CLAUDE_CODE_MANAGED_SETTINGS_PATH` hook도 돌지 않았다. Codex 0.159.2(Windows elevated sandbox): shell 쓰기 거부, 작업 폴더 `.codex/config.toml`의 notify·MCP와 `.codex/hooks.json`은 안 읽힘, `windows.sandbox` 없이도 정책 거부, workspace-write 세션을 이어도 read-only 유지. 새 테스트 2개는 수정 전 실패를 확인했다.
- 미해결: 같은 follow-up이 gateway 재시작 뒤 이전 실행과 같은 session·workdir를 쓸 수 있다(로컬 Codex 리뷰 P2). 엔진 env로 실린 Claude plugin은 실측상 쓰기 통로가 아니지만 허용 목록 밖이고, 러너가 물려주는 `CODEX_*` 변수(`CODEX_EXEC_SERVER_URL` 등)는 재지 않았다. 셋 다 후속 issue로 넘긴다.
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/base.py`, `tests/test_read_only_profile.py`.

## 2026-10-01 · #36 PR A 리뷰 2회차 — 읽기 전용 허용 목록 profile, 사후 파일 비교

- 결론: 이어 묻기·상담은 직원 설정에서 지우는 방식이 아니라 허용 목록 profile로 돈다. plugin·hook·MCP·PI extra_args가 빠지고, 그래도 실행 중 파일이 바뀌면 결과를 실패로 하고 PI에게 알린다. 1회차 MCP 우회와 2회차 plugin hook 우회는 같은 부류(지우기 목록에 남은 새 통로)라 구조로 닫았다.
- 바뀐 것: `labhq/adapters/read_only.py`의 `read_only_profile`이 직원의 이름·역할·지침·모델·한도·`project_dirs`·`disallowed_tools`만 가져온다. 분류되지 않은 AgentSpec 필드가 생기면 만들기를 거부한다. 러너는 보낸 쪽 override를 보지 않는다. Claude는 `--setting-sources ""`·`disableAllHooks`·plugin 없음, Codex는 `--disable hooks·plugins·apps·computer_use·browser_use`이고, 둘 다 `isolate_user_config`와 상관없이 격리한다. 러너가 실행 전후로 작업 폴더와 쓰기 가능한 project·upstream·참고 폴더를 비교한다(상한 `runner.read_only_check_max_entries` 50,000을 넘으면 실행 거부). 바뀐 목록은 manifest `read_only_changes`, 피드 경고는 `agent.log` level `alert`.
- 실행한 것: Claude 2.1.282로 실측했다. 수정 전 명령에서 작업 폴더·plugin의 SessionStart·Stop hook 4개가 plan 모드와 `Read,Glob,Grep`을 거치지 않고 돌았고 새 명령에서는 하나도 돌지 않았다(fixture `claude_read_only_*.jsonl`). Codex feature 이름은 codex-cli 0.159.2 `features list`로 확인했다. 새 테스트 22개 중 18개가 수정 전 코드에서 실패했다(나머지 4개는 기존 금지 목록 유지·오탐 방지·fixture 고정), Node 1개도 수정 전 실패를 확인했다. 로컬 Codex 리뷰 P1 1(profile이 `disallowed_tools`를 지움)·P2 2(폴더 ctime 오탐, 취소 때 비교 누락)를 모두 고쳤다. 전체 pytest 1402 passed/20 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과.
- 미해결: Windows에는 ctime이 없어 크기를 두고 mtime을 되돌린 수정은 못 본다. 다른 러너·프로세스가 같은 폴더를 쓰면 실패로 잡힌다. 감시 폴더 밖 쓰기와 Claude managed settings hook은 범위 밖이다. 옛 Codex에 `--disable` 이름이 없으면 읽기 전용 실행이 CLI 오류로 실패한다. Codex 실측 probe는 하지 않았다.
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/claude_code.py`, `labhq/adapters/codex.py`, `labhq/runner/integrity.py`, `labhq/runner/daemon.py`, `tests/test_read_only_profile.py`, `tests/fixtures/real/claude_code/claude_read_only_*.jsonl`.

## 2026-10-01 · #36 PR A 리뷰 1회차 — 읽기 전용 wrap-up, 링크로 적힌 통제 구역

- 결론: 이어 묻기·상담이 턴 한도에 걸려도 읽기 전용이 풀리지 않는다. 링크(symlink·junction)로 적힌 통제 구역은 러너가 실제 경로로 막는다. P1 두 건을 고쳤고 P2 다섯 건은 고쳤으며 네 건은 후속 issue로 넘긴다.
- 바뀐 것: read-only 작업은 wrap-up을 건너뛰고, 다른 작업의 wrap-up은 기존 override에 `max_turns`만 더한다. 러너가 통제 구역도 resolve해 비교한다. 질문 `options`가 list가 아니면 자유 입력 질문으로 읽고 list 밖의 질문 하나도 버리지 않는다. 참고 값의 NEL·DEL·U+2028/2029를 거부한다. 게이트웨이 루트 비교는 OS와 무관한 글자 비교다(Windows 게이트웨이의 POSIX 루트). 작업·프로젝트 폴더를 품은 참고는 뺀다. 프로젝트 보고의 경로 가림은 대소문자·구분자와 무관하다.
- 실행한 것: 로컬 Codex 리뷰(origin/main 대비) P1 1·P2 1 모두 고침. 새 테스트 13개 중 12개가 수정 전 실패함을 확인했다(1개는 이미 막히던 url 줄바꿈을 고정). 전체 pytest 1234 passed/19 skipped, `node tests/*.cjs` 10개, `bash scripts/check_public.sh` 통과.
- 미해결: PI 기본 참고의 github·url 가림, `~` 경로를 러너 계정 기준으로 풀기, 참고 폴더 안의 링크가 통제 구역을 가리키는 경우(project_dirs와 같은 한계), snapshot의 이어 묻기 답 길이. 실제 Claude·Codex CLI는 돌리지 않았다.
- 근거: `labhq/orchestrator/cso.py`(run_step wrap-up), `labhq/runner/daemon.py`(`_reference_dirs`), `labhq/intake.py`, `labhq/integrations/github.py`, `tests/test_followup.py`, `tests/test_intake_references.py`, `tests/test_intake_questions.py`.

## 2026-10-01 · #36 접수·참고 자료 PR A — 구조화 확인 질문, 참고 포인터, 이어 묻기

- 결론: CSO 확인 질문이 선택지 버튼·자유 입력·깊이(약 30/60/90분)로 폰에 뜨고, 답은 #34 경로로 재계획에 들어간다. 요청에 GitHub·DOI·PMID·URL·러너 경로 포인터를 붙이면 브리핑·계획·단계 prompt에 들어가며 경로는 쓰기 권한 없이 열린다. 끝난 요청은 같은 CSO 세션에 이어 물을 수 있다.
- 바뀐 것: `PLAN_SCHEMA.clarifying_questions`가 `{question, options 2-4, allow_free_text, depth?}`이고 문자열도 읽는다. PR 1 연구 PLAN도 같은 구조를 받되 문자열 질문은 그대로 둬서 기존 hash가 그대로다. `RequestIn.references`·`default_references`, `pi_profile.references`·`runner.reference_roots`(example은 빈 값), `labhq send --ref`·`--no-default-refs`, 2.5D **참고** 칩을 넣었다. 경로는 게이트웨이가 루트·통제 구역으로 거르고 러너가 실제 경로로 다시 확인한다. Claude는 `--add-dir`과 Edit·Write 거부, Codex는 `--add-dir` 없이 읽고, 쓰기 가능한 참고 경로는 러너가 한 번 경고한다. 프로젝트 GitHub 보고에서는 경로 참고를 가린다. `POST /api/requests/{id}/followup`은 읽기 전용으로 resume하고 요청 상태를 바꾸지 않는다. 4열 작업판이 패널 폭을 넓히던 문제도 고쳤다.
- 실행한 것: 기능마다 회귀 테스트를 먼저 썼다. 새 테스트를 main 위에서 돌려 실패함을 확인했다(pytest 61개: 질문 8·참고 44·이어 묻기 8·e2e 1, Node 3·4·2. 기존 승인 게이트 동작을 고정하는 테스트 1개는 main에서도 통과). 수정 후 전체 pytest 1222 passed/19 skipped, `node tests/*.cjs` 10개, `bash scripts/check_public.sh` 통과. 로컬 게이트웨이와 mock 러너로 2.5D·3D 질문 카드, 참고 칩, 이어 묻기를 브라우저에서 눌러 확인했다(375px 폭 포함). 로컬 Codex 리뷰(branch 대 main): P1 미리 허용된 셸이 참고 경로에 쓸 수 있음 → prompt 규칙·쓰기 가능 경고·README로 대응(셸 sandbox는 범위 밖), P2 이어 묻기 초안이 다른 요청으로 넘어감 → 고침.
- 미해결: 미리 허용된 셸 명령의 참고 경로 쓰기는 OS 권한으로만 막힌다. 실제 Claude·Codex CLI로는 돌리지 않았다. Codex가 `--add-dir` 없이 참고 경로를 읽는다는 것은 sandbox 기본 동작에 기댄 것이고, Windows elevated sandbox에서 확인하지 않았다. Gemini·Antigravity·cli 직원은 경로를 prompt로만 받는다. 범위 정책·`labhq_kb` 색인·브리핑 현실화는 PR B·C다. patch notes는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/intake.py`, `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `labhq/runner/daemon.py`, `labhq/web/ui/decide.js`, `labhq/web/ui/refs.js`, `tests/test_intake_questions.py`, `tests/test_intake_references.py`, `tests/test_followup.py`, `tests/web_clarify_options.cjs`, `tests/web_refs.cjs`, `tests/web_followup.cjs`.

## 2026-10-01 · #90 연구 수행 규약 PR 2 앞부분 — 리뷰 보강

- 결론: verifier는 지지·반박 출처가 모두 확인돼야 claim을 `verified`로 둔다. context 행·추론 행에 든 가짜 ID도 보고 전체를 실패로 만든다. 같은 출처를 ID·doi.org URL·artifact 별칭으로 나눠 독립 근거로 세던 길도 막았다.
- 바뀐 것: `verify.py`는 출처가 가진 식별자(외부 ID 또는 URI, artifact)를 모두 검사해 가장 나쁜 판정을 남기고(`resolutions`), 결함 행을 `defective_evidence`로 모은다. URI는 scheme·host만 대소문자를 무시하고 artifact 경로 구분자는 정규화한다. `claims.py`는 재인용을 출처의 모든 표기로 판정한다. 추론·가설 행은 관찰·조회 행까지 이어져야 하고, `comparable`은 적어 둔 method도 같아야 한다. `accessed_at`은 YYYY-MM-DD만 받는다(3.11+의 주 날짜 차단). 규약 문서 §3·§4를 맞췄다.
- 실행한 것: Codex 독립 리뷰 1회(P2 2건, 둘 다 고침)와 반대 관점 검토(P1 1건·P2 5건 고침). 새 회귀 12건이 수정 전 코드에서 모두 실패함을 확인했다. 수정 후 전체 pytest 1242 passed/19 skipped, `node tests/*.cjs` 7개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: 조회는 순차이고 전체 시간 상한이 없다. ID와 URI를 함께 적으면 URI는 검사하지 않는다. 0건 검색을 observed로 적는 우회와 REVIEW_SCHEMA의 R05·R07 판정은 R13 범위다. 셋 다 후속 issue로 넘긴다.
- 근거: `labhq/evidence/verify.py`, `labhq/evidence/claims.py`, `tests/test_evidence_verify.py`, `tests/test_research_evidence.py`, `docs/research_protocol.md`.

## 2026-10-01 · #90 연구 수행 규약 PR 2 앞부분 — 증거 원장 R04–R07

- 결론: 연구 결과 schema가 claim·evidence·link를 따로 받는다. 끊긴 참조, 근거 없는 supported/contradicted, 실패·0건 조회로 지지한 link, 평가·출처·정량 필수값 누락을 한 번에 거부한다. 출처 verifier는 조회 실패와 ID 부재를 나눠 남긴다. 연구 실행은 여전히 opt-in·기본 꺼짐이고 원장·verifier는 실행 경로에 아직 연결하지 않았다.
- 바뀐 것: `labhq/evidence/claims.py`(R04 inco 6종 evidence·revision·참조, R05 directness·source_level·independence_group·판정 이유, R06 조회일·원문 위치·검색 범위, R07 quantity·comparison), `labhq/evidence/verify.py`(lookup succeeded/failed/skipped와 found·not_found·insufficient·conflicting·requires_verification, 고정 응답 `StaticResolver`). result v2의 `findings`를 `claims`·`evidence`·`links`로 바꾸고 `validate_research_result`로 동결 계획에 묶었다. #58의 결과 계약·claim ledger·도구 실패 의미 항목을 이 원장으로 합쳤다. 규약 문서 §3·§4(7,741→10,359 bytes)와 README 한 줄을 고쳤다.
- 실행한 것: 기능 커밋마다 그 테스트를 직전 커밋 코드에서 돌려 실패를 확인했다(R04 23/23, R05 8/8, R06 schema 8/8·verifier 모듈 없음, R07 7/7, 리뷰 보강 4/4). 사전 Codex 리뷰의 P2 3건(scheme 혼동, artifact hash 변경, RefSeq 형식)을 고쳤다. 수정 후 전체 pytest 1230 passed/19 skipped, `node tests/*.cjs` 7개, `bash scripts/check_public.sh` 통과. pytest 임시 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: live resolver가 없고 기본은 조회 꺼짐이다. artifact 근거는 runner manifest(R10)가 생겨야 `found`가 된다. R08–R13, reviewer bundle, 실행 경로 연결은 다음 PR이다. 로컬에 Python 3.10이 없어 3.10은 CI로 확인한다. patch notes는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/evidence/claims.py`, `labhq/evidence/verify.py`, `labhq/research/contract.py`, `tests/test_research_evidence.py`, `tests/test_evidence_verify.py`, `docs/research_protocol.md`.

## 2026-10-01 · 재시작 뒤 consult 겹침 #93 · single_cell_de 조합 규칙 #109

- 결론: gateway가 consult 도중 재시작돼도 같은 ask가 같은 CSO session·workdir로 consult를 하나 더 띄우지 않는다. `single_cell_de@1`의 count scale·model 조합과 결론 모드는 rule로 판정해 맞지 않는 PLAN을 CP1 전에 재계획시킨다.
- 바뀐 것: 미완료 consult도 busy로 본다. 같은 ask의 이전 consult는 같은 runner 세대면 결과를 기다려 쓰고, 아니면 새 session·workdir로 다시 묻는다. 재개 중 roster가 비면 runner 재접속을 기다리고, dispatch 복구는 ask_id로 consult를 맞춘다. adopt 결과의 비용은 task ID별로 한 번만 더하고(병렬 task와 이중 합산 방지), 다른 직원이 돌린 consult는 adopt하지 않아 CSO session이 바뀌지 않는다. transient 실패는 남은 재시도만 돈다. pack rule `when`에 predicate 목록(모두 참)을 허용하고 `model_family` field와 rule 8개를 더했다. README §8, `docs/research_protocol.md` §6을 맞췄다.
- 실행한 것: 수정 전 #93 회귀 5건·리뷰 회귀 3건, #109 회귀(log_transformed + pseudobulk 통과, CP1 직행 포함)가 실패함을 확인했다. 수정 뒤 전체 pytest 1196 passed/19 skipped, `node tests/*.cjs` 7개, `bash scripts/check_public.sh`, `git diff --check` 통과. 로컬 Codex 리뷰 P2 4건(1회차 3, 2회차 1)과 자체 검토 1건은 이 브랜치에서 고쳤고, 수정 전 회귀 3건이 실패함을 확인했다.
- 미해결: 격리된 consult가 끝나면 `cso_session_id`가 그 session으로 바뀌는 기존 동작은 그대로다. cell 의사반복과 조건별 replicate 수는 구조화 field가 없어 rule로 만들지 않았다. `single_cell_de@1` 내용 hash가 바뀌어 이전에 고정한 PLAN은 다시 계획해야 한다(pilot, 기본 꺼짐). patch notes는 push 뒤 따로 쓴다. 후속 P2 3건(재개 중 CSO 비consult task의 busy 검사, facilities 재route, `normalized_counts`가 통과할 조합 없음)은 issue로 넘긴다.
- 근거: `labhq/gateway/server.py`, `labhq/orchestrator/cso.py`, `labhq/research/packs.py`, `labhq/research/contract.py`, `labhq/research/packs/single_cell_de.yaml`, `tests/test_consult_restart.py`, `tests/test_research_protocol.py`.

## 2026-10-01 · 후속 #104 #106 #107 #108

- #104: bench CLI tree 종료 검사를 남은 PID 조건 대기(10초 상한)로 바꾸고 timeout에 PID를 표시한다.
- #106: 같은 step은 running을 hibernating보다 우선하고 같은 상태면 최신 dispatch를 고른다. task board에 HPC 대기와 실행 중 task 취소를 복원한다.
- #107: 403 reset header는 remaining=0일 때만 rate limit으로 보고 reset·Retry-After까지 기다린다. 그 밖의 403은 1회 뒤 failed다.
- #108: 예전 `checks_passed`를 legacy 결과로 보존하고 구조화 채점 세 열은 N/A로 구분한다. report에 `bench rescore --all` 안내를 넣었다.
- 테스트: 수정 전 회귀 5건 실패 확인. 전체 pytest 연속 3회 각 1131 passed/18 skipped, Node 8개, `bash scripts/check_public.sh`, diff 검사가 통과했다.
- 한계: 실제 GitHub rate limit 응답은 호출하지 않았고 MockTransport로 header·대기·재시도를 검증했다. 기존 FastAPI deprecation warning 196건은 남아 있다.

## 2026-10-01 · #90 연구 수행 규약 PR 1 — 2회차 리뷰

- 결론: active pack의 acceptance를 machine rule로 판정한다. 모든 rule을 통과해야 PLAN을 동결하고 CP1을 열며, correction 계획 뒤 budget 거절도 즉시 중단한다. pilot은 여전히 CP1 뒤 멈춘다.
- 변경: pack `rules`는 `when`과 `require|forbid`, predicate의 `value|in|not_in`을 쓴다. loader가 모르는 field·연산자와 충돌하는 rule을 거부한다. acceptance 문자열은 설명으로만 남긴다. `single_cell_de@1`은 완전 혼동 시 비교·설명 가설, inferential statistics, condition-effect estimand을 금지한다.
- 검증: 수정 전 confounded PLAN·잘못된 rule 문법·correction 뒤 budget 거절 회귀가 실패함을 확인했다. 수정 뒤 연구 규약 14 passed, 전체 pytest 1108 passed/18 skipped, `tests/*.cjs` 5개와 `bash scripts/check_public.sh`가 통과했다. `PYTHONPATH`는 clone 루트, state·basetemp는 저장소 밖 임시 폴더를 썼다.
- 한계·인계: claim/evidence 원장·artifact manifest·감사·후행 무효화·CP2–4·연구 E2E 실행은 후속 PR 범위다. 실제 직원 CLI·HPC·live 연구 case는 실행하지 않았다. patch notes는 수정하지 않았다.
- 근거: `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/research/packs/single_cell_de.yaml`, `labhq/orchestrator/cso.py`, `tests/test_research_protocol.py`, `docs/research_protocol.md`.

## 2026-10-01 · bench 구조화 채점·baseline 설정 #92 #101

- 결론: 모든 arm이 같은 case별 JSON 결과 블록을 내고, 문장 표현 대신 그 값으로 채점한다. 형식 실패와 값 오답은 따로 기록·집계한다.
- 바뀐 것: 다섯 case에 key·type·기대값 규칙을 넣고 공통 prompt·mock 답을 갱신했다. 기존 문장 검사는 근거·한계의 보조 판정으로 남겼다. Claude·Codex baseline은 `extra_args`를 일반 adapter와 같은 위치에 넣는다. README에 사용자 동작을 반영했다.
- 실행한 것: 수정 전 회귀 4건 실패. 수정 후 전체 pytest 1099 passed/18 skipped, Node `tests/*.cjs` 5개, `scripts/check_public.sh` 통과. 임시·상태 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: 실제 유료 CLI 실행은 하지 않았다. 구조화 블록 도입 전 저장된 답은 재채점하면 형식 실패이며, 기존 15개 fixture는 보조 문장 검사 근거로만 남겼다.
- 근거: `labhq/bench.py`, `labhq/bench_data/cases/`, `tests/test_bench_structured.py`, `tests/test_bench_rescore.py`.

## 2026-10-01 · 러너 env·POSIX 종료 #83 · 라운드 게시 복구 #97

- 결론: 부모 Claude session marker만 지우고 운영자가 지정한 engine·task env는 보존한다. POSIX timeout은 leader가 먼저 끝나도 남은 process group을 SIGKILL한다. 라운드 기록은 GitHub의 rate-limit 대기 시각을 지키며, 토큰 누락은 재시작 때 다시 게시할 pending 상태로 둔다.
- 바뀐 것: 공용 env 병합 순서를 일반 직원 실행과 MCP 점검에 적용했다. `Retry-After`·`X-RateLimit-Reset`을 보존해 backoff의 하한으로 쓰고 rate limit은 일반 재시도 상한에서 뺐다. README의 engine env·라운드 기록 동작도 맞췄다.
- 실행한 것: 수정 전 #83 env와 #97 rate-limit·token·reset 회귀 4건 실패를 확인했다. 수정 후 관련 94 passed/10 skipped, 전체 pytest 1097 passed/19 skipped, Node `tests/*.cjs` 5개, `bash scripts/check_public.sh`, `git diff --check` 통과. pytest 임시·상태 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: POSIX 자손 종료 회귀는 Windows에서 skip되어 Linux CI 확인이 남았다. 실제 GitHub API와 토큰 교체는 fake API·재시작 상태로 대신했다. 범위 제한에 따라 저장소 밖 HARVEST·LEAD_NOTES와 patch notes는 수정하지 않았고 `.git` 읽기 전용이라 커밋하지 않았다.
- 근거: `labhq/adapters/base.py`, `labhq/util.py`, `labhq/tools/_mcpcompat.py`, `labhq/integrations/github.py`, `labhq/integrations/rounds.py`, `tests/test_isolation.py`, `tests/test_runner_safety.py`, `tests/test_round_records.py`, `tests/test_github_reporter.py`.

## 2026-10-01 · 웹 후속 #75 #87 #88

- 결론: 폰의 고정 직원 줄을 기본으로 접고 결정 이력을 최근 10건부터 요청별로 묶었다. 재접속 snapshot은 200개 이전의 활성 task도 실행 중으로 복원하며, 직원 카드와 3D 승인 시계가 실제 상태를 계속 보여 준다.
- 바뀐 것: **직원 보기** 토글, 결정 이력 **더 보기**, snapshot `running_tasks`, 완료·오류·순서 대기 문구와 오류 색, 3D 1초 갱신을 넣었다. main에 이미 있던 #87 REST 요청 요약의 활성 task 반영과 #88 2.5D 15초 갱신은 중복하지 않았다. README는 정규화 UTF-8 41,583→41,781 bytes(+198)다.
- 실행한 것: issue별 수정 전 Node 회귀 실패 확인. 수정 후 `node tests/*.cjs` 7개, 전체 pytest 1094 passed/18 skipped, `bash scripts/check_public.sh`, diff 검사가 통과했다. 375×812 headless Chrome에서 직원 줄이 접히고 사무실 첫 두 줄이 보였다.
- 미해결: 실제 iPhone·Android의 touch와 Safari 동작은 확인하지 않았다. FastAPI `on_event` deprecation warning 192건은 기존 경고다.
- 근거: `tests/web_issue75.cjs`, `tests/web_issue87.cjs`, `tests/web_command_center.cjs`, `tests/web_live.cjs`, `tests/test_ops_status_usage.py`.

## 2026-10-01 · PR #77 Sonnet 보정·Claude arm 권한 #40

- 판정: Sonnet 5개는 KRAS·GEO·Protein·Penguins 맞음, Plastome 부분/PASS다. 전체 fixture 15개는 맞음 13·부분 2이며 판정-검사 15/15 일치한다. 근거 줄·출처는 `bench/calibration.md`; Sonnet 원본 bytes/SHA-256 5/5 일치다.
- 변경: 표 헤더·단위·행 항목, species 종류 수·n=·EMT 이름·전수 양수 표현을 처리한다. accession 뒤 연도·단계 번호와 원본 전체 설명은 필수 집계와 구분한다. Claude는 acceptEdits·arm 경로·전용 hook으로 안쪽 파일 쓰기/단순 명령을 허용하고 밖 쓰기를 거부한다. 공통 prompt는 answer.md 저장을 요청하며 저장한 보고서를 채팅 요약으로 덮어쓰지 않는다.
- 검증: 전체 pytest 783 passed/18 skipped(TEMP 임시·상태 폴더), fixture 변조·누락·모순 회귀, dry-run argv/실제 hook 안·밖 판정/cwd 검사 통과. `bash scripts/check_public.sh`·신규 파일 공개 검사·diff 검사 통과. 새 의존성은 없다.
- 한계·인계: Native Windows의 Claude OS sandbox 부재로 임의 script/interpreter는 거부한다. Codex workspace-write와 그 실행 권한까지 완전히 같지는 않다(`docs/reference/bench-permissions.md`). 실제 CLI 재실행·결과 폴더 쓰기·patch notes·저장소 밖 노트 수정은 하지 않았다. `.git` read-only라 이번 변경만 `.pr-drafts/commits.json`, 같은 PR 보고는 `.pr-drafts/sonnet-pr.md`에 계획한다.

## 2026-10-01 · PR #77 3회차 P1·bench 예산 정책 #40

- 변경: bench 자료·검사기를 `labhq/bench_data/` package-data로 옮기고 `importlib.resources`로 찾는다. labhq와 baseline에 같은 case/runner timeout을 적용한다.
- 예산: 기본 거절을 유지한다. `--approve-budget-up-to N`은 labhq의 요청 총예산이 원래 case 예산 N배 이내일 때만 승인한다. 표에 승인 횟수·최종 비용/예산 배수를 기록하며 원래 예산 초과는 FAIL이다.
- 검증: 전체 pytest 705 passed/17 skipped(TEMP 임시·상태 폴더). wheel non-editable 설치 후 case 5개·참고 자료·검사기 실행, `bash scripts/check_public.sh`·diff 검사 통과.
- 인계: `.git`은 read-only라 이번 변경만 `.pr-drafts/commits.json`에 계획한다. patch notes·저장소 밖 로컬 노트는 수정하지 않는다. 실행 중인 병렬 단계의 비용까지 제한하는 정책은 아니다.

## 2026-10-01 · PR #77 실제 답 10개로 bench 검사 보정 #40

- 결론: 직접 판정은 맞음 9·부분 1·틀림 0. 부분은 KRAS sol의 추가 동일성 주장에 근거 범위 문제가 있지만 필수 ID·assay 한계를 충족해 PASS로 정했다. 판정과 근거 줄은 `bench/calibration.md`에 있다.
- 바뀐 것: 공개 답 10개를 fixture로 보존했다. 전체값과 하위 집계·다른 열·pair당 값을 구분하고, Markdown 수치·결측 개수/분모/비율·bp/kb 설계 범위를 처리한다. 같은 전체 문맥의 오답·모순은 계속 거부한다.
- 검증: 보정·기존 변형 테스트 111 passed, 전체 pytest 680 passed/17 skipped, mock 5/5 PASS, 원본-fixture SHA-256 10/10 일치, 공개 검사 통과. pytest 임시·상태 폴더는 저장소 밖 TEMP를 사용했다.
- 인계: `.git` 읽기 전용으로 이번 변경만 `.pr-drafts/commits.json`에 계획했다. 같은 보고를 `.pr-drafts/calibration-pr.md`에 남겼다. patch notes와 저장소 밖 로컬 노트·실제 run은 수정하지 않았다. 실제 run 재채점은 후속 작업이다.

## 2026-10-01 · PR #77 bench 검사·재채점 #40

- 결론: 표현 차이로 생긴 FAIL을 줄이고 저장된 산출물을 현재 검사로 다시 채점한다.
- 바뀐 것: `re:`·숫자 추출 비교, 다섯 case의 한국어·영어·단위·띄어쓰기 검사, `bench rescore <case> [--run-id ID | --all]`(기본 최신 run). 이전 판정은 `score_history`에 남기고 실행별·case별 comparison을 갱신한다. 실행 기록과 비용·모델은 보존한다.
- 실행한 것: 새 테스트 44개 포함 전체 pytest 613 passed/17 skipped, case마다 정답 변형 2개·숫자 오류·항목 누락 검사, mock test-agent 5/5와 저장된 mock 재채점, 공개 검사·diff 검사 통과.
- 인계: `.git` 읽기 전용이라 이번 변경만 `.pr-drafts/commits.json`에 남겼다. PR 보고 초안은 `.pr-drafts/rescore-pr.md`. patch notes는 요청대로 수정하지 않았다.
- 근거: `labhq/bench_data/checks/contains_terms.py`, `labhq/bench_data/cases/`, `labhq/bench.py`, `tests/test_bench_rescore.py`.

## 2026-10-01 · #40 비교 bench와 테스트 에이전트

- 결론: PI 결정(2026-10-01, #40)에 따라 기본 arm은 `labhq`, `sonnet-max`(sonnet/max), `sol-ultra`(gpt-5.6-sol/ultra), `astra-ultra`(gpt-6-astra/ultra)다. LabHQ의 Claude 직원은 `opus=sonnet`으로 치환하고 Codex 직원은 설정을 유지한다.
- 바뀐 것: `bench.arms`의 모델·effort 설정, `bench.staff_model`과 `--staff-model`, run·test-agent의 `--arms`, arm별 결과 누적과 `bench report`. 결과표는 실행 당시 모델·effort를 표시하며 real/mock을 따로 모은다. Opus는 설정으로 추가한다. README와 설정 예도 갱신했다.
- 실행한 것: arm 선택·누적 report·모델 치환 회귀 테스트와 전체 `pytest -q -p no:cacheprovider` 556 passed/17 skipped, 네 arm의 `--dry-run` 명령 확인, `bash scripts/check_public.sh` 통과. 임시·상태 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: 실제 유료 CLI는 실행하지 않았다. 네 arm의 비용·token·과학 결과는 PI 머신에서 측정한다. Codex 비용은 `미집계`로 남긴다. `.git` 쓰기 제한으로 이번 변경의 커밋 계획만 `.pr-drafts/commits.json`에 남겼다.
- 근거: `labhq/bench.py`, `labhq/settings.py`, `labhq/cli.py`, `config/labhq.example.yaml`, `tests/test_bench.py`.

## 2026-10-01 · 막힌 단계 같은 세션 재개 #81 · 예외 종료 비용 #67

- 결론: PI 답을 받은 막힌 단계는 같은 session·workdir로 이어 간다(수정 자체는 #76). 예외로 끝난 요청도 최종 비용을 화면에 보낸다.
- 바뀐 것: `request.failed` 예외 경로 payload에 `cost_usd`·`cost_known`(#67). #81은 mock 직원 blocking_decision → PI 답 → 같은 session_id·workdir 재개, resume 없는 엔진은 같은 workdir 새 세션을 E2E로 고정했다.
- 실행한 것: 전체 `pytest -q -p no:cacheprovider` 804 passed/17 skipped(main 병합 뒤), `node tests/*.cjs`, `bash scripts/check_public.sh` 통과.
- 미해결: 실제 직원 CLI로 재개하는 것은 확인하지 않았다.
- 근거: `tests/test_e2e_mock.py`, `tests/test_cso.py`, `tests/web_state.cjs`.

## 2026-10-01 · 후속 P2 네 건 #51 #53 #54 #68

- 결론: 승인 timeout 기록, 자체 변경이 있는 merge의 패치노트 검사, 라운드 게시 재시도, IPv6 link-local 탐색과 doctor 오타 보고를 고쳤다.
- 바뀐 것: 명확화·예산 승인이 시간 초과되면 `timed_out` 결정으로 저장해 라운드 기록 `pi_decisions`에 보인다(#51). `scripts/patch_notes.py`가 git 자동 병합 결과와 달라진 파일이 있는 merge만 검사하고, STATUS.md·패치노트만 정리한 merge와 깨끗한 동기화 merge는 뺀다(#53). `RoundRecorder`가 일시 실패한 GitHub 게시를 상한 있는 backoff로 다시 넣고 영구 거부는 재시도하지 않는다(#54). global IPv6 route가 없으면 interface 열거로 link-local 주소를 찾고, `force_engine` 오타는 doctor fail 행으로 보고한다(#68).
- 실행한 것: issue마다 수정 전 실패하는 회귀 테스트, 전체 `pytest -q -p no:cacheprovider` 807 passed/17 skipped(main 병합 뒤), `bash scripts/check_public.sh` 통과.
- 미해결: GitHub `Retry-After`·rate-limit reset 시각을 backoff에 반영하는 것은 #97로 넘긴다.
- 근거: `tests/test_approval_timeout.py`, `tests/test_patch_notes.py`, `tests/test_round_records.py`, `tests/test_ipv6_interfaces.py`, `tests/test_doctor.py`.

## 2026-10-01 · #39 labhq_ask 질의 경로

- 결론: 직원은 `labhq_ask`로 CSO·시설팀·동료에게 묻고, 코드가 판정한 hard stop만 PI에게 올린다. 답이 늦으면 step을 hibernate하고 같은 session·workdir에서 재개한다.
- 바뀐 것: 5/20/15분 대기, task/대상/요청 3/2/12회 상한, 시설팀→CSO fallback, 동료 read-only consult, 메신저 질문·답변 이벤트, task별 capability token과 다른 task 사칭 403을 넣었다. #38의 묻고 멈추는 게이트도 이 경로를 쓴다.
- 실행한 것: 수정 전 새 테스트가 `AskRequest` import에서 실패함을 확인했다. 수정 후 대상 10 passed, 전체 504 passed/17 skipped, 공개 검사를 통과했다. 실제 직원 CLI는 실행하지 않았다.
- 미해결: 실제 PI 폰·직원 CLI 연동은 mock engine과 fake MCP client로 대신했다. commit 뒤 `patch_notes/README.md` 행을 만들고 amend해야 한다.
- 근거: `labhq/tools/ask_mcp.py`, `labhq/orchestrator/cso.py`, `labhq/runner/approvals.py`, `tests/test_ask.py`, `tests/test_e2e_mock.py`.

## 2026-10-01 · HPC 도구 실패 표시·제출 후 재시도 차단 #80

- 결론: HPC 운영 실패를 MCP 오류로 전달하고, 알려진 pending job이 있는 실패 시도는 자동으로 재실행하지 않는다.
- 바뀐 것: compat `ToolError`, submit·cancel·status·queue 오류 상세, qstat 반환 코드 검사, qacct·PBS 조회 실패와 job 부재 구분, Codex `isError`·`is_error` 처리. PI 거절은 정상 결과에 재제출 금지 안내를 붙인다.
- 실행한 것: 수정 전 회귀 18건 실패(두 번 실행), job 부재 2건 통과. 수정 후 대상 127 passed/9 skipped, 전체 `pytest -q -p no:cacheprovider` 540 passed/17 skipped, `bash scripts/check_public.sh`, `git diff --check` 통과. `PYTHONPATH`는 clone 루트, state·basetemp는 저장소 밖 임시 폴더를 썼다.
- 미해결: PI 거절을 오류로 올릴지와 제출 후 실패의 재개 정책은 보류한다. job 추적 등록 실패·러너 단절로 pending job을 모르는 경우의 중복 방지는 범위 밖이다. 실제 클러스터·직원 CLI·MCP 1.x는 검증하지 않았다. `.git` 읽기 전용이라 커밋·PR 발행 대신 초안을 남겼다. patch_notes와 작업 범위 밖 로컬 인계·수확 파일은 수정하지 않았다.
- 근거: `tests/test_hpc_failures.py`, `tests/fixtures/hpc_failure_server.py`, `tests/test_cso.py::test_failed_attempt_with_submitted_job_does_not_resubmit`.

## 2026-10-01 · 연결된 도구 목록·배지 #63

- 결론: README §2의 연결된 도구 표와 shields.io 배지를 정규직 YAML에서 생성한다.
- 바뀐 것: `scripts/integrations.py --write/--check`, 종류별 항목 수 배지, 직원·공개 출처 링크. 내장 MCP 2·외부 MCP 2·plugin 1·skill 2·엔진 기능 3. README 33,603→37,441 bytes(+3,838).
- 실행한 것: 전체 `pytest -q -p no:cacheprovider` 516 passed/17 skipped, README `--check`, `bash scripts/check_public.sh`, `git diff --check` 통과. 테스트는 `PYTHONPATH`를 clone 루트로, `LABHQ_STATE_DIR`를 임시 state로, `--basetemp`를 저장소 밖 임시 폴더로 지정했다.
- 미해결: 실제 설치·인증·MCP 접속은 검증하지 않았다. 파견직 예시·PI 개인 커넥터·이 clone에 없는 labhq_ask는 제외했다. `.git` 읽기 전용이라 커밋·PR 발행 대신 초안을 남겼으며 patch_notes는 수정하지 않았다. 작업 범위 밖 로컬 인계·수확 파일은 갱신하지 않았다.
- 근거: `agents/core/*.yaml`, `labhq/runner/daemon.py`, `labhq/recruit/paper2agent.py`, `tests/test_integrations.py`.

## 2026-10-01 · HANDOFF 개편: 개발 총괄 교대

- 결론: 개발 총괄을 Claude와 Codex가 번갈아 맡을 수 있게 HANDOFF.md를 지금 기준으로 다시 썼다(PI 결정 2026-10-01).
- 바뀐 것: 시작 순서, 규칙에 더해 겪어서 안 작업 방식, 작업 큐, 최근 PI 결정, 결정 대기, 구조 표. CLAUDE.md·AGENTS.md의 ⛔ 체크포인트 문구를 작업 큐·결정 대기로 바꿨다. 105줄·10,663바이트 → 93줄·8,053바이트.
- 실행한 것: 공개 검사 통과. 문서만 바꿨다.
- 미해결: 로컬 경로·진행 중 작업은 저장소 밖 노트에 있어 공개 저장소만으로는 이어받을 수 없다(의도).

## 2026-10-01 · #57 웹 Command Center 1–5

- 결론: Munder Difflin의 배치만 옮겨 2.5D 오른쪽 Command Center·폰 하단 탭·직원 카드 줄을 만들고, 결정 초안 보존·작업판·결정 이력을 구현했다. 6–8은 범위 밖으로 남겼다.
- 바뀐 것: keyed 결정 카드와 공통 3D 입력, roster 전원 카드와 턴/시간 게이지, step별 시도·산출물·누락·리뷰·취소, 인증된 `GET /api/approvals/history`, allowlist를 쓰는 `/ui/{path}`.
- 실행한 것: 수정 전 regression 4건 실패 확인, 전체 `pytest -q -p no:cacheprovider` 499 passed/17 skipped, `node tests/web_state.cjs`, `bash scripts/check_public.sh` 통과.
- 미해결: 직원별 로그·capability card·사무실 소품 연결(6–8), 픽셀 테마는 하지 않았다. 실제 기기 화면과 스크린샷은 완료 판정에서 제외했다.
- 근거: `labhq/web/ui/`, `labhq/web/state.js`, `labhq/gateway/server.py`, `tests/web_command_center.cjs`, `tests/test_web.py`.

## 2026-10-01 · Codex 직원·개발 라운드 기록 후속 #56 #69

- 결론: Codex 직원의 쓰기·웹 권한을 명시하고 문헌 원 출처 확인을 강화했다. 개발 라운드는 설정한 labhq commit 링크와 종료 조건을 남긴다.
- 바뀐 것: sandbox 기반 roster, 완료된 웹 검색 query parser와 실측 fixture, 직원별 모델·웹·PubMed·bioRxiv 설정, 전용 `CODEX_HOME` 안내, `dev_log.source_repo` 링크와 doctor·HANDOFF 운영 안내.
- 실행한 것: 수정 전 regression 실패 확인. 수정 후 전체 `pytest -q -p no:cacheprovider` 500 passed/17 skipped, `bash scripts/check_public.sh` 통과. mock E2E 이벤트 수집이 중간에 1회 실패했으나 단독·전체 재실행은 통과했다. README 33,738→33,576 bytes(-162).
- 미해결: 실제 Codex 로그인·hosted MCP·GitHub 발행은 실행하지 않았다.
- 근거: `tests/test_real_streams.py`, `tests/test_registry.py`, `tests/test_round_records.py`, `tests/test_doctor.py`.

## 2026-10-01 · #55 러너 안전 경계

- 결론: Windows timeout·취소가 CLI 프로세스 트리를 끝내고, 직원 subprocess는 부모 Claude 세션 마커를 받지 않는다. labhq MCP timeout과 Claude 교차 세션 tool 차단, 원자적 기록, resolved model provenance도 함께 적용했다.
- 바뀐 것: Windows `CREATE_NEW_PROCESS_GROUP`·`taskkill /T /F`와 제한 환경 fallback, Claude env allowlist·doctor 안내, Codex/Claude MCP timeout, `SendMessage`·`ListAgents` deny, registry·계약 YAML·manifest `os.replace`, CLI가 보고한 `model_id`, Windows Python 3.12 CI.
- 실행한 것: 수정 전 회귀 10건 실패를 확인했다. 수정 후 대상 60건과 Windows 손자 프로세스 종료 검사, 전체 `pytest -q -p no:cacheprovider` 501 passed/17 skipped, 공개 검사가 통과했다.
- 미해결: #55의 6번 broker task token은 D 묶음 범위라 건드리지 않았다. 확인되지 않은 `crossSessionInbound` 설정도 넣지 않았다. 실제 Claude/Codex에서 장시간 MCP 승인을 기다리는 probe는 하지 않았다.
- 근거: `labhq/adapters/base.py`, `labhq/adapters/claude_code.py`, `labhq/adapters/codex.py`, `labhq/runner/workspace.py`, `tests/test_runner_safety.py`.

## 2026-10-01 · 후속 #24 #25 #46 #47 가드·doctor

- 결론: plugin hash에 작업 트리의 실행 mode 반영(#24), hostname에 없는 IPv6 LAN 주소 탐색(#25), doctor가 러너의 data·계정 guard와 직원 adapter preflight를 그대로 재사용(#46), percent-encoded token 파라미터 이름도 로그에서 가림(#47).
- 바뀐 것: `labhq/adapters/claude_code.py`, `labhq/cli.py`, `labhq/doctor.py`, `labhq/runner/daemon.py`, `labhq/security.py`와 각 regression test.
- 실행한 것: 수정 전 regression 20건 실패 확인, 수정 후 전체 `pytest -q` 통과, 공개 검사 통과. 작업은 Codex(gpt-6.1-sol)가 했고 커밋·rebase·PR은 Claude가 만들었다.
- 미해결: 실제 HPC·CLI 로그인·LAN 연결은 검증하지 않았다(Windows에서 POSIX host API는 mock).

## 2026-10-01 · 후속 #30–#33 러너·운영

- 결론: npm shim 옆 Node 우선(#30), custom CLI token 보존(#31), 종료 비용으로 웹 합계 동기화(#32), HPC 기상 중 끝난 task를 hibernating 집계에서 제외(#33).
- 바뀐 것: `labhq/adapters/base.py`, `labhq/adapters/cli.py`, `labhq/web/state.js`, `labhq/gateway/server.py`와 각 regression test.
- 실행한 것: 수정 전 새 테스트 9 failed(네 issue 재현), 수정 후 전체 `pytest -q` 통과, 공개 검사 통과. 작업은 Codex(gpt-6.1-sol)가 했고 커밋·PR은 Claude가 만들었다.
- 미해결: 2.5D·3D 화면 확인은 reducer 테스트로 대신했다. 실제 CLI·HPC 실행은 범위 밖.

## 2026-10-01 · #43 라운드 기록

- 결론: 요청이 끝나거나 재시작으로 중단되면 `gateway.state_dir/rounds`에 Markdown·JSON 기록을 남기고, 설정한 private 저장소에는 요청마다 이슈 1건을 갱신한다.
- 바뀐 것: schema v1 기록, 게시 전마다 저장소 공개 여부 확인, 본문이 같으면 재게시 생략. 실행 환경 snapshot(labhq 버전, git commit, 러너 CLI 버전)은 요청을 만들 때 한 번 저장해 복구 때 덮어쓰지 않는다. 러너가 시작할 때 CLI `--version`을 capability로 보내고, 작업 결과에 manifest 요약(실행 시각, 모델, 턴, CLI 버전, plugin)을 실어 gateway가 러너 디스크를 못 읽어도 기록이 비지 않는다. 상위 저장소 안의 plugin도 하위 파일 전체(hook이 부르는 scripts 포함)를 hash한다.
- 실행한 것: Windows `pytest -q` 456 passed/17 skipped(임시 폴더를 저장소 안에 둔 경우도 통과), `scripts/check_public.sh` 통과, Codex 리뷰 5회.
- 미해결: 실제 GitHub 발행은 mock 전송만 검증했다. 기록 저장소(private) 생성은 PI 결정. timeout된 PI 승인을 결정으로 남기는 일은 후속 issue.
- 근거: `labhq/integrations/rounds.py`, `labhq/runner/versions.py`, `labhq/runner/workspace.py`, `tests/test_round_records.py`.

## 2026-10-01 · 패치노트와 README 갱신 규칙

- 결론: 커밋마다 `patch_notes/README.md`에 한 줄, main 커밋 3개 안에 README 갱신을 CI(`patch-notes` job)가 확인한다. 형식은 PI의 튜토리얼 저장소 패치노트와 같다.
- 바뀐 것: `scripts/patch_notes.py`(check·rows), `.github/workflows/test.yml`, 지난 커밋 96개 소급 기록, README 배지·동작 화면(2.5D·3D), 에이전트 규칙(CLAUDE.md·AGENTS.md).
- 실행한 것: `tests/test_patch_notes.py` 3개(임시 git 저장소로 규칙 확인), Windows `pytest -q`, 공개 검사.
- 미해결: 스크린샷은 mock demo 화면이다. UI 개편 뒤 다시 찍는다.

## 2026-10-01 · 문헌 담당 gpt-6-luna 전환

- 결론: `lit_scout`가 Antigravity/Gemini 대신 Codex `gpt-6-luna`로 돌고, Codex 자체 웹 검색을 쓴다.
- 바뀐 것: `agents/core/lit_scout.yaml`, Codex 어댑터(직원 `tools`에 `WebSearch`·`WebFetch`가 있으면 `-c web_search="live"`), 정적 demo roster, README 조직도.
- 실행한 것: Windows `pytest -q` 443 passed/17 skipped, 공개 검사 통과, codex-cli 0.159.2 실제 실행에서 격리 옵션과 함께 `web_search` 이벤트 확인.
- 미해결: Antigravity의 과학 DB skill(dbSNP·ClinVar 등)은 Codex에 없다. 문헌·DB MCP 연결은 #41에서 다룬다.

## 2026-09-28 · #37 #38 #35 보안·재시도

- 토큰 비교를 바이트 constant-time으로 통일하고 gateway 로그의 token 값을 가린다.
- PowerShell 위험 명령과 제한 구역 접근, Bash·PowerShell의 명백한 허용 루트 밖 쓰기를 승인 요청으로 돌린다. 셸 문자열 검사이며 샌드박스는 아니다.
- agy 모델 카탈로그의 관찰된 일시 오류와 재시도 신호를 transient로 분류한다. 인증·권한·정책 오류는 terminal이다.
- 검증: Windows 전체 pytest 410 passed/17 skipped; `bash scripts/check_public.sh` 통과. 실제 CLI·네트워크 장애 재현은 포함하지 않았다.

## 2026-09-28 · #35 doctor 사전 점검

- 결론: `labhq doctor`가 실행 전 기능과 누락 사항을 표로 보여 준다.
- 바뀐 것: 설정·엔진·직원·플러그인·계산 도구 점검, 선택적 네트워크 검사와 상태 디렉터리 manifest.
- 실행한 것: Windows `pytest -q` 375 passed/17 skipped, 공개 검사 통과, 실제 doctor 표·manifest 확인.
- 미해결: 로그인·원격 HPC·스킬 가시성은 비대화형 확인 범위까지만 판정한다.

## 2026-09-28 · #27 결과 보존·연속성

- 결론: 수정 실패 때 첫 성공 결과를 살리고, 단계 산출물과 실패 원인을 최종 보고에 남긴다.
- 바뀐 것: 재시도·수정 작업 공간 재사용, 의존 단계의 산출물 접근, 최대 턴 종료 후 부분 결과 저장, 누락 산출물 판정, CSO 세션 재개. 리뷰는 매번 새 세션이다.
- 실행한 것: Windows `pytest -q` 377 passed/17 skipped, 공개 검사 통과, 터미널 `labhq demo` 완료.
- 미해결: 실제 Claude CLI·HPC 운영 검증과 원본 HARVEST 반영은 남았다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/runner/`, `tests/test_cso.py`, `tests/test_e2e_mock.py`.

## 2026-09-28 · #27 계획·배정 결함 1·2·3·8·9·15

- 결론: 실행 환경과 직원 권한을 반영해 계획하고, PI 답변이 필요한 지점에서 배정을 멈춘다.
- 바꾼 것: 러너 기능·roster 표시, 질문 뒤 1회 재계획, 차단 단계 답변 뒤 재실행, 중복 리뷰 단계 제거, 의존성 검사, 어댑터별 재개 판정.
- 검증: Windows `pytest -q` 364 passed/17 skipped, `bash scripts/check_public.sh` 통과. 임시 파일과 사용자 홈은 작업 폴더 밖의 쓰기 가능 영역으로 지정했다.
- 미해결: 실제 CLI·HPC 연결 검증은 이 mock 테스트에 포함되지 않는다.

## 2026-09-28 · #26 운영 상태·사용량

- 결론: 실행 중인 요청·단계·승인을 확인하고 비용 미집계를 구분한다.
- 바뀐 것: 상태 API/CLI, UTF-8 JSON, 엔진별 token 집계·manifest, 종료 경고, 웹 비용 표시.
- 실행한 것: 대상 96 passed, 전체 335 passed/19 skipped/11 failed(기준 커밋도 같은 11 failed), 공개 검사·Node 상태 검사·Chrome 폰 화면 확인.
- 미해결: 이 환경의 기존 Windows/sandbox 실패 11건. 실제 엔진 과금·중단 시그널의 수동 운영 확인은 남았다.
- 근거: `tests/test_ops_status_usage.py`, `tests/test_real_streams.py`, `labhq/gateway/server.py`.

## 2026-09-28 · Windows 실행·demo 격리 #26

- 한 일: demo의 gateway·runner 상태와 작업·인재·직원 경로를 임시 폴더로 격리했다. Windows npm shim은 Node.js 스크립트로 풀어 실행하고, 풀 수 없는 batch는 실행 전에 거부한다. `prefix_args`와 Windows 설정 안내를 추가했다.
- 테스트: Windows `pytest -q` 342 passed/6 failed/17 skipped (원본 332 passed/10 failed/17 skipped; shebang 4건 해결, 새 실패 0). `bash scripts/check_public.sh` 통과.
- 남은 점: 기존 plugin provenance 3건과 러너 상태 DB 3건 실패. 실제 직원 CLI 실행은 하지 않았다.

## 2026-09-28 · 후속 P2 #19·#21·#22

- 무엇을: `labhq demo --host ::`가 IPv6 주소로 폰 URL을 출력한다(#19). plugin provenance 해시에 git index mode(실행 비트)를 넣었다(#21). `pr_gate.py --dry-run` advice가 병합이 막혀도 현재 head의 P2 목록을 담는다(#22). HANDOFF에 P5 완료와 P3 러너 계정 결정 대기를 적었다.
- 테스트: Windows `pytest -q` 332 passed → 338 passed, 실패는 기존 shebang 4건 그대로. 공개 검사 통과.
- 남은 점: 실제 IPv6 LAN 접속은 미검증. P2 두 건은 후속 issue로 넘겼다(작업 트리 mode, hostname에 없는 IPv6 주소).

## 2026-09-28 · PR 규칙: 자동 병합 게이트 끔

- 무엇을: PI 결정으로 `pr-gate.yml`의 자동 트리거를 없애고 수동 dry-run만 남겼다. dry-run은 상한 없이 병합 조건 충족 여부와 이유만 알린다(`advisory`). 리뷰 깊이와 병합은 Claude가 판단한다: 새 라운드는 직전 수정 확인이나 다른 부류의 결함이 있을 때만, 같은 부류의 좁은 변형은 부류를 닫는 수정 한 번 뒤 병합하고 나머지는 후속 issue.
- 테스트: `tests/test_pr_gate.py` 40 passed. 공개 검사 통과.
- 다음: 없음. 판단이 서지 않는 쟁점만 PI에게 넘긴다.

## 2026-09-28 · P5 공개 가드·Windows 인코딩

- 무엇을: 통제접근 경로를 접근 정책과 같은 후보 추출·정규화(구분자·대소문자·`.`/`..`·file URI 파싱과 percent-decode)로 판정해 해당 줄을 통째로 가리고, 글자에 붙은 구역 경로도 가린다. 네트워크 URL은 두고, `/`·`E:/` 같은 루트 구역이면 GitHub 보고를 끈다. 모든 GitHub 쓰기(제목·본문·코멘트·보고서·커밋 메시지)를 한곳에서 검사하고 branch·sha는 그대로 둔다. 파일 I/O는 UTF-8, CLI 콘솔은 기존 인코딩에서 대체 출력한다.
- 테스트: mock GitHub 전체 흐름·AST 인코딩·cp949 콘솔 검사 통과. Windows 전체 pytest는 기존 11 failed에서 9 failed로 줄었고, 남은 실패 목록은 같다.
- 다음: 병합 뒤 실제 private 테스트 저장소에서 end-to-end 재검증한다.

## 2026-09-28 · P2 bioinfo-agent plugin 연결

- 무엇을: `bioinfo-agent`에 Claude Code plugin `bioinfo`를 `--plugin-dir`로 연결했다. 이 직원만 skill을 허용하고 나머지 개인 설정 격리는 유지한다. 실행 전 plugin 경로와 manifest를 검사한다.
- 근거: 두 실제 init probe에서 skill 허용 시 `bioinfo:bioinfo-analyze`만 로드되고 `--disable-slash-commands` 시 skill은 비었다. 공개 fixture에는 해당 이름만 남겼다.
- 테스트: 대상 71 passed. 전체 Windows `pytest -q`는 기준 커밋과 동일한 7 failed/14 skipped, 260→269 passed(새 실패 0). `bash scripts/check_public.sh` 통과.
- 남은 점: Windows sandbox의 기준선 실패 7개와 PI 머신의 실제 plugin 분석 task·QC handoff 확인.

## 2026-09-28 · demo phone mode

- 한 일: `labhq demo --web --phone`이 LAN 주소의 `/3d` URL을 출력하고, client·runner token을 새로 만들고, 폰 승인을 기다린다. 기본 120초 뒤에만 자동 승인한다. 데모 publish 래퍼가 `runner_id`·`runner_seq`를 넘기지 않아 P1+ ⑤ 이후 `--web` 데모가 멈추던 문제도 고쳤다.
- 테스트: phone mode 8 passed, 전체 `pytest -q` 268 passed/14 skipped/7 failed(Windows shebang 4, 긴 임시 경로 2, 기존 공개 가드 1). `bash scripts/check_public.sh` 통과.
- 바꾼 파일: `labhq/cli.py`, `tests/test_demo_phone.py`, `README.md`, `STATUS.md`.
- 검증: 폰 모드를 실제로 띄워 승인 대기 → 자동 승인, REST로 먼저 누른 거절 반영, 토큰 없는 요청 401을 확인했다.
- 막힌 점: 전체 test 7건은 현재 Windows 환경·기존 코드에서 실패한다. iPhone Safari 실기와 Windows firewall 동작은 미검증.
- 질문: 없음.

## 2026-09-28 · 후속 issue #12·#13

- 무엇을: 공유 표시 규칙으로 3D 완료 포즈·표지를 3초 뒤 대기로 돌리고, 리뷰 요약 누락·파싱 실패·stale 상태도 8·9회에 한 번 경고한다. Fixes #12, Fixes #13.
- 테스트: Windows pytest 11 failed/256 passed/14 skipped → 11 failed/266 passed/14 skipped(새 실패 0). 대상 테스트 63 passed/1 skipped, Node 검사 2개, 공개 검사 통과.
- 막힌 점: Windows 기준선 실패 11개는 그대로다. Linux CI와 실제 브라우저는 미검증.
- 다음: Claude가 PR을 열고 Linux CI와 3D 실화면을 확인한다.

## 2026-09-28 · PR 게이트 실운영 점검

- 무엇을: select 잡의 `PYTHONPATH`를 고정하고 쓰기 권한 부족은 경고로 끝낸다. `select`·`gate` check를 판정에서 제외하되 다른 check가 없으면 보류한다.
- 테스트: Windows 기준선 8 failed/255 passed/14 skipped → 8 failed/259 passed/14 skipped(새 실패 0). 게이트 테스트 29 passed, YAML 파싱·공개 검사 통과.
- 막힌 점: 현재 `origin/main`에는 `scripts/pr_gate.py`가 없어 기본 브랜치 배포 전 select import는 여전히 실패한다.
- 다음: Claude가 기본 브랜치 배포 후 PR 이벤트로 select·gate 재실행을 확인한다.

## 2026-09-28 · PR #10 실제 봇 형식 대응

- 무엇을: 이미지형 P1/P2 배지를 읽고, 현재 head에서 작성된 지적만 `original_commit_id`로 고른다. 요약이 없거나 파싱되지 않아도 10회면 PI를 호출하며 Running은 기다린다.
- 테스트: 원본 `a7e4350`의 Windows pytest 8 failed/251 passed/14 skipped → 8 failed/255 passed/14 skipped(새 실패 0). 실제 봇 fixture를 포함한 게이트 테스트 25 passed, 공개 검사 통과.
- 막힌 점: 실제 GitHub 쓰기 동작은 push 전이라 미검증.
- 다음: Claude가 새 커밋을 push해 PR #10의 판정 결과를 확인한다.

## 2026-09-28 · PR 게이트 상한 정체 해소

- 무엇을: 10회 뒤 새 head를 아직 보지 않은 Completed 리뷰도 `needs-pi`로 보내고, 같은 head의 중복 호출을 막는다. Running은 기다린다.
- 테스트: fast-forward 기준 `bef633e`의 Windows pytest 8 failed/247 passed/14 skipped → 8 failed/251 passed/14 skipped(새 실패 0). 게이트 테스트 21 passed, 공개 검사 통과.
- 막힌 점: 실제 GitHub 이벤트는 push 전이라 미검증.
- 다음: Claude가 새 커밋을 push해 PR #10의 PI 호출 경로를 확인한다.

## 2026-09-28 · PR #10 후속 issue 보존

- 무엇을: P2 후속 issue를 모두 확보한 뒤에만 squash merge한다. 지적 ID marker와 기존 issue의 원문 링크로 재시도 중복을 막는다.
- 테스트: Windows 8 failed/219 passed/13 skipped → 8 failed/223 passed/13 skipped(새 실패 0). 게이트 테스트 17 passed, 공개 검사 통과.
- 막힌 점: 실제 GitHub API 쓰기는 push 전이라 미검증.
- 다음: Claude가 새 커밋을 push해 PR #10에서 동작을 확인한다.

## 2026-09-28 · PR 게이트 후속 수정

- 무엇을: P2만 남은 PR은 👍 없이 병합한다. 배지가 없는 지적은 P1처럼 막는다. CI 완료는 `workflow_run`으로 받고, 30분마다 열린 PR을 재판정한다.
- 테스트: Windows 8 failed/217 passed/13 skipped → 8 failed/219 passed/13 skipped(새 실패 0). 게이트 테스트 13 passed, YAML 파싱·공개 검사 통과.
- 막힌 점: 실제 GitHub 이벤트와 Linux CI는 push 전이라 미검증.
- 다음: Claude가 후속 커밋을 push해 PR에서 트리거를 확인한다.

## 2026-09-28 · PR 리뷰 게이트

- 무엇을: 현재 head의 Codex 리뷰·P1/P2·CI·병합 가능 상태를 판정한다. 합의 시 squash merge와 P2 후속 issue, 8회 경고, 10회 `needs-pi` 호출을 구현했다.
- 테스트: Windows 기준선 8 failed/206 passed/13 skipped → 8 failed/217 passed/13 skipped(새 실패 0). 새 판정 테스트 11 passed. 공개 검사 통과.
- 막힌 점: GitHub 실이벤트·Linux CI는 push 전이라 미검증.
- 다음: Claude가 브랜치를 push해 PR을 열고 Linux CI와 실제 Codex 요약 형식을 확인한다.

## 2026-09-28 · P4 1단계 — 공유 reducer와 실시간 3D
- 무엇을: `state.js`로 상태 처리를 분리하고 `/3d`에 실제 roster·포즈·요청 보드·DOM 승인/거절·since 재연결을 연결했다. 2.5D 전환과 데모 유지. 정적 경로 제한과 배포 에셋 포함.
- 테스트: Windows 기준선 11 failed/203 passed/13 skipped → 11 failed/227 passed/14 skipped(새 실패 0). Node에서 원본 43개 이벤트 상태·재연결·승인 검사, 공개 검사 통과.
- 화면: Chrome 153 headless, 390×844/DPR 2·1280×900. 실시간 승인/거절·roster 추가/삭제·재연결·2.5D 렌더 통과, 콘솔 오류·가로 넘침 0. 데모 9 calls/69,382 triangles 유지, 실제 12명 9 calls/69,538 triangles(+0.22%, 배지).
- 막힌 점: Windows 기존 실패 11개와 symlink 권한 skip. Linux CI·iPhone Safari 실기·wheel 설치 실행은 미검증. Chrome은 샌드박스 제약 때문에 테스트 전용 프로필에서 SwiftShader로 측정했다.
- 다음: Claude가 브라우저 확인 후 push·PR. 신규 production 모듈은 `state.js`와 `lab3d/src/live.js` 2개.

## 2026-09-27 · P1+ 데이터 경계
- 무엇을: 경로 비교·상대 쓰기·설정 검증을 닫고, 통제 구역이 있는 Windows 러너와 원본을 읽는 POSIX 러너의 시작을 거부한다. `hpc.submit_prefix`는 잡 제출·취소에 적용한다.
- 테스트: Windows 기준선 10 failed/60 passed → 9 failed/69 passed/1 skipped. 새 실패 0개. `scripts/check_public.sh` 통과.
- 막힌 점: POSIX 파일 권한 검사는 Windows에서 건너뜀. Linux CI 확인 필요. UNC Claude 규칙은 미실측이라 러너가 거부한다.
- 다음: Linux CI에서 POSIX 권한 테스트와 계정 분리 배치를 확인한다.
- 후속 수정: `docker -v`·`scp`·`rsync`·`file://`처럼 인자에 붙은 경로를 탐지한다. Windows 9 failed/80 passed/1 skipped(신규 실패 0), 공개 검사 통과.
- 리뷰 반영: 선행 `\` 경로를 절대경로로 판정한다. 계정 전환 제출은 `hpc.job_group`과 POSIX 그룹 소속을 확인하고 잡 스크립트·로그 권한을 맞춘다. Windows 9 failed/82 passed/3 skipped(신규 실패 0).
- 2차 리뷰 반영: execute-only 구역도 거부한다. 전환 잡은 `hpc.user`·양쪽 계정의 그룹 소속을 확인하고 `hpc_out/`에서 실행한다. Windows 9 failed/83 passed/4 skipped(신규 실패 0).
- 4차 리뷰 반영(2026-09-27): main의 CSO 변경을 병합하고, 전환 잡 입력의 other 권한과 드라이브 상대경로의 불확실한 쓰기·구역 판정을 닫았다. Windows 9 failed/112 passed/5 skipped(신규 실패 0), 공개 검사 통과.
- 5차 리뷰 반영(2026-09-27): 전환 잡 제출 때 `workspace_root`·날짜 폴더에 group traverse를 주고, 바깥 상위 경로가 막히면 명확히 거부한다. Windows 9 failed/112 passed/7 skipped(신규 실패 0), 공개 검사 통과.
- 6차 리뷰 반영(2026-09-27): Linux CI의 umask 022 테스트 setup에서 부모 폴더를 각각 0700으로 만들고, 생성 직후·거부 후 권한을 모두 확인한다. 제품 코드는 부모 mode를 가정하지 않아 변경하지 않았다. Windows 9 failed/112 passed/7 skipped(신규 실패 0), 공개 검사 통과.
- 7차 리뷰 반영(2026-09-27): 반복 제출 중 workdir의 g+x를 유지하고, 연결된 task 입력은 원본 mode를 바꾸기 전에 거부한다. 명시적 경계에서만 경로를 분리해 `/scratch/data/cohort` 오탐을 없앴다. Windows 9 failed/114 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 8차 리뷰 반영(2026-09-27): Python 3.12 `Path.chmod`의 `follow_symlinks`를 테스트 monkeypatch에서 전달하고 g+x 단언은 유지한다. 같은 형태의 `os.access` wrapper도 원본 인자를 전달한다. Windows 9 failed/114 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 9차 리뷰 반영(2026-09-27): `hpc.submit_prefix`를 `qdel`에도 적용하고, 취소 실패 때 `sudoers`의 `qdel` 권한을 안내한다. `qstat`은 러너 계정으로 조회한다. Windows 9 failed/115 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 10차 리뷰 반영(2026-09-27): 공백이 든 통제 구역을 구조화된 경로·따옴표 셸 경로에서 보존하고, 따옴표 없는 셸 입력은 원문 경계 검사로 놓침을 막는다. Windows 9 failed/116 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 11차 리뷰 반영(2026-09-27): Bash의 backslash-escaped 공백을 경로로 인식하고, `jobs`의 기존 입력을 private으로 정리하되 이전 잡 스크립트 접근을 유지한다. Windows 9 failed/116 passed/13 skipped(신규 실패 0), 공개 검사 통과.

## 2026-09-26 · P1+ 새 페이지 스냅샷 복구
- 한 일: 웹의 `lastSeq`를 페이지 메모리에만 둔다. 새로 연 페이지는 스냅샷으로 시작하고 같은 페이지의 재연결만 `since`를 보낸다. 예전 localStorage 키는 무시한다.
- 테스트: 정적 회귀 테스트 추가. Windows 10 failed·65 passed → 10 failed·66 passed, 새 실패 0. 공개 검사 통과.
- 막힌 점: 실제 브라우저 재연결은 미검증.
- 다음: Linux CI와 브라우저에서 새로 고침·재연결 확인.

## 2026-09-26 · P1+ 상태 복구·이벤트 재전송
- PR #9 10차: 불확실 task는 같은 runner 세대에만 재전송하고, 완료된 direct 요청은 agent 없이 종결하며, GitHub 계획·리뷰 등의 미게시 이벤트를 재시작 후 전달한다. Windows 10 failed·148 passed(신규 실패 0), 공개 검사 통과.
- PR #9 9차: runner 세대가 바뀐 수락 task는 수동 복구로 종결하고, 같은 세대의 runner 재시작 뒤 추적 중인 HPC job은 재제출 없이 wake로 잇는다. Windows 10 failed·138 passed(신규 실패 0), 공개 검사 통과.
- PR #9 8차: 수락 task의 완료 대기에서 연결 timeout을 빼고, 재시작 후 기존 GitHub issue를 찾아 중복 생성을 막으며 terminal 이벤트를 다음 seq보다 먼저 보낸다. Windows 10 failed·135 passed(신규 실패 0), 공개 검사 통과.
- PR #9 7차: 제어 단계 task와 리뷰 수정 횟수를 재시작 후 이어받고, GitHub 최종 보고·코멘트·닫기 작업을 중복 실행하지 않는다. Windows 10 failed·132 passed(신규 실패 0), 공개 검사 통과.
- PR #9 6차: runner 재시작 task를 실패 결과로 종결하고 불확실한 전송은 같은 ID로 재전송한다. 누락된 GitHub issue를 최종 보고 전에 복구하며 replay-gap 스냅샷은 웹 상태를 교체한다. Windows 10 failed·119 passed(신규 실패 0), 공개 검사·Chrome headless 통과.
- PR #9 5차: HPC 완료 알림은 단계 결과 채택까지 보존하고, 종료 상태·이벤트·GitHub 전달 대기를 한 트랜잭션에 저장해 재시작 때 전달한다. Windows 10 failed·115 passed(신규 실패 0), 공개 검사 통과.
- PR #9 4차: `task.result`는 attempt·revision 이력으로만 저장하고 DAG가 채택한 결과만 완료 처리한다. revision 피드백을 복구하며 runner 수신 ACK와 같은 task ID 재전송·중복 실행 방지를 추가했다. Windows 신규 실패 0, 공개 검사 통과.
- PR #9 3차: CSO main을 병합해 재개도 skip·재시도·리뷰 판정을 쓰게 하고, 늦게 온 task 결과·HPC 완료 복구, 중요 outbox 이벤트 보존, 완료 단계 runner 대기 제외, 교체 소켓 격리, 재개 거절 실패 이벤트를 고쳤다. Windows 신규 실패 0, 공개 검사 통과.
- PR #9 리뷰 반영: 재개 후 과학 리뷰·수정, runner 세대별 seq, 단계 비용, runner 대기, 복원 불가 승인 만료, GitHub issue 번호 복원을 추가. Windows 10 failed·66 passed → 10 failed·71 passed(새 실패 0), 공개 검사 통과.
- 한 일: 게이트웨이 요청·승인·이벤트와 러너 HPC 잡·outbox를 각 SQLite WAL에 저장. 재시작 요청은 `interrupted`로 두고 PI 승인 뒤 남은 DAG 단계만 실행. 이벤트 `schema_version`·전역 `seq`, WS/REST `since`와 보관 범위 초과 스냅샷을 추가.
- 테스트: Windows 기준선 10 failed·60 passed → 10 failed·65 passed, 새 실패 0. UTF-8 mock 통합 흐름 2개 통과. 공개 검사 통과.
- 막힌 점: Windows 기존 실패 10개는 그대로. 헤드리스 브라우저 재연결은 미검증.
- 다음: Linux CI와 실제 브라우저·HPC 재연결 확인. push·PR은 Claude 담당.

## 2026-09-26 · P1+ ④ 직원 CLI 개인 설정 격리
- 한 일: 어댑터가 PI 개인 CLI 설정을 빼고 직원 CLI를 띄운다(`engines.*.isolate_user_config`, 기본 켜짐). `engines.*.env`가 실제로 전달되게 고쳤다(전에는 무시됐다).
- 실측(Windows 11, 실제 계정): 개인 지침에 있는 단어를 묻는 질문으로 확인했다.
  - Claude 2.1.282: 기존 명령은 skill 55·plugin 6·개인 서브에이전트 3·SessionStart hook·전역 CLAUDE.md를 불러왔다. 격리 후 skill 0, 내장 plugin 2, 내장 서브에이전트 6, hook 0, 전역 지침 없음. 같은 질문의 입력 토큰이 약 12k 줄었다.
  - Codex 0.155: `--ignore-user-config --ignore-rules`로 config.toml의 plugin·notify hook·MCP가 빠졌다(입력 24k → 15.5k 토큰). 전역 AGENTS.md는 남는다. `project_doc_max_bytes=0`, `features.agents_md=false`로도 안 빠졌다.
  - Codex on Windows: config.toml을 건너뛰면 `[windows] sandbox`도 빠져 파일 쓰기가 막히는데 종료 코드는 0이었다. `windows.sandbox="elevated"`를 다시 넣어 쓰기를 확인했다.
  - agy 1.2.11: 격리 전에도 전역 지침을 읽지 않았다. `--disable-slash-commands`는 과학 DB skill까지 끌 수 있어 넣지 않았다.
- 가림 보강: Claude `rate_limit_event`에 요금제 사용률·재설정 시각·조직 초과사용 설정이 있었다. main의 Claude fixture 7개에 들어 있던 것을 가리고 구조 테스트를 붙였다.
- 리뷰 반영(Codex 봇 P1): 전역 AGENTS.md가 있으면 Codex 직원 작업을 실행 전에 거부한다(`engines.codex.allow_global_agents_md`로만 허용). 어댑터 파일 입출력을 UTF-8로 고정했다. 한국어 Windows에서 역할 지침 파일을 cp949로 쓰다 죽던 기존 결함이다.
- 리뷰 반영 2차: `isolate_user_config`는 구현된 Claude·Codex에만 둔다(엔진 설정의 모르는 키는 거부). 실측 스크립트도 같은 preflight를 거친다. prepare 이후에 subprocess 환경을 다시 만든다.
- 리뷰 반영 3·5차: 거부 검사와 CLAUDE.md 제외는 자식 CLI가 쓸 수 있는 home 후보 전부(병합된 `HOME`과 `USERPROFILE`)를 본다. `CODEX_HOME`·`CLAUDE_CONFIG_DIR`이 있으면 그것 하나(상대경로는 작업 폴더 기준).
- 리뷰 반영 4차: 실측 스크립트가 `LABHQ_CONFIG`를 읽는다. 설정 파일을 UTF-8로 읽는다(한국어 주석이 든 예시 설정이 Windows에서 cp949로 깨지던 결함).
- 리뷰 반영 6차: Claude는 작업 폴더에서 위로 올라가며 CLAUDE.md를 읽는다. 상위 폴더에 카나리 CLAUDE.md를 두고 격리 세션에 물으니 YES였고, 모든 상위 폴더의 CLAUDE.md·`.claude/CLAUDE.md`를 제외한 뒤 NO가 됐다(실제 계정, haiku, 회당 약 $0.009).
- 규칙: PR 리뷰 답글에 `@codex`를 붙이지 않고, push 뒤 `@codex review`를 한 번만 단다(PI 결정 2026-09-27, 댓글마다 봇 세션이 따로 떴다). CLAUDE.md·AGENTS.md·HANDOFF와 직원에게 주입되는 `ROLE_FOOTER`, README §4를 고쳤다.
- 리뷰 반영 8차: 실측 스크립트의 `--name`은 파일 이름만 받는다(가리지 않은 원본이 `--output-dir` 밖, 예컨대 공개 저장소로 나가지 않게). 예시 설정·`settings.py`의 멘션 안내도 새 규칙으로 맞췄다.
- 테스트: Windows 기존 실패 10개 → 9개(남은 것은 모두 기존 실패), 새 테스트 29개 통과. Linux는 CI.
- 막힌 점: Codex 전역 AGENTS.md는 직원용 `CODEX_HOME` 로그인(PI 조치)이나 러너 전용 계정으로만 빠진다. 직원용 로그인 방식은 미검증.
- 다음: P1+ 나머지 갈래(② CSO, ③ 데이터 경계, ①⑤ 영속화·이벤트 순번) 병합.

## 2026-09-26 · P1+ ② CSO 실패 전파·리뷰·재시도
- PR #7 3차: runner 재연결 신호 대기(기본 30초), skipped 웹 상태, review_unparsed 웹·GitHub 실패 표기를 추가. Windows 8 failed·90 passed(기준선 외 WebSocket 연결 대기 2건), 공개 검사 통과; 헤드리스 DOM 확인 실패.
- PR #7 P2 재검토: 예산 판정·승인을 요청 단위로 직렬화하고 완료 단계는 보존, 미시작 단계만 budget 사유로 skip. Windows 6 failed·89 passed(신규 실패 0), 공개 검사 통과.
- PR #7 리뷰 반영: runner offline·전송 실패는 재시도, 일반 nonzero exit은 중단, 리뷰 스키마 전체 검사. Windows 6 failed·87 passed(기존 실패만), 공개 검사 통과.
- 한 일: 실패·예산 거부·취소의 하위 단계만 skip하고 사유를 보고한다. 리뷰 판정은 엄격히 재요청한 뒤에도 읽지 못하면 `review_unparsed`로 실패 처리한다.
- 설정: `step_max_attempts=2`, `step_retry_backoff_s=0.2`. 재시도 비용도 요청 예산에 합산한다.
- 테스트: Windows 기준선 6 failed·64 passed → 6 failed·80 passed. 신규 실패 0. `scripts/check_public.sh` 통과.
- 막힌 점: Linux CI는 PR에서 확인 필요. 다음: Claude가 PR을 열고 P1+ 다른 갈래와 병합 검토.

## 2026-09-26 · P1 실제 CLI 연동 (Claude·Codex·agy·Gemini)
- 버전: claude 2.1.282 · codex-cli 0.155.0-alpha.16 · gemini-cli 0.57.0 · agy 1.2.11 (Windows 11 호스트, 실제 계정)
- 한 일: 실측 스트림 19개를 가려 `tests/fixtures/real/`에 넣고 파서 테스트를 붙였다. 조용한 실패 세 가지를 막았다: Claude의 `is_error: true`+`subtype: success`, Gemini CLI의 빈 stdout+종료 코드 0, agy의 권한 거부(`denied_actions`에만 남음).
- Codex: `exec` 기본 승인 정책 `never`가 MCP 호출을 실패시킨다(#24135 재현). labhq 내장 MCP 서버에만 `default_tools_approval_mode="approve"`를 붙인다. 승인은 도구 안의 폰 승인이 맡는다.
- Gemini: 개인 계정은 Gemini CLI 지원이 끝났다(`IneligibleTierError`). 새 `antigravity` 엔진을 추가하고 여우 문헌 담당을 옮겼다. agy에는 호출 단위 승인 훅이 없어 통제 데이터에 닿는 직원에게 쓰지 않는다.
- Claude: labhq 게이트웨이·러너를 거친 직접 요청이 정상 응답했다. 승인 도구가 실제 Claude와 호환되지 않던 결함을 고쳤다(mcp 2.x의 구조화 결과 때문에 거부). 수정 후 작업 폴더 안 쓰기는 바로 허용, 밖 쓰기는 폰 승인 요청 → 승인하면 기록, 거부하면 차단을 확인했다.
- Claude deny 규칙: Windows에서는 `Read(//c/Users/...)` 형식만 막힌다. 정책이 `C:\...`를 이 형식으로 바꾸게 고쳤고, 가짜 비밀 파일 읽기가 막히는 것을 실제 계정으로 확인했다.
- 도구: `scripts/probe_engines.py`(실측 재캡처), `scripts/redact_stream.py`(경로·계정·ID 가림, init 이벤트는 허용 목록 필드만).
- 테스트: Linux(WSL Ubuntu 22.04, Python 3.10) 70 passed. Windows는 기존 실패 11개 중 10개가 남았다(Claude 규칙 테스트는 이번 경로 수정으로 통과), 새 실패 없음. agy 어댑터는 실제 계정으로 한 번 돌려 확인했다.
- 막힌 점: 헤드리스 CLI가 PI 개인 설정(hook·서브에이전트·config·전역 지침)을 불러온다. 한 줄 응답에 $0.26이 든 것도 이 때문으로 보인다(UNVERIFIED). P1+에서 격리한다. Codex CLI는 OpenAI 쪽 401 오류로 현재 쓸 수 없다.
- 다음: P1+ 안전·복구 최소선.

## 2026-09-26 · 로드맵 갱신 (PI 결정)
- 한 일: HANDOFF [3]에 P1+ 안전·복구 최소선을 넣었다. P1 다음, P2·P3 전에 상태 영속화·CSO 실패 전파·통제 데이터 경계·CLI 개인 설정 격리·이벤트 순번을 한다.
- P4를 실사용 점검 + 3D 도입으로 고쳤다. 3D 사무실(#3)을 `/3d`로 연결하고 2.5D와 함께 둔다.
- 근거: 세 자문(gpt-6-sol, gpt-6-astra, Gemini 3.8 Flash) 설계 검토. 셋 다 교체가 아니라 수정이라고 판정했다.
- 다음: P1 실제 CLI 연동.

## 2026-09-25 · PR #3 리뷰 수정 · 스킨 scale과 상태 표지 분리

- 한 일: glTF의 named/추정 head·양손·label 앵커를 scene-space proxy로 제공. world 위치·회전만 따라가고 scale은 1로 유지한다. 손 포즈는 원본 노드를 움직이며 proxy는 숨김·dispose도 따른다.
- 회귀 확인: scale 0.01·1·5 × 앵커 유/무, 108포즈 표본 통과. 표지 부품 크기는 procedural 대비 1.0배(오차 <1e-7), world AABB 대각선은 포즈 차이로 0.975–1.031배. 콘솔 오류·외부 요청 0.
- 테스트: Windows: lab3d 10 passed; 전체 11 failed·25 passed, 기존 실패 11개 ID 동일. 기본 procedural 사무실 9 drawCalls/69,382 triangles 유지. `bash scripts/check_public.sh` 통과.
- 바꾼 파일: `src/skins.js`, `lab3d/README.md`, `lab3d/SKINS.md`, `STATUS.md`. 브라우저 QA와 상세 로그는 로컬 `.pr-drafts/p4-3d-artlab/review3/`.
- 막힌 점: 이번 지적 없음. Linux·실기 브라우저는 UNVERIFIED. push·merge는 수행하지 않는다.
- 두 번째 지적: 메시를 움직이는 손 노드·bone을 L/R 이름으로 추정하고, 한쪽이라도 없으면 상태별 전신 포즈를 적용한다. `skin.motion`에 선택 경로를 기록했다.
- 추가 검증: 명시 앵커·이름 추정·L/R 접미사·weighted bone·손 없음·빈 노드 6모델의 실제 정점 이동 통과. scale 108표본·9 drawCalls 유지, pytest 10 passed/전체 기존 실패 11개 동일·공개 검사 통과. 로컬 근거: `.pr-drafts/p4-3d-artlab/review3-motion/`.

## 2026-09-25 · P4 선행 · 3D 아트 랩 (Codex gpt-6-astra, 게이트웨이 미연결)

- 한 일: 원본 종이숲의 12석·캐릭터 형상을 유지해 독립 lab3d로 이관. procedural/glTF 스킨 계약, 공용 상태 표지, 폰 이름표·승인 칩, 파견직 최대 4명과 모자 배지 추가.
- 바꾼 파일: `labhq/web/lab3d/`, `labhq/web/vendor/three/`, `tests/test_lab3d.py`, `STATUS.md`.
- 테스트: Windows Python 3.12: 새 pytest 10 passed. 전체 11 failed · 25 passed로 기존 실패 11개의 ID가 기준선과 같다. 공개 검사 통과.
- 브라우저: Chrome 153 headless · 390×844 CSS px · DPR 2: 12종×6상태, glTF/GLB, 클립·앵커 누락, 실패 복구, 파견직, 폰 탭·승인 순회, 480/481px 경계, reduced-motion 확인. 페이지 콘솔 오류·외부 요청·가로 넘침 0.
- 막힌 점: GPU 자식 프로세스가 샌드박스에서 종료돼 SwiftShader로 측정했다. 수치는 해당 실행의 표본이다. Linux·iPhone Safari 실기 성능/발열·외부 rigged 모델은 UNVERIFIED.
- PI 결정: 3D로 간다. 두 아트 시안 중 gpt-6-astra 시안(캐릭터·배치)을 골랐다. 캐릭터는 나중에 외부 3D 모델로 바꿀 수 있어야 한다.
- 의존성: three.js 0.186.1(MIT)을 `labhq/web/vendor/three/`에 벤더링했다. 세 자문(gpt-6-sol, gpt-6-astra, Gemini)이 모두 권한 방식이다. 되돌리려면 이 커밋 하나를 revert한다.
- 다음: `/3d` 서빙과 본 구현은 P1–P3 뒤 P4에서 한다. 기존 2.5D 사무실과 게이트웨이는 이 PR에서 바뀌지 않는다.
- 근거: 로컬 `.pr-drafts/p4-3d-artlab/`의 스크린샷 3장·검증 로그. PR 첨부는 요청자가 진행한다.

| 화면 | drawCalls | triangles | fps |
|---|---:|---:|---:|
| 사무실 | 9 | 69,382 | 39 |
| 80px 도감 | 6 | 69,316 | 54 |
| CSO glTF 교체 | 13 | 65,694 | 37 |
| 파견직 3명 | 9 | 75,970 | 35 |

## 2026-09-25 · P0+ CI (Codex)
- 한 일: 모든 브랜치 push·PR·수동 실행에서 Ubuntu Python 3.10·3.12 pytest를 돌리는 CI 추가. README에 배지와 저장소 주소 추가.
- 테스트: `bash scripts/check_public.sh` 통과. Windows 11(Python 3.12) `pytest -q`는 main db49ed4와 같은 11 failed · 15 passed. Linux 26 passed는 WSL·CI에서 별도 확인.
- 다음: P1 실제 CLI — 첫 실계정 실행 전 ⛔.

## 2026-09-25 · P0 인수인계 문서 (Codex)
- 한 일: 부록 A 파일 5개 추가, 버전 0.2.0 → 0.2.1, .gitignore에 로컬 전용 노트 파일 2개 추가
- PI 결정: 엔지니어 에이전트는 Codex. 웹 사무실은 최종적으로 3D로 간다 (설계 제안은 별도 PR).
- PI 위임: Codex 리뷰 봇과 PR당 최대 10회 주고받고, 합의된 PR은 Claude가 병합한다.
- 테스트: Linux(WSL Ubuntu 22.04, Python 3.10) 26 passed. Windows 11(Python 3.12)은 main db49ed4와 같은 11 failed · 15 passed.
- 막힌 점: Windows 전용 실패 11개는 기본 인코딩 cp949(4개, PYTHONUTF8=1로 풀림), fake CLI 셸 스크립트 실행 WinError 193(4개), 경로 구분자 가정(3개).
- Windows 경로 문제: 통제 경로 Read가 deny 대신 allow, Claude 권한 규칙이 `Read(/\data\cohort/**)`로 깨짐, 공개 가드가 `/data/cohort`를 가리지 못함. Linux 러너 대상이라 이번 범위 밖이지만 Windows 러너에서는 데이터 구역 가드가 작동하지 않음.
- 다음: P0+ CI PR (이 PR 위에 쌓음)

## 2026-09-25 · P0 GitHub 저장소 (PI, 확인: Claude)
- ehojune/bioinfo-team-3d에 public으로 올림 (main db49ed4, v0.2.0, 파일 62개)
- 확인: main에서 pytest 26 passed, 비밀값·실제 설정 파일 없음 (검사에 걸린 두 곳은 탐지용 정규식 문자열)
- 인수인계본(v0.2.1)과의 차이: 버전 표기와 인수인계 문서뿐, 코드는 같음
- 다음: handoff-docs PR → P0+ CI → P1
- 결정 대기: HANDOFF.md 맨 아래 목록
