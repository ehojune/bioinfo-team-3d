## 단계
#1037 rev11(인계): wiki 노출 이력과 공개 게시 차단의 첫 단위.

## 한 일
- `labhq/evidence/wiki_exposure.py`: 요청의 `wiki_exposure`(`none`/`possible`/unknown) 규칙. `none`일 때만 공개 게시 허용.
- `labhq/gateway/server.py`: 새 요청은 `none`으로 시작. `dispatch`가 전달 전에(`_bind_wiki_exposure`) roster의 `wiki_tools`로 task 허가(`meta["wiki_tools"]`)를 정하고 필요하면 요청을 `possible`로 올려 저장한다. roster 키가 없거나 직원이 없으면 unknown으로 보고 `possible`, 허가 없음. 이력이 없는 요청은 기록하지 않는다.
- `labhq/runner/daemon.py`: `_mcp_servers(..., wiki_bound=False)`. 허가가 묶인 dispatch에만 `labhq_wiki`를 붙인다(draft의 runner 불일치 시험 해결).
- `labhq/result_storage.py`: `progress_step`이 `none`이 아니면 한 번 알리고 진행 게시를 끈다(draft의 게시 차단 시험 2건 해결).
- 시험 `tests/test_wiki_exposure_1037.py`: 게이트웨이 binding 9가지, 새 요청의 `none`, runner 허가. manual 12장 한영, 참고 문서 `docs/reference/wiki-evidence(.en).md`, Windows inventory 3줄.

## 확인
- Windows: wiki 증거·게시·노출 시험 49 passed. 관련 시험(wiki·annot·hpc·job·read-only profile·research cp2) 469 passed. `test_annot*.py` 2건은 이 변경과 무관하게 이미 실패한다(mcp 라이브러리의 `is_error`).

## 이 PR에서 뺀 것
- W1의 증거 수준 초안(`claims.py`의 `wiki_summary`, `wiki_contract.py`, `tests/test_wiki_evidence.py`)은 되돌렸다. 엔진 결과 계약의 스키마 hash(`test_output_types_research`)를 바꾸고 봇이 결과 계약 prompt 규칙에도 `wiki_summary`를 더하라고 지적했다. 초안은 `codex-worker1/1037-lane`에 있다.

## 하지 않은 것(다음 단위)
- 보고서·업로드·라운드 게시, `ProjectReporter`의 pipeline 기여, CP2 차단.
- 게시 검사와 외부 쓰기 사이 경쟁, 재시작·구형 runner 통합 시험, 이어 묻기 요청의 노출 상속.
- 독립 출처 인증과 wiki 본문·경로 마스크.
- 기존 시험 두 개(`tests/test_step_progress_push.py`, `tests/test_wiki_mcp.py`)는 fixture만 새 규칙에 맞췄다.
