# #1055 — 과학 리뷰어가 PDB·ChEMBL을 집계되는 도구로만 조회

- 결론: `sci_reviewer`에 `labhq_annot`의 `pdb`·`chembl`을 준다. 그런데 직원 파일만 고치면 효과가 없었다. 리뷰는 읽기 전용 task이고 `read_only_profile`이 `builtin_mcp`·`annot_tools`·`mcp`를 모두 지우기 때문이다(실제 프로필을 돌려 확인). 그래서 **읽기 전용 허용 목록을 바꿨다**.
- 읽기 전용 허용 목록 변경(`labhq/adapters/read_only.py`): 직원 파일이 `builtin_mcp`에 `annot`을 적고 `annot_tools`에 이름을 명시했을 때, 그중 `READ_ONLY_ANNOT_TOOLS`(`pdb`, `chembl`)에 있는 것만 읽기 전용 프로필에 남긴다. 직원 파일의 목록은 허용 집합을 좁히기만 하고 넓히지 못한다. 목록이 없거나 허용 도구가 하나도 없으면 지금처럼 꺼진다. `read_only_mismatch`는 그 서버가 정확히 그 도구만 내놓을 때만 받아들인다. 보내는 쪽의 override는 읽기 전용 task에 적용되지 않는다.
- 영향 범위: 이 목록에 있는 도구를 명시한 직원은 `sci_reviewer`뿐이다. `chief_of_staff`의 문헌 도구 목록은 허용 집합과 겹치지 않아 읽기 전용에서 그대로 꺼진다.
- 바꾼 것: `agents/core/sci_reviewer.yaml`(`builtin_mcp: [annot]`, `annot_tools: [pdb, chembl]`, 셸 대신 도구를 쓰라는 프롬프트 한 문장), `labhq/adapters/read_only.py`, manual 한·영, 시험(`test_read_only_profile.py` +8건, `test_annot_literature.py` +1건, `test_annot.py` 집합 갱신).
- 시험: 허용 목록으로 좁혀지는지, 목록이 없거나 비면 꺼지는지, 다른 채널(`mcp`·`plugin_dirs` 등)은 여전히 꺼지는지, 실제 `sci_reviewer.yaml`이 프로필에서 살아남는지, 러너가 서버를 달고 mismatch 검사가 정확히 그것만 받아들이는지, 보내는 쪽이 목록을 넓히지 못하는지. 이 변경을 빼면 5건이 실패한다. 관련 시험 253개 파일 중 2건(`test_stdio_server_*`, `test_with_a_key_*`)이 로컬에서 실패하지만 origin/main에서도 같게 실패한다(이 PC의 mcp 1.x).
- 미검증: Codex 엔진의 `sandbox: read-only` 안에서 `labhq_annot` 호출이 실제로 통하는지(`codex exec` 필요). 셸 HTTP 차단은 이 PR이 아니라 #1060이다. PubMed·bioRxiv 원격 MCP도 읽기 전용 프로필이 지운다는 점은 따로 다룬다.
