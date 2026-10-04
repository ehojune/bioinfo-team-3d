## 2026-10-05 · #369 — pack 조합 표의 core PLAN field 칸도 schema로 검사

- 결론: PR #403 봇 P2. 조합 표에 core PLAN field(`brief.study_type`, `protocol.statistics.applicable` 등)가 들어가면 그 칸은 검사 없이 load됐다. PLAN schema에 없는 값(`"bogus"`, bool 자리의 `1`, list field의 문자열)은 어떤 PLAN과도 맞지 않는 행이다. 이제 load에서 거부한다.
- 바뀐 것: `plan_field_problem()`이 칸의 path를 `ResearchPlan` schema로 따라가 그 field type에 strict하게 맞는지 본다. `contract.py`가 `packs.py`를 import하므로 schema는 검사할 때 늦게 가져온다. 선택(`| None`) field의 `null`은 PLAN dump의 값과 같아 허용한다. 문서: `docs/research_protocol.md` 6절, 매뉴얼 연구 lane 절.
- 실행한 것: 새 test 6건 중 거부 5건이 옛 검사에서 실패하고 이 branch에서 통과했다. 남은 1건은 맞는 칸이 계속 load되는지 확인한다. 전체 pytest 3912 passed·54 skipped, `scripts/check_public.sh`.
- 봇 P2: Field 제약(`ge=1`, `min_length=1`)은 annotation이 아니라 metadata에 있어 함께 검사한다. `protocol.revision: 0`, `brief.subject: ""` test가 수정 전 실패, 수정 뒤 통과.
- 미해결: 없음.
- 근거: `labhq/research/packs.py`, `tests/test_research_bulk_pack.py`.
