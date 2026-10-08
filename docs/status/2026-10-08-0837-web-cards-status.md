## 2026-10-08 · #494 — 웹 결정 카드와 요청 상태 정리 (PI 점검 R10 R14 R18 R19 R21)

- 결론: PI가 내일 헷갈릴 웹 화면 다섯 가지를 고쳤다. gateway 재시작 뒤 요청이 '실패'로 보이지 않고, 재개를 승인하면 새로고침 없이 상태가 바뀐다. CP2 카드는 claim 표로 읽히고, CP2 '수정 요청'이 요청을 끝낸다는 것이 버튼과 카드에 보인다.
- 바뀐 것:

  | 항목 | 화면 |
  |---|---|
  | R14 | 요청 상태 라벨 10개(중단됨·러너 기다림·취소됨·거부됨 포함, 모르는 상태는 그대로 표시). 재개 카드 승인과 `request.resume_waiting`·`resumed`·`resume_timeout`을 reducer가 반영한다: 러너 기다림(빠진 직원) → 진행 중, 초과면 중단됨. 새 재개 카드가 뜨면 그 요청을 중단됨으로 표시한다. 2.5D·3D 공통 |
  | R18 | CP2: claim별 표(단계·claim·상태·근거 종류·표시), 원장 JSON·산출 hash는 접는다. CP2 summary 한국어(`cso.py` 그 한 줄). CP1: raw key 중복 제거, 단계 목록(id·직원·지시 앞부분·산출) 펼침, protocol 접음, 가설은 JSON 대신 글 |
  | R10 | CP2 버튼 `수정 요청(요청 끝남)`, 카드 안내 한 줄. manual·research_protocol의 revise 경로 문구 정정 |
  | R19 | KIND_KO에 resume·question. 재개 카드 step id join, countdown 없음. 거절 결과 한 줄(재개·CP1·CP2·직원 질문). 직원 질문 카드는 선택지를 버튼으로 그리고(하나뿐이어도) 선택을 요구한다. 결정 카드 첫 줄에 요청 문장(3D에도 requests 전달) |
  | R21 | alert 로그에 toast. 한도 대기 카드에 `MM-DD HH:MM` 재개와 최대 기한. manual에 `quota_default_wait_s`·`quota_max_wait_s`와 `지금 재개` 범위 |

- 실행한 것: trial `req_7ccde78be0`의 CP1·CP2 카드를 gateway DB에서 read-only로 꺼내 줄인 fixture로 CP2 22행·CP1 단계 12개를 확인했다. 새 node 시험 12건(`tests/web_cards_status.cjs`), CP2 summary 단언 1건. 전체 pytest(Windows, 1d8c778) 4739 passed·59 skipped. 리뷰 반영(61e4e54): 선택지가 하나인 직원 질문도 버튼과 선택 강제. 회귀 시험 1건은 고치기 전 코드에서 실패했고, 그 뒤 node 시험 전부와 `tests/test_web*`·`tests/test_lab3d.py` 73 passed·1 skipped. 375px 정적 미리보기에서 가로 넘침 없음. Codex 로컬 리뷰 지적 0건. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결(서버 쪽이 필요한 것):
  - gateway가 재시작 때 요청을 interrupted로 바꿔도 status event가 없다. 열린 페이지는 새 재개 카드가 뜰 때만 알고, 이전 재개 카드가 남아 있던 요청은 새로고침해야 안다. status event나 boot id가 필요하다(`gateway/server.py`).
  - snapshot `step_details`에 quota `deadline_at`이 없어 새로고침 뒤에는 최대 기한이 빠진다(`request_step_details`).
  - UAC 대기 전용 event(시작·해제)가 없어 `#engine-holds` 행은 만들지 않았다. alert toast로 대신한다(`adapters/base.py`).
  - 재개 summary의 Python list repr(`server.py` `new_resume_approval`), CP1 summary·거절 문구 English(`cso.py`)는 웹에서만 다듬었다.
  - CP2 수정 요청 → 이어 가기 흐름, `labhq send/watch`의 quota 표시는 범위 밖이다.
- 근거: `tests/web_cards_status.cjs`, `tests/fixtures/research_cards_req_7ccde78be0.json`, `tests/test_research_cp2.py::test_cp2_refuses_evidence_whose_artifact_was_not_collected`.
