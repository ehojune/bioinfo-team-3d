# 단계 실패 재계획과 선언 산출 규칙

- 결론: 일반 단계와 리뷰 수정 단계의 실패를 기본 1회 재계획하며, 모든 대체 경로는 같은 선언 파일명에 씁니다.
- 바뀐 것: `max_failure_replans`와 이전 설정 호환, 경로 독립 파일명·척도 및 출처 기록 규칙, 리뷰 수정 실패 복구.
- 실행한 것: 봇 지적 회귀는 수정 전 2 failed·1 passed, 수정 뒤 관련 pytest 242 passed. 공개·패치노트·목차·diff 검사 통과.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`, `labhq/settings.py`, `tests/test_cso.py`, `tests/test_output_types.py`, `tests/test_output_types_research.py`, `docs/manual.md`.
