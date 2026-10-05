## 2026-10-05 · #373 — 해당 없는 점검표 항목을 보고서 한계에서 빼기

- 결론: 벤치 C에서 labhq는 네 과제 모두 정확성은 동점이었지만 읽기에서 졌다. 문헌 표(t4)와 정규화 점검(t5) 보고서가 요청과 무관한 `batch`·`pairing`·`gene_set_test`·`independent_validation`을 한계로 길게 늘어놨다. `not_applicable` 답은 이제 한계가 아니고 보고서 본문에서 빠진다.
- 바뀐 것: `limitations()`는 `assumption` 답만 돌려주고, 새 `not_applicable()`이 해당 없는 항목 id를 준다. 보고서 문맥은 그 id를 "본문에서 빼라"는 한 줄로 넘긴다. 점검표 규칙은 "해당하지만 못 한 점검은 `assumption: <이유>`"로 답하게 한다(그래야 한계로 남는다). 보고서 프롬프트 두 곳과 매뉴얼 점검표 절. README "아직 지는 곳"을 벤치 C 수치로 갱신했다.
- 실행한 것: 점검표 test 두 곳을 새 기준으로 정확히 단언하게 바꿨다(한계는 assumption만, not_applicable은 따로, 보고서 문맥에서 한계 부분에 없음). 전체 pytest 3944 passed·54 skipped, `scripts/check_public.sh`.
- 미해결: 보고서·요약·QC 반복, 묶음 안 링크 깨짐은 #423·후속에서.
- 근거: `labhq/vocab/topic_checklists.py`, `labhq/orchestrator/cso.py` `plan_report_context`.
