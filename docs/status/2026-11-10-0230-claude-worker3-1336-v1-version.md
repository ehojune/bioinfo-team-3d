# 2026-11-10 claude-worker3 — #1336 r10·r11: 버전 1.0.0

## 결론
labhq 버전을 1.0.0으로 올리는 PR이다. #1331이 닫혀 v1.0을 선언할 때 병합하며, 그때까지 `hold`다.

## 바뀐 것
- `pyproject.toml`·`labhq/__init__.py`: `0.5.0` → `1.0.0`
- `README.md`·`README.en.md` 제목: `(v0.5)` → `(v1.0)`

## 실행한 것
- 버전을 언급하는 시험 40개 파일: 2212 passed. 실패 2건(`test_annot`, `test_annot_regulatory`)은 로컬 mcp 판본(`is_error`) 문제로 무관

## 미해결
- 병합 시점: #1331 종료
