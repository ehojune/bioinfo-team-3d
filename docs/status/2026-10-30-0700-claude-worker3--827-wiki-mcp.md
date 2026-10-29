## 단계
#827 PR 2: `labhq_wiki` MCP(search·read·status)와 `outputs/wiki_queries.jsonl` 기록(producer 계약 v1).

## 한 일
1. `labhq/tools/wiki_mcp.py`: 호출마다 계약 v1 한 줄(검색어·snippet은 해시·길이만, `source_level: tertiary`)을 append. 실패는 코드 하나(`wiki_disabled`·`root_problem`·`index_failed`·`not_found`·`range_invalid`)로 기록. `status`는 root 경로를 숨긴다.
2. `labhq/wiki/index.py`: `read()` 추가 — 색인에 있는 노트만 열고(절대 경로·`..`은 `not_found`), 한 번에 200줄, 파일 전체 sha256.
3. `Runner._mcp_servers`: `wiki`가 `builtin_mcp`에 있고 `wiki.enabled`·`external_model_allowed`가 모두 켜졌을 때만 `labhq_wiki` 부착. runner roster에 `wiki_tools`를 실어 gateway `_agent_mcp`가 같은 조건으로 목록을 만든다.
4. `lit_scout`·`biologist`·`sci_reviewer`에 `wiki` 부착. manual 한·영, `config/ci` Windows inventory.

## 테스트 결과
- Windows `tests/test_wiki_mcp.py`·`tests/test_wiki_index.py` 통과, 관련 test(`test_agent_prompts`·`test_isolation`·`test_e2e_mock`)와 `windows_boundary` 계열 145 passed.
- `tests/test_annot.py::test_stdio_server_lists_tools...`는 main에서도 같은 이유(`CallToolResult.is_error` → mcp 버전)로 실패하는 기존 문제.
- 미검증: 실제 stdio MCP 왕복(이 PC의 mcp 클라이언트 버전 문제로 직접 호출 test만), 실제 사용자 wiki, WSL.
