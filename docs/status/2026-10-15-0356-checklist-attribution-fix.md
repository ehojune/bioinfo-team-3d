## 결론

Refs #586. 병합된 PR #600의 봇 지적 둘(P2)을 고칩니다. 단독 처리 결과의 리뷰 판정이 실행되지 않은 계획 단계 A에 붙던 것과, 같은 점검 id를 topic별로 다르게 답했을 때 리뷰어 판정이 bare id 하나로 합쳐지던 것입니다.

## 바뀐 것

- **단독 처리(`solo_review: true`)**: 선택은 "단독 결과에도 P2를 달되 `단독 처리`에 귀속"입니다. 단독 경로에서는 계획 단계가 실행되지 않고 직접 처리 결과 하나가 리뷰 대상이라, 점검 miss의 P2는 `step_id`가 `단독 처리`이고 요청 문구도 "단독 처리 결과에서 …를 끝내거나 한계에 남기세요"입니다. 실행 기록 점검표의 담당 단계 칸도 `단독 처리`입니다(`assumption`은 `가정`, `not_applicable`은 `해당 없음` 그대로). 리뷰어 없이 단독으로 끝난 요청도 같은 표시입니다. 팀 경로가 단독에서 넘어온 경우(`fallback`)는 단계가 실제로 실행됐으므로 단계 id를 그대로 씁니다.
  - P2를 남긴 이유: P2는 verdict를 바꾸지 않고 `리뷰 참고`에 한 줄로만 실려서 팀 경로와 PI가 보는 양이 같습니다. 판정만 표에 두면 단독 경로에서는 `못 했다`가 `리뷰 참고`에 안 보입니다. `단독 처리`는 계획 단계 id가 아니라서 웹의 단계 카드(`state.js`가 `issue.step_id === step.id`로 붙임)에도 달리지 않습니다.
- **topic별 답을 가진 공유 id**: 리뷰어 줄을 `(id, topic)`으로 따로 보관합니다. `topic.id`·`topic/id` 줄은 그 topic의 답에만, bare `id` 줄은 자기 줄이 없는 topic 전체에 하나의 판정(단계는 모두 적고 첫 단계에 답)으로 적용합니다. 계획이 bare id 하나로 답했으면 답이 하나이므로 예전처럼 가장 나쁜 판정을 씁니다.
  - miss는 같은 topic의 `step:` 답에만 연결됩니다. `bulk_rna_seq.batch=step:S5`, `single_cell_rna_seq.batch=step:S4`(10-13 시운전 모양)에서 `single_cell_rna_seq.batch=not_done`이면 S4에만 P2가 달리고 S5는 영향이 없습니다. 한쪽이 `assumption:`이면 그 판정은 `step:` 답에 miss를 만들지 않습니다.
  - 점검표는 topic 답마다 한 줄입니다(`bulk_rna_seq/batch | S5 | 했다`, `single_cell_rna_seq/batch | S4 | 안 했다`).
  - 같은 부류라 함께 닫았습니다: 한 topic의 `topic/id`를 이름으로 든 P1·P2 지적이 다른 topic의 같은 id miss를 대신해 지적을 막던 것(`_names_check`). 이제 지적이 가리키는 topic의 miss만 대신합니다.
- `docs/manual.md` 점검표 절에 두 동작을 적었습니다.

## 확인

- 새 test 9건(`tests/test_checklist_review.py`): topic별 판정(둘 다 step, step과 assumption, bare 줄과 topic 줄 혼합, bare 답은 여전히 합침), 지적 대체의 topic 경계, 표의 alias별 줄, 단독 단위 test, 단독 end-to-end(리뷰 있음·없음). main의 소스로 돌리면 8건이 실패하고(나머지 1건은 바뀌지 않은 동작의 보호용) 이 branch에서는 모두 통과합니다. #600이 남긴 "단독 경로 end-to-end test 없음"도 이걸로 채웠습니다.
- 기존 test는 고치거나 약하게 만들지 않았습니다(`test_a_shared_id_names_every_owning_step_and_files_the_issue_on_the_first` 포함).
- 관련 pytest(cso·research·solo·report·review·checklist·output_types·strict schema·bundle·semantics 52개 파일) 2187 passed, 4 skipped. `for f in tests/*.cjs; do node --test "$f"; done` 58개 파일 모두 통과. 전체 pytest는 CI에 맡깁니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 단독 처리 보고서의 실행 기록 "Step status and output paths"는 여전히 실행되지 않은 계획 단계를 `A: not run`으로 적습니다. 점검표와 다른 절이라 이 PR에서는 손대지 않았습니다.
- 단독 리뷰어에게 주는 점검 문맥(`plan_review_context`)의 "Answers"는 계획의 `step:A` 형태 그대로입니다. 리뷰어 프롬프트는 바꾸지 않았습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
