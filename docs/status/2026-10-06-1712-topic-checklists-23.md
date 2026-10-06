## 2026-10-06 · #420 — 새 topic 23개 점검표

- 결론: #420으로 채택한 topic 23개에 점검표 65항목을 넣었다(전체 43 topic·125항목). 병합 전에 PI가 항목을 검토한다.
- 바뀐 것: `labhq/vocab/topic_checklists.yaml`, `docs/reference/topic_checklists_sources.md`(topic별 근거 한 줄), 점검표 test 1개, README·README.en·manual의 점검표 수.
- 실행한 것: 근거 PMID 87개 esummary 제목 대조(87 ok), 관련 test 7개 파일 122 passed, 전체 suite 4005 passed·54 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 계획 프롬프트의 점검표 부분이 4,886자에서 10,400자로 늘었다. #420에서 정한 방식(프롬프트엔 topic 이름만, 점검표는 작업 폴더 파일)으로 줄이는 일은 러너가 파일을 넘기는 경로가 필요해 따로 한다.
- 근거: #420, PR #440, PR #446.
