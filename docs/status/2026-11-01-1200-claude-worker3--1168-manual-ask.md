# #1168 rev8 — 병합한 PR의 남은 지적 두 건을 manual에 반영

| 항목 | 내용 |
|---|---|
| 결론 | PR #1231·#1236이 "남은 지적"으로 남긴 manual 두 건을 한·영에 넣었다 |
| 바뀐 것 | `docs/manual.md`·`docs/manual.en.md`(ask 조기 답 즉시 반환 한 문장, dash 입력 예시의 `trap`), patch note. 코드는 그대로 |
| 실행한 것 | `patch_notes.py check`, `check_public.sh`, `test_text_encoding.py` |
| 미해결 | `trap` 예시는 dash에서 실제로 돌려 보지 않았다 |
| 근거 | https://github.com/ehojune/bioinfo-team-3d/pull/1231, https://github.com/ehojune/bioinfo-team-3d/pull/1236 |
