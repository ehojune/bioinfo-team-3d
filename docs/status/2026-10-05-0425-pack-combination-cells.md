## 2026-10-05 · #369 — pack 조합 표의 칸을 PLAN 답과 같은 규칙으로 검사

- 결론: pack의 `allowed_combinations` 칸이 field의 type·`minimum`·`pattern`을 어기면 그 행은 어떤 PLAN도 통과할 수 없다. 이제 load에서 거부한다. PR #366 봇 P2(#369 남은 지적)다.
- 바뀐 것: `labhq/research/packs.py`에 `field_value_problem()`을 두어 PLAN 답 검사(`contract.py`)와 조합 표 칸 검사가 같은 판정을 쓴다. 전에는 칸을 `allowed_values`로만 봤다. 필수 field 칸의 `null`도 거부한다. 내장 pack 내용과 hash는 그대로다. `docs/research_protocol.md` 6절.
- 실행한 것: 새 test 8건 중 거부 7건이 옛 검사에서 실패하고 이 branch에서 통과했다. 통과 1건은 맞는 칸이 계속 load되는지 확인한다. PLAN 답 오류 문구는 그대로라 기존 test 기대값을 바꾸지 않았다. 전체 pytest 3905 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: #369 1번(적용 pack의 `not_applicable` 우회)은 #390에서 topic 판정으로 닫혔다. 2번 `pairing_evidence` 닫힌 선택지는 pack version을 올려야 해 이 PR에 넣지 않았다.
- 근거: `labhq/research/packs.py`, `labhq/research/contract.py`, `tests/test_research_bulk_pack.py`.
