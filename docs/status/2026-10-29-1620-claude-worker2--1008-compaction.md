# compaction 구간 예외 (#1008)

| 엔진 | 압축 신호 | 근거 |
|---|---|---|
| Claude Code 2.1.310 | 있음. stream-json `system`/`compact_boundary`, `compact_metadata{trigger,pre_tokens,post_tokens}` | `claude.exe`에서 frame schema 문자열 확인(실제 압축을 일으켜 본 것은 아님) |
| Codex 0.165 (`exec --json`) | 없음. `exec`의 item 종류는 agent_message·reasoning·command_execution·file_change·mcp_tool_call·collab_tool_call·web_search·todo_list. `thread/compacted`·`contextCompaction`은 app-server 쪽 | `codex.exe` 문자열 확인 |
| Gemini·Antigravity | 확인하지 않음(어댑터에 신호 처리 없음) | 코드 |

- 구현: `adapters/claude_code.py`가 `agent.compaction`을 내고, runner가 `Breaker.reset_window()`로 집계를 다시 세며 경고 단계는 푼다. 제한(constrain·stop)은 PI 해제 전까지 유지.
- 시험: `tests/test_breaker_enforcement.py`(+3).
- 미확인: 실제 Claude 세션에서 압축을 일으켜 프레임을 받아 본 것, `status: compacting` 프레임(문자열만 확인, 처리 안 함).
