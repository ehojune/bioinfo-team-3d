## 단계
#1200: 비서실장(CoS)의 annot 도구를 이름 차단 목록이 아니라 허용 목록으로. 설계는 [#1200 설계 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1200)의 승인안이다.

## 바뀐 것
- `AgentSpec.annot_tools: list[str] | None = None`(`models.py`). `None`은 전부(기존 동작), 목록이면 그것만. 이름은 소문자 도구 이름이고 `builtin_mcp`에 `annot`이 있어야 한다.
- `runner/daemon.py`: `annot_tools`가 있으면 `labhq_annot` 서버 env에 `LABHQ_ANNOT_TOOLS=<쉼표 목록>`(빈 목록은 빈 값). 비밀이 아니다.
- `annot_mcp.py`: 모든 도구를 `@offered()`로 등록한다. 허용 목록 밖이면 등록하지 않는다. 목록에 서버가 정의하지 않은 이름(오타·빈 이름)이 있으면 시작 전에 `SystemExit`(실패 시 닫힘). `alphagenome` 두 도구는 키 파일이 있을 때만 정의되므로 이름은 허용한다.
- `agents/core/chief_of_staff.yaml`: annot 이름 차단 줄을 지우고 `annot_tools`에 문헌 7개를 적었다.

## 왜 서버에서
Claude의 `--disallowedTools`만 이름 목록을 지키고 Codex·Gemini 어댑터는 이 목록을 읽지 않는다. Claude에는 MCP 도구 허용 목록 플래그가 없다. 서버가 거르면 모든 엔진이 같은 `list_tools`를 본다.

## 실행한 것
- 시험을 먼저 추가해 수정 전 실패를 확인했다. `tests/test_annot_literature.py`(52건): stdio 서버가 허용된 도구만 내는지, 환경 변수가 없으면 전부, 모르는 이름이면 시작 거부, 러너 env 전달, 직원 yaml의 목록이 서버 도구의 부분집합, 이름 형식 거부.
- 관련 `test_annot.py`·`test_annot_regulatory.py`·`test_agent_prompts.py`·`test_agent_capabilities.py`·`test_external_wait.py`·`test_registry.py`: 107 passed. 로컬에서 `CallToolResult.is_error` 속성 오류로 2건이 실패하나 설치된 mcp 버전 차이이며 이 변경 전에도 같다(#1195 상태 문서 참고).

## 하지 않은 것
- Codex·Gemini 도구 필터 기능은 쓰지 않았다(버전 지원 미확인).
- Windows inventory는 새 시험 파일이 없어 건드리지 않았다.
- 신규 직원의 기본을 "문헌만"으로 바꾸는 것은 이번 범위가 아니다.
- #1195(`pdb`·`chembl`)와 `chief_of_staff.yaml`·`test_annot_literature.py`에서 겹친다. 이 PR을 따르면 #1195의 deny 목록 수정은 필요 없어지고, #1195의 정적 시험(`@server.tool` 장식자를 찾는 시험)은 `offered`를 찾도록 고쳐야 한다.

## rev3
- `tests/test_external_wait_board_676.py`: 도구를 `@server.tool()`만이 아니라 `@offered()`로도 찾는다. 도구 수 하한(15)과 "모든 도구가 실행기를 부른다"는 확인은 그대로다. 이 시험이 바뀌지 않으면 #1200이 CI에서 실패한다.
