## 2026-10-02 · PR #323 — consult ref 격리(#86 #165)

- 결론: consult에는 staging 복사본만 보이고, 같은 질문도 ref가 다르면 cache를 재사용하지 않는다.
- 바뀐 것: runner가 source 경로를 manifest·prompt에서 지운다. ref는 workspace root부터 폴더 handle을 따라 상대 open하며, 검증 뒤 부모가 link·junction으로 바뀌면 제외한다.
- 실행한 것: 새 회귀 3건이 수정 전 실패하고 수정 후 통과했다. `tests/test_ask.py` 20건과 공개 검사를 통과했다.
- 해결: source 절대 경로 노출, ref 부모 TOCTOU, ref가 다른 ask의 cache 충돌을 막았다.
- 미해결: CI와 봇 재검토 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/adapters/held_dir.py`, `labhq/orchestrator/cso.py`, `labhq/runner/daemon.py`, `tests/test_ask.py`.
