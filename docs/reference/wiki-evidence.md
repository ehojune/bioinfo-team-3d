# wiki 노출 이력과 공개 게시 차단 (#1037)

lookup-only. 동작 설명은 [manual 12장](../manual/12-github-reports.md), 증거 계약은 #827 댓글에 있다. 증거 수준 검사(`wiki_summary`를 원문 직접 독해로 승격하지 못하게 하는 규칙)는 W1 초안(`e2a36148`)을 되살려 넣었고, 결과 스키마 hash가 의도적으로 바뀐다. 공개 보호는 아직 **UNVERIFIED**다.

## 정한 것

| 항목 | 값 |
|---|---|
| 기록 | 요청 기록의 `wiki_exposure` (`labhq/evidence/wiki_exposure.py`) |
| `none` | 이력을 적는 게이트웨이가 만든 요청이고, wiki 도구를 줄 수 있는 직원에게 task를 보낸 적이 없다 |
| `possible` | 그런 직원에게 task를 보냈다. 다시 `none`이 되지 않는다 |
| 없음·그 밖의 값 | **unknown**. 이력이 없거나 읽을 수 없다. 단 `created_at`과 `updated_at`(저장할 때마다 갱신된다)이 모두 숫자이고 `labhq_wiki` 출시(PR #1040 병합, 2026-10-29T21:01:56Z) 이전이면 `none`으로 본다(그 뒤로 요청을 건드린 적이 없어 도구를 쓸 수 없었다). 생성만 이전이고 이후에 갱신됐거나(resume 등) 시각이 없으면 unknown |
| 공개 게시 | `none`일 때만 허용한다. `possible`·unknown은 막는다 |

## 어떻게 묶었나

- 게이트웨이 `dispatch`가 task를 보내기 직전에(`_bind_wiki_exposure`) 그 runner가 보낸 roster의 `wiki_tools`로 task `meta["wiki_tools"]`를 정하고, wiki가 가능하거나 알 수 없으면 요청을 `possible`로 올린다. 저장은 전달보다 먼저다.
- roster에 `wiki_tools` 키가 없거나(옛 runner), 그 직원이 roster에 없으면 알 수 없는 것이므로 `possible`로 올리고 task에는 허가를 주지 않는다.
- runner는 `meta["wiki_tools"]`가 참인 dispatch에만 `labhq_wiki`를 붙인다(`_mcp_servers(..., wiki_bound=True)`). 전에 보낸 roster가 나중의 도구 구성을 허가하지 못한다.
- 이력이 없고 출시 이후인 요청은 dispatch가 와도 기록하지 않는다(unknown 그대로). 출시 이전 요청은 `none`으로 보므로 wiki 직원에게 task가 가면 `possible`이 된다.

## 이번 단계가 막는 것과 아직 아닌 것

| 경로 | 상태 |
|---|---|
| 단계별 GitHub 진행 게시(`ResultStorage.progress_step`) | 막음 |
| `ProjectReporter`: 프로젝트 issue·댓글·보고서·부록·종료, pipeline 기여(승인 카드 전, branch·파일·PR 직전) | 막음(쓰기 직전 재확인) |
| `RoundRecorder`: 라운드 issue 쓰기 | 막음(쓰기 직전 재확인) |
| `ResultStorage.upload`: 연구 결과 올리기(문서·그림·PDF 파일마다) | 막음(쓰기 직전 재확인) |
| CP2 근거 카드 | 막지 않고 경고 한 줄(PI의 검토이지 게시가 아님) |
| 진행 게시 안의 경쟁(게시 도중 노출이 올라감) | 막음: 파일마다 쓰기 직전에 다시 확인 |
| CP1 수정 요청·모델 거부 뒤 다시 시작한 요청의 노출 상속 | 막음: 새 요청이 부모의 수준으로 시작한다(첫 저장에 들어간다) |
| 재시작·구형 runner | 시험함: 기록은 저장소에서 다시 읽고(재계산하지 않는다), `wiki_tools` 키가 없는 구형 runner에 보낸 요청은 `possible`이 된다 |
| 결과 올리기의 새 저장소 생성 직전 재확인 | 시험함 |
| 증거 수준 검사: `wiki_summary` 행은 literature_claim·tertiary·indirect이고 원본(lineage.original)이 될 수 없다(`claims.py` 검증). 같은 주장에 `full_text` 읽기 기록이 없으면 wiki 요약만으로 `supported`·`partially_supported`를 채우지 못한다(`ledger_errors`). 요약이 반대 방향이면 그대로 세어 `supported`를 막는다(숨기지 않는다). 보고서 근거 표는 `wiki 요약 (신선도)`로 적는다 | 막음 |
| 결과 계약 prompt 규칙(`RESULT_CONTRACT_FIELD_RULES`)과 결과 스키마(`access_status`에 `wiki_summary`, `wiki_freshness`) | 반영. 결과 스키마 hash를 의도적으로 갱신했다(계획 스키마·계획 prompt·`VALID_PLAN_SHA`는 그대로) |
| 단계 결과를 받을 때 감사 기록 대조(`orchestrator/cso.py`의 `save_salvage` → `wiki_contract.step_findings`) | 연결함: wiki를 인용한 행은 그 단계 workdir의 `outputs/wiki_queries.jsonl`과 대조한다. 기록에 없는 locator, 신선도·출처가 다른 행, 기록을 읽을 수 없거나 한 줄이라도 깨진 경우의 모든 wiki 행은 CP2에서 거부 행(`wiki audit: …`)이 된다. 그 행에서 파생된 행과 그 행에만 기대는 claim도 함께 빠진다(fail-closed) |
| 한계 | 감사 기록은 직원의 작업 폴더 안 파일이라 직원이 고쳐 쓸 수 있다(독립 출처 인증이 아니다). gateway가 workdir를 직접 읽을 수 없는 runner(다른 머신·WSL)면 읽을 수 없어 wiki 행이 모두 거부된다 |
| 독립 출처 인증, wiki 본문·경로 마스크 | 후속 |

## 한계

- 이 기록은 "wiki 도구를 쓸 수 있었다"이지 "복사됐다"가 아니다. `possible`이면 복사 여부와 무관하게 막는다.
- 결과 저장소 선택 카드는 막힌 요청에도 올리기를 제안하고, 올리려 할 때 거절한다(카드 표시는 아직 바꾸지 않았다).
- 이어 묻기(follow-up)는 같은 요청 안에서 돌아 같은 기록을 쓴다. 부모에서 새 요청을 여는 두 경로(CP1 수정 요청, 모델 거부 뒤 다시 시작)는 부모 수준을 물려받는다. 다른 경로로 요청을 만드는 코드가 생기면 이 규칙을 따라야 한다. request id가 없거나 알 수 없는 요청에 묶인 task는 기록할 곳이 없다.
