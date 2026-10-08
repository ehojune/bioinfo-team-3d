## 2026-10-08 · 메타데이터 표·목록 정리는 단순 작업으로, 직원 직접 지정은 연구 lane 밖으로 (PI 점검 R16)

- 결론: 연구 lane을 켠 trial에서 웹으로 "GEO GSE10072 시료 메타데이터를 표로 정리해 줘"를 보내자, 규칙 분류가 "단순 작업으로 확실히 한정되지 않음"으로 research에 넣어 CP1과 5단계 연구 계획이 떴다. PI 설정에도 연구 lane을 켰으므로 내일 PI가 같은 일을 겪는다. 이제 출처에 있는 것을 표·목록으로 정리하거나(메타데이터·시료 정보) 추출·다운로드하는 요청은 simple이다. 분석(DE·경로·생존 등)이 들어가면 표를 함께 요청해도 research로 둔다. 직원 한 명을 직접 지정한 요청은 PI가 research를 명시하지 않는 한 simple이고, 명시했을 때의 안내는 한국어로 바꿨다.
- 바뀐 것: `labhq/research/contract.py`(`_SIMPLE_SIGNALS`·`_RESEARCH_SIGNALS`, `classify_intake(mode=)`), `labhq/orchestrator/cso.py`(mode 전달, direct+research 문구), `docs/research_protocol.md` 접수 표, `tests/test_research_protocol.py` 7건.
- 실행한 것: 연구 시험 415 passed, 전체 pytest(Windows) 4769 passed·59 skipped, main(#496) 위로 rebase 뒤 연구·cso 시험 재실행 통과. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 웹·CLI에서 PI가 연구/간단을 직접 고르는 선택지는 웹 카드 PR(#494)과 기동 PR(#493) 병합 뒤 따로 넣는다.
- 근거: `tests/test_research_protocol.py::test_collecting_what_a_source_says_is_simple_but_any_analysis_is_research`.
