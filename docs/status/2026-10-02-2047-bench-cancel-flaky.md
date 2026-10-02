## 2026-10-02 · Windows bench 취소 test 불안정 수정

- 결론: Windows가 `taskkill` 생성 중 `MemoryError`를 내도 이미 수집한 PID로 process tree를 정리한다.
- 바뀐 것: `_kill()`의 직접 종료 fallback에 Windows 자원 부족을 포함하고 해당 실패를 회귀 test에 주입했다.
- 실행한 것: 대상 test 30회 통과, `tests/test_bench.py` 55건 통과, 공개 저장소 검사 통과.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어받는다.
- 근거: `labhq/adapters/base.py`, `tests/test_bench.py`.
