## 결론

Refs #586 (3번). 10-13 PI 시운전에서 리뷰어 두 명이 점검표와 계획의 답을 받고도 점검 항목을 근거로 든 지적이 0건이었고, 보고서에는 점검 항목 이름이 나오지 않았습니다. 이제 리뷰어가 항목마다 했다·일부·안 했다와 근거를 답하고, 계획이 단계로 답한 항목을 못 했다고 판정하면 P2 지적이 생기며, 실행 기록에 점검표가 붙습니다. #586의 1·2번은 이 PR 범위가 아닙니다.

## 바뀐 것

- **리뷰 프롬프트** (`REVIEW_PROMPT`, `RESEARCH_REVIEW_PROMPT`): 선언한 점검 항목과 필수 선행 기준(`precedent.<n>`)마다 `checklist` 한 줄(`id`, `status` done·partly·not_done, `evidence` 단계 id나 산출 경로)을 답하게 합니다. 계획의 답이 아니라 결과를 보고 판정하게 합니다.
- **schema**: 일반 리뷰(`REVIEW_SCHEMA`)와 연구 리뷰(`RESEARCH_LANE_REVIEW_SCHEMA`)에 선택 필드 `checklist`만 더했습니다. 없는 리뷰와 옛 저장 리뷰는 그대로 유효하고, 읽을 수 없는 줄은 리뷰 전체가 아니라 그 필드만 버립니다(`lenient_review`). 연구 lane은 재시작 때 저장 리뷰를 다시 읽을 때도 이 줄을 유지합니다.
- **P2 지적**: 계획이 `step:<id>`로 답한 항목을 partly·not_done으로 판정하면 `review_with_checklist`가 그 단계에 P2 지적을 더합니다. 그 id를 이름으로 든 P1·P2 지적이 이미 있으면 더하지 않고, P3는 대신하지 못합니다. `assumption`·`not_applicable` 답은 지적을 더하지 않습니다. P2라 verdict는 바뀌지 않고 보고서 `리뷰 참고`에 한 줄로 실립니다. 여러 topic이 같은 id를 쓰면 담당 단계를 모두 적고 첫 단계에 답니다.
- **점검표**: 실행 기록 부록(`## 부록: 실행 기록`)에 항목·담당 단계·리뷰 판정·근거 표를 붙입니다. `_finish` 한 곳에서 만들어 일반·단독·연구 lane이 같은 표를 얻습니다. 요청 묶음은 `report_appendix`를 그대로 옮기므로 따로 고칠 곳이 없었습니다. `assumption` 답은 `가정`, `not_applicable`은 `해당 없음`(판정 `-`)이고, 리뷰어 답이 없으면 판정은 `-`입니다.
- **프롬프트 해시 pin** (`tests/test_output_types*.py`): 바뀌지 않았습니다. 이 PR은 계획 프롬프트와 계획 schema를 건드리지 않아서 pin된 해시가 그대로 통과합니다. 일부러 갱신할 변화가 없어 주석도 달지 않았습니다.
- **기존 test 두 개를 고쳤습니다**: `test_already_strict_schema_is_unchanged`와 `test_research_review_schema_is_strict`는 연구 리뷰 schema의 모든 필드가 필수라고 가정했습니다. 선택 필드 하나를 예외로 두도록 바꾸고(필수 목록은 그 필드만 빼고 같아야 함), 전송용 복사본이 그 필드만 nullable로 만드는지 확인하는 test를 더했습니다.
- `docs/manual.md`의 점검표 절에 리뷰어 답·P2·표를 한 단락 적었습니다.

## 확인

- 새 `tests/test_checklist_review.py` 19건(프롬프트, 두 schema, P2 규칙의 경계, 표, 일반·연구 end-to-end와 재시작). main의 소스로 돌리면 19건이 모두 실패하고 이 branch에서는 통과합니다.
- 관련 pytest(cso·research·solo·report·review·checklist·output_types·strict schema·bundle·semantics) 2154 passed, 4 skipped. `for f in tests/*.cjs; do node --test "$f"; done` 57개 파일 모두 통과. 전체 pytest는 CI에 맡깁니다.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과(PR #600).
- 작업은 scratch clone의 별도 venv에서만 했고 PI 인스턴스·8787·55921 포트·`~/.labhq`는 쓰지 않았습니다.

## 남은 지적

- 단독 처리(solo) 경로의 end-to-end test는 없습니다. 세 리뷰 호출부가 같은 함수를 쓰고 기존 solo test가 그 경로를 지나가며, 단위 test가 함수를 덮습니다.
- id가 영어 단어와 겹치면(`batch`) 그 단어를 쓴 P1·P2 지적을 대신으로 보고 지적을 더하지 않습니다.
