## 2026-10-02 · #295 — 목차 갱신 workflow 직렬화(#292 후속)

- 결론: 병합이 겹쳐도 목차 갱신이 최신 기록을 빠뜨리지 않는다. 한 번에 하나만 돌고, 최신 main에서 만들며, push가 실패하면 다시 만든다.
- 바뀐 것: `.github/workflows/notes-index.yml`(concurrency, `ref: main`, 재시도 3번).
- 실행한 것: workflow YAML 확인. 실제 동작은 병합 뒤 main에서 확인한다.
- 미해결: 없음.
- 근거: `.github/workflows/notes-index.yml`.
