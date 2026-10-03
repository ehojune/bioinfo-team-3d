## 2026-10-04 · #58 ①④ — 일반 단계 결과 계약과 보고서 경고

- 결론: 일반 단계 답을 네 블록으로 보존하고, 모으지 않은 Evidence 경로와 실패한 도구 호출은 요청을 거부하지 않고 최종 보고서 경고로 올린다.
- 바뀐 것: 일반 STEP·직원 규칙, `TaskResult` 파싱 필드, runner의 도구 오류 첫 줄 수집, SYNTH·`_finish` 경고 절. 연구 result v2·CP2 프롬프트는 그대로다.
- 실행한 것: 새 회귀는 수정 전 import 실패. 수정 뒤 관련 pytest 417 passed·1 skipped, 공개 저장소·목차·diff 검사 통과.
- 미해결: #58의 tool_use_id와 웹 감사 번들 다운로드는 다음 PR 범위다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/runner/daemon.py`, `labhq/models.py`, `tests/test_general_evidence.py`.
