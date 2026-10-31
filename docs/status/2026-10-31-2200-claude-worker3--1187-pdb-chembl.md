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
