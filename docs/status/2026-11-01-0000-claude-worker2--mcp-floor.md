# #1202: mcp 하한을 코드가 쓰는 2.0으로 올리고 doctor가 경고한다

- 결론: `pyproject.toml`의 `mcp>=1.2`를 `mcp>=2.0`으로 올렸고, `labhq doctor`에 `mcp` 행(`config`)을 더했다. 설치된 mcp가 2.0보다 낮거나 없으면 warn과 `pip install -U "mcp>=2.0"` 안내를 낸다.
- 근거: mcp 1.x는 `CallToolResult.isError`, 2.x는 `is_error`(실측: 1.28.1 필드 `isError`, 2.0.0 필드 `is_error`). 시험(`tests/test_annot.py`)이 2.x 이름을 쓴다. `labhq/tools/_mcpcompat.py`는 제품 코드가 두 판본을 모두 받게 하지만 시험·claim 기준은 2.x다.
- 바뀐 것: `pyproject.toml`, `labhq/doctor.py`(`MCP_FLOOR`, `_mcp_row`), `tests/test_doctor.py`(경계 5건·없음·pyproject와 하한 일치·collect 행 1개).
- 실행한 것: 1.28.1 환경과 2.0.0 새 venv 둘 다 `tests/test_doctor.py` 71 passed. 2.0.0 venv에서 `tests/test_annot.py`·`tests/test_external_wait_board_676.py` 30 passed.
- 미해결/미검증: 설치판이 고정한 mcp 버전은 확인하지 못했다(이 repo에는 고정 파일이 없음). 2.0.0 밖 판본(2.1~2.3)에서 전체 suite는 CI에 맡긴다. 하한 위쪽 상한(`<3`)은 두지 않았다.
