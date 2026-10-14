## 결론

Refs #586. PR #602의 봇 지적(P2, 인라인 4275011097)을 고칩니다. #602가 topic별 리뷰어 판정을 `(id, topic)`으로 보관했지만, 실제 리뷰 문맥(`plan_review_context`)은 공유 id를 `- batch:` 한 줄로만 싣고 규칙은 "나열된 id 그대로" 답하게 했습니다. 정상 리뷰어는 bare `batch` 한 줄만 돌려주고, 그 판정이 모든 topic 칸에 복사되어 한 topic의 `not_done`이 완료된 다른 topic의 단계에도 P2로 달렸습니다. 이제 리뷰어에게 topic별 id를 보여주고 그 id로 답하게 합니다.

## 바뀐 것

- **리뷰 문맥** (`plan_review_context`, 일반 리뷰·연구 리뷰·단독 처리·단독 리뷰 네 호출부가 같은 함수): 계획이 공유 id를 topic별로 답했으면 topic마다 한 줄입니다.
  `- bulk_rna_seq.batch: <그 topic의 점검 내용> Why: <이유> — 계획 답: step:S5`
  `- single_cell_rna_seq.batch: <그 topic의 점검 내용> Why: <이유> — 계획 답: step:S4`
  합쳐 이은 점검 내용(` / `) 대신 topic 자신의 내용과 이유를 씁니다. 새 함수 `topic_checklists.review_entries`가 줄을 만들고, 읽는 쪽(`check_slots`)과 같은 칸 규칙을 씁니다.
- **bare id 유지**: 공유하지 않는 점검, 한 topic만 선언한 점검, 계획이 bare id 하나로 답한 공유 id, 선행 기준(`precedent.<n>`)은 예전과 같은 `- <id>: <점검> Why: <이유>` 한 줄입니다.
- **리뷰 규칙** (`CHECKLIST_REVIEW_RULE`, 일반·연구 리뷰 프롬프트 공통): "나열된 id 그대로, `topic.id`로 나열된 점검은 나열된 id마다 한 줄씩" 답하게 한 문장을 더했습니다.
- `docs/manual.md` 점검표 절의 해당 문장을 고쳤습니다.
- **프롬프트 해시 pin**: 갱신할 것이 없었습니다. pin된 것은 계획 프롬프트(`test_output_types.py`), 연구 계획 프롬프트(`test_output_types_research.py`), 단계 프롬프트(`test_step_prompt_rules.py`, `test_general_evidence.py`)이고 리뷰 프롬프트는 pin되어 있지 않습니다. 이 PR은 계획·단계 프롬프트를 건드리지 않으며, 그 test들이 그대로 통과합니다.

## 확인

- 새 test 6건(`tests/test_checklist_review.py`):
  - 문맥이 공유 id를 topic별로 싣고 topic 자신의 점검 내용과 계획 답을 붙이는지(`step`+`step`, `step`+`assumption`)
  - 공유하지 않는 점검과 bare 답은 bare id 한 줄인지
  - 두 리뷰 프롬프트가 규칙 문장을 담는지
  - 프롬프트에 나열된 id만 정확히 답하는 가짜 리뷰어가 topic별 판정을 얻고 한 topic의 `not_done`이 다른 topic 단계에 P2를 만들지 않는지(10-13 시운전의 `batch` S4·S5 모양)
  - 같은 가짜 리뷰어를 팀 경로 `run_request` 끝까지 돌려 프롬프트 조립, 파싱, P2, 실행 기록 점검표를 확인하는 end-to-end test. 이 test는 `S4`에만 P2가 달리고 표가 `bulk_rna_seq/batch | S5 | 했다`, `single_cell_rna_seq/batch | S4 | 안 했다`인지 봅니다.
  main의 소스로 돌리면 5건이 실패하고(나머지 1건은 바뀌지 않은 bare 동작의 보호용) 이 branch에서는 통과합니다. 기존 test는 고치지 않았습니다.
- 관련 pytest(cso·research·solo·report·review·checklist·output_types·strict schema·bundle·semantics·step prompt·general evidence 54개 파일) 2218 passed, 4 skipped. `for f in tests/*.cjs; do node --test "$f"; done` 58개 파일 모두 통과. 전체 pytest는 CI에 맡깁니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 리뷰어가 나열된 `topic.id`를 무시하고 bare `batch` 한 줄만 돌려주면 #602대로 그 줄을 자기 줄이 없는 topic 전체의 한 판정으로 보고, miss 한 건에 두 단계를 모두 적습니다. topic을 가를 근거가 없어서 그렇게 두었습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
