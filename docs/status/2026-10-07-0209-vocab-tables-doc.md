## 2026-10-07 · 어휘·topic·점검표 표 (#453)

- 결론: PI 요청대로 topic이 무엇인지, topic별 점검표, 핵심 단어(어휘 키)를 표로 보는 md 하나(`docs/vocabulary.md`)를 README에서 링크했다.
- 바뀐 것: `scripts/vocab_tables.py`(원본 `labhq/vocab/`에서 생성, `--write`·`--check`), `docs/vocabulary.md`(topic 43·점검표 124항목·data 30·format 28·operation 22·공개 자원 21), `tests/test_vocab_tables.py`(어긋나면 실패), README·영문 README 링크, HANDOFF 작업 방식 한 줄.
- 실행한 것: `tests/test_vocab_tables.py`·`tests/test_integrations.py` 36 passed, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: PR #453.
