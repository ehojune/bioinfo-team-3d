# #1238 rev3 — ci-reuse.md의 진단 설명을 맞춤

| 항목 | 내용 |
|---|---|
| 결론 | `docs/reference/ci-reuse.md` 57행에서 task stack을 빼고 faulthandler 스레드 stack만 남는다고 적었다 |
| 바뀐 것 | 문서 한 줄, patch note. 코드는 그대로 |
| 실행한 것 | `patch_notes.py check`, `check_public.sh` |
| 미해결 | 없음 |
| 근거 | https://github.com/ehojune/bioinfo-team-3d/pull/1240 |
