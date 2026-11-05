## 단계
#1099(#1087 리뷰 P2): `route: solo` prompt에 스크립트 선언 규칙.

## 한 일
1. `labhq/orchestrator/cso.py` `SOLO_PROMPT`의 "Save analysis code under outputs/scripts" 줄 뒤에 `SCRIPT_OUTPUTS_PLAN_RULE` 한 줄.
2. `tests/test_output_types.py`: 규칙이 `SOLO_PROMPT`와 request·assumptions를 채운 prompt에 있는지 단언(규칙을 빼면 실패함을 확인).

## 확인
- `tests/test_output_types.py`·`test_output_types_research.py`·`test_report_figures_svg.py`·`test_report_paper_format.py`·`test_topic_checklists_precedents.py` 121 passed.
- 계획 prompt의 고정 hash 시험은 solo prompt를 포함하지 않는다(영향 없음).

## 하지 않은 것
- 실제 solo 요청에서 에이전트가 스크립트를 선언하는지는 보지 못했다.
