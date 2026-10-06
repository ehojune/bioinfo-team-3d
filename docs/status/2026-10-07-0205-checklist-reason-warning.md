## 2026-10-07 · #446 — 점검표: 가능하면 하고, 못 하면 이유와 경고 (#452)

- 결론: topic 점검표는 자료가 허락하는 점검을 하고, 못 한 점검은 `assumption: <못 한 이유>`로 답한다. 이유가 있는 생략은 받아들이고 항목마다 경고 `점검 못 함 <topic>/<id>: <이유>`를 한 번 띄운다. 이유가 비었거나 자리표시(`-`, `n/a`, `없음` 등)인 답은 누락과 같아 기존 교정 경로를 탄다. 승인 카드나 실행 중단은 없다.
- 바뀐 것: `labhq/vocab/topic_checklists.py`(자리표시 판정, `skipped`·`skip_warnings`, 계획 규칙 문구), `labhq/orchestrator/cso.py`(일반·연구·재계획 경로의 경고, `보고서 경고` 건수, 일반·연구 리뷰 프롬프트), `labhq/web/state.js`(피드 alert 한 줄씩), manual 점검표 절 끝 한 문단과 로드맵, HANDOFF PI 결정 2줄·#420 행. 연구 lane은 이유 있는 `assumption`을 전에도 통과시켰고 test로 확인했다. 경고는 동결 계획에 들어가 CP1 hash에 포함된다.
- 실행한 것: 바꾼 모듈을 import하는 test 파일 43개 1645 passed·4 skipped, 전체 suite 4197 passed·55 skipped, `node tests/web_checklist_skip.cjs`, `scripts/check_public.sh`.
- 미해결: 없음. manual의 기존 점검표 문단은 topic-papers PR과의 충돌을 줄이려고 손대지 않았다(새 문단은 절 끝).
- 근거: `tests/test_topic_checklists_precedents.py`, `tests/web_checklist_skip.cjs`.

### 리뷰 반영 (35f62e7)

- 재계획: `with_checklist_skip_warnings`가 옛 `점검 못 함` 경고를 지우고 현재 점검표 것만 붙인다. 이제 하는 점검의 경고가 남거나 이유가 바뀐 항목이 두 건으로 세어지지 않는다.
- 웹 피드: 같은 요청에서 이미 보인 생략 경고는 재계획 `request.plan`에서 다시 올리지 않는다.
- 재시작: #446 전에 CP1 승인된 연구 계획의 자리표시 이유(`not_applicable: none` 등)는 실패 대신 요청의 `checklist_reasonless`에 남긴다. 동결 계획은 hash 때문에 바꾸지 않는다.
- 실행한 것: 바꾼 모듈을 import하는 test 파일 44개 1704 passed·6 skipped, 전체 suite 4199 passed·55 skipped, `node tests/web_checklist_skip.cjs`. 새 test 셋은 옛 코드에서 실패함을 확인했다.
