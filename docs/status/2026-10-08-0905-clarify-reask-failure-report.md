## 2026-10-08 · clarify 재질문과 실패 보고 원인을 보강 (PI 점검 R6 R12)

- 결론: 답한 질문은 정규화한 본문이나 stable id가 같으면 다시 묻지 않는다. 새 질문만 두 번째 카드로 보내며, 요청당 카드 2장을 넘으면 남은 질문을 한국어 보고서에 적고 멈춘다.
- 바뀐 것: 일반·연구 계획과 실패 재계획이 같은 질문 필터를 쓴다. 단계 실패 보고서 첫머리는 단계·담당·가린 원인·PI 조치를 세 줄로 보여 주고, 서버가 웹 feed용 실패 이유도 보낸다.
- 실행한 것: 관련 pytest 286 passed, 보존 확인 14 passed. Windows 전체 pytest 4752 passed·59 skipped. `scripts/check_public.sh` 통과.
- 미해결: 없음.
- 근거: `tests/test_intake_questions.py`, `tests/test_cso.py`, `tests/test_research_cp2.py`.
