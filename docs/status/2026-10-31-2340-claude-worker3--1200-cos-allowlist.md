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

## rev4
- `labhq/adapters/read_only.py`: 읽기 전용 프로필이 `AgentSpec`의 모든 필드를 분류해야 해서 `annot_tools`가 없으면 `RuntimeError`로 읽기 전용 실행(검토·후속)이 시작하지 못했다(CI의 `test_e2e_mock`·`test_followup`·`test_workspace_boundary` 실패). `READ_ONLY_FIELDS`에 `"annot_tools": None`을 넣었다. 설계 때 "확인만"으로 적은 파일이 실제로는 수정 대상이었다.
- 시험: `test_a_read_only_run_offers_no_annot_tools_whatever_the_staff_spec_allows`. 위 세 시험과 `test_github_reporter.py`는 로컬에서 통과(206 passed, 18 skipped).

## rev5: 리뷰 P1 하나와 P2 둘
- manual P1: `docs/manual.md`·`docs/manual.en.md`의 공개 자원 조회 도구 절에 `annot_tools`의 세 경우(없음=전부, 목록=그것만, 빈 목록=없음), 모르는 이름이면 서버가 시작하지 않는 것, 읽기 전용 실행은 받지 않는 것을 적었다.
- roster P2: `AgentSpec.summary()`가 `annot_tools`를 싣고, `format_roster`가 `_annot_roster`로 제한이 있으면 `labhq_annot=yes (tools: ...)`만 적는다(전부면 이전 문장 그대로). CSO가 비서실장에게 못 하는 변이·영역 조회를 배정하지 않게 한다.
- env P2: `annot_tools`가 없는 직원에게도 runner가 `LABHQ_ANNOT_TOOLS=*`를 서버 env에 명시한다. 서버는 `*`를 제한 없음으로 읽는다(`*,fulltext`는 모르는 이름이라 시작 거부).
- 시험: `tests/test_annot_literature.py` 56건. 관련 `test_annot.py`·`test_agent_prompts.py`·`test_registry.py`·`test_external_wait_board_676.py`·`test_workspace_boundary.py` 포함 114 passed, 18 skipped. 로컬에서 `test_annot.py::test_stdio_server_lists_tools…`가 `is_error` 속성 오류로 실패(mcp 버전 차이, 이 변경 전부터).

## rev6: override가 만든 스펙
- 리뷰 P1: `pi_language.py`의 통역 task가 비서실장의 `builtin_mcp`를 `[]`로 덮는데 내가 넣은 검증("annot_tools needs builtin_mcp: annot")이 그 병합 결과를 거부해 통역이 모델 실행 전에 실패했다. 맞는 지적이고 내 결함이다.
- 고침: 검증에서 `builtin_mcp` 결합을 뺐다(이름 형식만 검증). annot 서버가 없으면 `annot_tools`는 쓰이지 않을 뿐이다. `builtin_mcp`를 덮는 부류(통역·읽기 전용·기타 override) 전체가 닫힌다.
- 시험 먼저: 덮어쓴 비서실장 스펙이 유효한 것, `builtin_mcp=[]`일 때 러너가 `labhq_annot`을 붙이지 않는 것. 수정 전 둘 다 실패. `test_annot_literature.py`·`test_followup.py`·`test_pi_language.py`·`test_workspace_boundary.py` 등 259 passed, 18 skipped.
