# 보고서 실행 기록 부록과 분석 재현성 (#373 벤치 A)

## 단계
P1 — 눈가림 채점에서 확인된 읽기 쉬움·재현성 열세를 고쳤습니다.

## 한 일
- 일반·연구 보고서 본문을 결론과 권고 → 결과 → 방법 요약 → 한계 순서로 안내합니다.
- 단계 상태·경고·승인·리뷰·claim check는 한 개의 `부록: 실행 기록`에 모읍니다.
- 분석 script와 결과를 좌우한 reference는 `outputs/`에 선언·보존하고, 방법에 seed·버전을 적게 했습니다.
- prompt 고정 hash는 위 재현성 규칙 추가를 이유로 갱신했습니다.

## 테스트 결과
- [x] 선행 회귀 5 failed → 수정 뒤 통과
- [x] 관련 test 238 passed
- [x] `pytest -q`: 3716 passed, 53 skipped
- [x] Node CJS 18개 통과
- [x] `scripts/check_public.sh` 통과
- [x] UI 변경 없음

## 바꾼 파일
`labhq/orchestrator/cso.py`, `tests/test_cso.py`, `tests/test_general_evidence.py`, `tests/test_output_types.py`, `tests/test_output_types_research.py`, `tests/test_research_report.py`, `tests/test_step_prompt_rules.py`, `docs/manual.md`

## 막힌 점
없음.

## PI·Claude에게 물을 것
없음.
