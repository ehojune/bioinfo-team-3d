## 2026-10-06 · #420 — 새 topic 23개 점검표

- 결론: #420으로 채택한 topic 23개에 점검표 65항목을 넣었다(전체 43 topic·125항목). 리뷰 지적대로 계획 프롬프트에는 점검표 대신 작업 폴더 `topic_checklists.tsv` 이름만 싣는다(점검표 부분 10,400자 → 643자). 병합 전에 PI가 항목을 검토한다.
- 바뀐 것: `labhq/vocab/topic_checklists.yaml`, `docs/reference/topic_checklists_sources.md`(topic별 근거 한 줄), `topic_checklists.py`(TSV·짧은 규칙·누락 오류에 점검 내용), `cso.py`(계획·재계획 Task에 TSV), 러너 `workspace.py`·`daemon.py`(TASK.md 전에 TSV 쓰기), 점검표 test, README·README.en·manual.
- 실행한 것: 근거 PMID 87개 esummary 제목 대조(87 ok), 점검표·공개 자원 test 27 passed, 점검표·작업 폴더 관련 test 13개 파일 253 passed·32 skipped, 전체 suite 4010 passed·54 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 낡은 러너는 TSV를 쓰지 않지만, 그때도 답 누락 고침 요청이 점검 내용을 담는다.
- 근거: #420, PR #440, PR #446.
