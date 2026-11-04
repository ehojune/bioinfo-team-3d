## 결론

오늘 19:00 PI 시운전 전에 웹 결함 4건을 고쳤다. 새 회귀 test가 네 건 모두 main 코드에서 실패하고 이 branch에서 통과한다.

## 바뀐 것

1. 직접 맡김 표시: 직접 맡긴 요청은 "처리: 직접 맡김"으로 보인다. 경로 결정 없이 끝난 요청(계획 단계 실패 등)은 처리 표시를 뺀다. 3D 요청 행은 영어 phase 대신 2.5D와 같은 한글 단계 이름을 쓰고, 끝난 요청은 상태(완료·실패)만 보인다. 라벨은 `state.js`(`processingLabel`·`phaseLabel`) 한 곳이다.
2. CP1 대기 단계: `request.plan`은 phase를 `plan`으로 두고, `step_id`가 있는 dispatch가 `execute`로 넘긴다. 스냅숏은 리뷰가 없고 시작한 단계도 없으면 `plan`으로 읽는다.
3. 직접 요청 중 보내기: `send()`가 `renderComposerMode`와 같은 `mode !== 'direct'` 검사를 한다. 직접 맡긴 요청을 고른 채 보내기를 누르면 새 요청으로 간다(전에는 메모를 보내 409).
4. 연구 종료 사유: `report_incomplete`, 리뷰 revise·해석 실패, 보고서 작성 실패, 예산 거부, CP2 거절·수정 요청·시간 초과·판독 불가에 사유 한 줄(`cso.py`의 `RESEARCH_END_ERRORS`)을 붙인다. 사유가 비면 `state.js`가 중립 문구를 보인다.
5. `docs/manual.md`의 화이트보드·직접 요청 보내기·연구 종료 사유 설명을 맞췄다. `web_state_hashes.json`은 request.plan 직후 phase가 바뀐 한 항목(인덱스 5)만 갱신했고, `web_solo_route.cjs`는 state.js로 옮겨 간 라벨을 동작으로 확인하게 고쳤다.

## 실행한 것

- `tests/web_trial_ready.cjs`(CI 목록 `tests/test_web_3d.py`에 추가): 네 결함 모두 main 코드에서 실패, 이 branch에서 통과. 3D 요청 행은 live.js를 그대로 불러 DOM stub로, `send()`는 index.html에서 잘라 vm으로 실행한다.
- pytest 단언 3곳 추가(`test_research_report.py` 2곳, `test_research_protocol.py` CP2 표): main의 `cso.py`에서 실패, 이 branch에서 통과.
- Node test 33개 전부 통과. 전체 pytest 통과(exit 0, 8분 42초). `scripts/check_public.sh`, `git diff --check` 통과.
- index.html을 정적 서버로 띄워 demo 모드가 콘솔 오류 없이 그려지는 것까지 확인했다. 요청 흐름은 Node harness로만 확인했다.

## 미해결

- 3D 화이트보드(`lab3d/src/office.js`)의 `status · phase` 줄은 영어 키 그대로다(이번 점검 항목 밖).
- 리뷰 revise 뒤 새 CP1을 기다리는 중에 새로고침하면 스냅숏에 리뷰가 남아 `execute`로 읽는다(실시간 경로는 `plan`).
- 연구가 아닌 `ok=False` 종료 중 사유를 주지 않는 경로(브리핑 실패 등)는 중립 문구가 나온다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
