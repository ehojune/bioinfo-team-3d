## 2026-10-03 · Codex 직원 output schema strict 변환

- 결론: Codex 직원에게 넘기는 모든 output schema를 OpenAI strict 형식으로 복사해 연구 lane의 시작 즉시 400을 막았다. 응답의 transport용 optional `null`은 원래 schema에 따라 지운 뒤 기존 검증으로 보낸다.
- 바뀐 것: object의 모든 property를 required로 만들고 optional field는 nullable로 바꾸며 `additionalProperties: false`를 고정했다. 원본 schema와 hash, Claude 경로는 그대로다.
- 실행한 것: 새 test는 수정 전 collection 실패, 수정 후 관련 pytest 201건 통과·1건 skip. codex-cli 0.159.0-alpha.12.1과 gpt-5.6-luna 실측은 400 없이 JSON을 반환했고, `blocking_decision: null` 정리 뒤 `ResearchResult` 검증을 통과했다. 공개 저장소 검사와 diff 검사도 통과했다.
- 미해결: 없음.
- 근거: `labhq/util.py`, `labhq/adapters/codex.py`, `tests/test_openai_strict_schema.py`.
