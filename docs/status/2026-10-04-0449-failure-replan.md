# 단계 실패 재계획 상한 분리와 선언 산출 규칙

- 결론: 일반 단계 실패는 기본 1회 재계획하고, 리뷰 revise 재계획은 기존처럼 기본 꺼짐입니다.
- 바뀐 것: `max_failure_replans`와 이전 설정 호환, 경로마다 달라지는 산출 선언 규칙, 실패 단계 결과를 읽는 FakeHub 회귀 테스트.
- 실행한 것: 수정 전 5 failed, 수정 뒤 관련 pytest 315 passed. 공개·diff 검사 통과.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`, `labhq/settings.py`, `tests/test_cso.py`, `tests/test_output_types.py`, `docs/manual.md`.
