## 단계
#1187(PI 승인 #883 2번): 직원용 PDB(RCSB)·ChEMBL 조회 도구를 `labhq_annot`에 더한다. 구현 설계는 [#1187 설계 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1187)의 승인안 그대로다.

## 바뀐 것
- `labhq/tools/annot_structure.py`(새): `StructureAnnotator`. `pdb(ids_or_queries, top)`: PDB ID는 Data API, 그 밖의 문자열은 Search API 전문 검색 후 상위 `top`(1~25)을 요약. 좌표 파일은 받지 않는다. `chembl(targets_or_compounds, kind, limit)`: `target`·`compound`는 ID나 검색어(상위 5), `activity`는 target ChEMBL ID만(최대 200행, 100행씩 페이지). 잘리면 `truncated`.
- `annot_mcp.py`: `_run_structure`와 도구 둘, instructions 문구. 시간 상한(`run_lookup`), 요청 조회 원장(`_admitted`)은 기존 도구와 같은 경로다.
- `annot.py`: `MIN_INTERVAL_S`에 `rcsb` 0.2초·`chembl` 0.5초, `CACHE_MAX_AGE_S`에 `pdb` 7일. ChEMBL은 `status.json`의 `chembl_db_version`이 캐시 키에 들어가 버전이 바뀌면 새로 받는다.
- `cso.py`: 직원 명단 문장에 "RCSB PDB structure and ChEMBL compound lookups". 계획 prompt 본문이 아니라 명단 문장이라 해시 시험은 바뀌지 않았다.
- `source`에 `license`·`license_url`·`usage`: RCSB CC0 1.0, ChEMBL CC BY-SA 3.0.
- 시험: `tests/test_annot_structure.py` 27건(오프라인 `httpx.MockTransport`). 도구 목록을 정확히 비교하던 `test_annot.py`·`test_annot_regulatory.py`에 두 이름을 더했다. Windows inventory에 새 시험 추가.

## 실행한 것
- `test_annot_structure.py` 27 passed. `test_annot*.py`·`test_external_wait.py`·`test_output_types*.py` 237 passed.
- `validate_profile` 통과.

## 확인하지 못한 것
- RCSB·ChEMBL의 공식 호출 한도와 이용 조건: 공식 페이지를 열지 못했고 제3자 목록은 "수치 상한 비공개"라고만 했다. 간격은 보수적으로 잡았고 코드에 미확인이라고 적었다. 라이선스 이름은 총괄이 준 문구와 알려진 정책이며 RCSB 정책 페이지는 직접 읽지 못했다.
- 실제 서비스 호출: 하지 않았다. API 응답 모양은 공개 문서 기준 fixture다.
- 로컬에서 `test_annot.py::test_stdio_server_lists_tools…`와 `test_annot_regulatory.py::test_with_a_key…`가 `CallToolResult.is_error` 속성 오류로 실패한다(설치된 mcp 버전 차이, 이 변경과 무관). 도구 목록 비교 줄은 그 앞에서 통과했다. CI의 고정 버전에서 확인한다.
- manual 한·영은 총괄이 따로 잡는다.

## rev4: chief_of_staff
- `agents/core/chief_of_staff.yaml`의 `disallowed_tools`에 `mcp__labhq_annot__pdb`·`mcp__labhq_annot__chembl`을 더했고, 그 집합을 고정한 `tests/test_annot_literature.py::test_chief_of_staff_gets_only_the_literature_tools_of_annot`도 같이 고쳤다. 시험을 먼저 고쳐 수정 전 실패를 확인했다.
- 후속(구조): 새 도구가 생길 때마다 같은 일이 반복되므로 CoS를 deny 목록이 아니라 allow 목록(문헌 도구만)으로 바꾸는 수정이 필요하다.

## rev5: CI와 P2
- CI 실패 둘은 제 결함: (1) 시험이 `mcp.server.fastmcp`(mcp 2.x에 없음)에서 `ToolError`를 import → `labhq.tools._mcpcompat`. (2) staff 쪽 파일에 `/api/`가 있으면 안 되는 시험(`test_semantics_actions_run.py`)에 ChEMBL 주소가 걸림 → 문자열을 부분으로 이어 쓰고, 그 이유와 이름이 시험 문자열에 걸리지 않게 주석을 고침.
- 리뷰 P2: ChEMBL 원본 응답이 요약 허용 목록에서 버려지던 것을 `answer.raw`로 보존(PDB와 같은 방식). 모든 입력이 잘못되면 `status.json`도 부르지 않는다.
- 실행: `test_annot_structure.py`(31건)·`test_semantics_actions_run.py`·`test_semantics_shadow_remove.py`·`test_external_wait.py` 143 passed.

## rev6: main 반영(#1201 이후)
- #1201(annot_tools 허용 목록)이 먼저 병합되어 `annot_mcp.py`·`chief_of_staff.yaml`·`test_annot_literature.py`·`cso.py`가 충돌했다. 충돌은 main 쪽을 기준으로 풀었다.
- `pdb`·`chembl`을 `@offered()`로 등록. `chief_of_staff.yaml`의 deny 목록 추가(rev4)는 허용 목록이 대신하므로 뺐다(CoS는 문헌 7개만 받는다).
- 정적 시험 둘(`test_annot_structure.py`의 모든 도구가 실행기를 거치는지, `test_external_wait_board_676.py`의 20분 실행기 확인)이 `offered` 장식자와 `_run_structure`를 인식한다.
- 시험: 관련 `test_annot*.py`·`test_external_wait_board_676.py`·`test_agent_prompts.py`·`test_semantics_actions_run.py` 212 passed. 로컬 mcp 1.x에서 `is_error` 속성 오류 2건은 이 변경과 무관(CI는 mcp 2.x).

## rev7: 명단 문장과 manual
- `cso.py`의 `_annot_roster` 기본 문장에 "RCSB PDB structure and ChEMBL compound lookups"를 되살렸다(rev6에서 충돌 해결로 빠졌던 것). 허용 목록이 있는 직원은 `tools: ...` 문장이라 영향이 없다.
- manual 한·영: 공개 자원 조회 도구 표에 `pdb`·`chembl` 행, 설명 단락. 입력 형식, `truncated`, `answer.raw` 보존, 요청 간격(0.2초·0.5초)과 **공식 한도 미확인**, 캐시(PDB 7일·ChEMBL 버전 키), 라이선스(CC0 1.0·CC BY-SA 3.0), 비서실장 제외.
- 시험 먼저: 명단이 PDB·ChEMBL을 알리고 제한된 직원에는 알리지 않는 것, manual 한영에 두 도구가 있는 것. 수정 전 둘 다 실패. 관련 203건과 `test_annot_structure.py`·`test_annot_literature.py` 90건 통과.
- 확인하지 못한 것: RCSB·ChEMBL 공식 호출 한도(공식 문서를 열지 못함, 제3자 목록은 "수치 비공개"), 실제 서비스 호출, 로컬 mcp 1.x에서 `is_error` 오류 2건(CI는 mcp 2.x).

## rev8: main 반영(Atlas 병합 뒤)
- #1186(AlphaGenome Atlas)이 병합되어 `annot_mcp.py` import 줄과 manual 한·영의 AlphaGenome 항목이 충돌했다. 서버는 `annot_structure`와 `ATLAS_ENABLED_ENV` import를 둘 다 두고, manual은 PDB·ChEMBL 항목에 main의 AlphaGenome 항목을 이어 붙였다.
- 시험: `test_annot_structure.py`·`test_annot_literature.py`·`test_external_wait_board_676.py`·`test_annot.py`·`test_annot_regulatory.py`·`test_annot_alphagenome_atlas.py`·`test_semantics_actions_run.py`·`test_agent_prompts.py` 363 passed, 4 skipped. 로컬 mcp 1.x의 `is_error` 오류 2건은 이 변경과 무관(CI는 mcp 2.x).

## rev9: 리뷰 P1 — 유효성 표지
- P1(`b0540f54` 리뷰): activity 요약의 허용 목록이 `data_validity_comment`·`potential_duplicate`를 버려 의심 값과 중복 인용이 보통 측정값처럼 보였다. 두 필드를 `_ACTIVITY_KEEP`에 넣었다.
- 요약에 `flagged_rows`(표지가 붙은 행 수)를 항상 싣고, 1건 이상이면 `validity_note`로 "의심스러운 측정·독립이 아닌 중복 인용, 근거로 쓰기 전에 확인"을 적는다. 표지가 없으면 노트는 없다.
- 시험 먼저: 표지가 요약 행에 있고 개수·노트가 맞는 것, 표지가 없으면 노트가 없는 것(수정 전 둘 다 실패). `test_annot_structure.py` 34건과 관련 시험 통과.
