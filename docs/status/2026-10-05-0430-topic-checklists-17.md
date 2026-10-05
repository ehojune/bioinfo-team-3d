## 2026-10-05 · #373 — topic 점검표 17개 추가와 같은 id 점검 합치기

- 결론: 점검표가 없던 topic 17개에 항목 48개를 넣어, 승인된 topic 20개 모두 점검표가 있다. #298에 올린 추가안을 그대로 옮겼다. PI가 10-05 18:57에 확인했다(#298). 근거 문헌 29편은 PMID(1편은 DOI)를 확인해 참고 기록에 둔다.
- 바뀐 것: `labhq/vocab/topic_checklists.yaml`(17 topic). `requirements()`는 두 선언 topic이 같은 id에 다른 점검을 두면(ATAC·ChIP의 `library_qc`, ATAC·메틸화의 `differential_model`, bulk·단일세포의 `batch`) 뒤 점검을 버리지 않고 ` / `로 잇는다. 앞의 둘은 이 PR의 데이터로 생겼고, `batch`는 전부터 있던 경우다. 계획 프롬프트의 점검표 규칙은 PR #400대로 점검 문장만 싣는다(1,124자 → 4,614자). 근거는 `docs/reference/topic_checklists_sources.md`, 매뉴얼 점검표 절.
- 실행한 것: 새 test(같은 id 두 topic)가 수정 전 코드에서 실패하고 수정 뒤 통과했다. `atac_seq`에 점검표가 생겨 "점검표 없는 topic은 요구 없음" 단언을 없는 topic key로 옮기고, `atac_seq`는 자기 항목만 요구하는지 확인한다. 전체 pytest 3898 passed·54 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 봇 P1(10-05): somatic 점검표가 matched normal과 panel of normals를 같은 대안으로 다뤘다. PoN은 기술적 인공물만 거르므로, 생식계열 변이는 matched normal로, 없으면 집단 생식계열 자료로 거르고 남는 위험을 한계로 밝히게 나눴다. PoN은 인공 변이 필터 항목으로 옮겼다.
- 미해결: 없음.
- 근거: `labhq/vocab/topic_checklists.py`, `tests/test_topic_checklists_precedents.py`.
