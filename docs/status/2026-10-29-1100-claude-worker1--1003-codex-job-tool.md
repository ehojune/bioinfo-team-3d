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
