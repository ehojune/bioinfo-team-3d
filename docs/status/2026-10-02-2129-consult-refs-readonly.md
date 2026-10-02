## 2026-10-02 · PR #323 — consult ref 격리(#86 #165)

- 결론: 같은 runner의 consult도 source workspace를 열지 않고, 검증한 ref 파일의 복사본만 읽는다.
- 바뀐 것: 폴더·링크 ref는 거부한다. 파일은 `refs/consult-<task>/ref-NN.<ext>`로 이름을 바꿔 복사하며, 파일당 8 MiB·전체 32 MiB를 넘으면 제외 사유를 prompt에 남긴다.
- 실행한 것: 새 회귀는 수정 전 5 failed·1 passed, 수정 후 6 passed. 관련 pytest 48 passed·1 skipped, 공개 검사도 통과했다.
- 해결: source의 `CLAUDE.md`·`.claude/rules/`와 선언하지 않은 파일은 consult에 노출되지 않는다.
- 미해결: CI와 봇 재검토 판정은 개발 총괄이 이어받는다.
- 근거: `labhq/runner/daemon.py`, `tests/test_ask.py`, `tests/test_read_only_followups.py`.
