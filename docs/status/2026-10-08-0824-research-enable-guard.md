## 2026-10-08 · 연구 lane이 끝까지 도는지 doctor가 알리고, research 오타 키를 오류로 (PI 점검 R1 R25)

- 결론: PI 시운전 전 점검(R1)에서 PI 설정에 `research` 블록이 없어 v0.5 연구 lane이 돌지 않는 것을 찾았다. 문서는 `enabled: true` 한 줄로 켠다고 했지만, 그러면 CP1 승인 뒤 단계를 실행하지 않고 끝난다. `labhq doctor`에 `research lane` 행(꺼짐 / CP1에서 멈춤 warn / 끝까지 감)을 넣고, `research` 아래 오타 키는 시작할 때 오류로 한다. `docs/research_protocol.md`와 예시 설정은 `enabled`·`evidence_checkpoint`·`active_packs` 세 키 형태로 고쳤다. R25: `docs/pi-qa.md`의 옛 창구(#298 → #435), #402 상태(병합됨), Codex 직원 보호 설명을 매뉴얼과 맞추고, 폰 접속 보류를 적었다.
- 바뀐 것: `labhq/doctor.py`(`_research_row`), `labhq/settings.py`(`ResearchSettings` extra forbid), `docs/research_protocol.md`, `config/labhq.example.yaml`과 패키지 사본, `docs/manual.md` doctor 절 한 줄, `docs/pi-qa.md`, `tests/test_doctor.py` 2건(4 case).
- 실행한 것: 관련 시험 99 passed. 전체 pytest(Windows)는 그림자 hook 표시 시험 1건만 실패(doctor가 semantics를 읽은 줄) → 그 부분을 빼고 그 파일 포함 관련 시험 재실행 통과. 나머지 4742 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: PI 설정에 실제로 켜는 일은 PI 결정 3(#435)을 따른다.
- 근거: `tests/test_doctor.py::test_doctor_says_whether_the_research_lane_runs_end_to_end`, `::test_a_misspelled_research_key_is_an_error_not_a_silent_default`.
