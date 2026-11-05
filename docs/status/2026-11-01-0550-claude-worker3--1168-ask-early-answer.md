# #1168 rev6 — request_ask가 넘기는 중에 온 답을 버리던 문제

| 항목 | 내용 |
|---|---|
| 결론 | `request_ask`가 `_on_ask` 직후 future가 끝났으면 그 답으로 마친다. `_on_ask`가 실패하면 `pending_asks`를 지운다 |
| 바뀐 것 | `labhq/runner/approvals.py`, `tests/test_ask_early_answer_1168.py`, 패치노트 |
| 실행한 것 | 새 test 4건(수정 전 hibernate·실패 경로 2건 실패 확인), `test_ask.py`·`test_ask_policy.py`·`test_ask_review3.py` 포함 78건 통과 |
| 미해결 | 없음. 실제 gateway와의 경합은 가짜 `_on_ask`로만 재현했다 |
| 근거 | https://github.com/ehojune/bioinfo-team-3d/issues/1168 |
