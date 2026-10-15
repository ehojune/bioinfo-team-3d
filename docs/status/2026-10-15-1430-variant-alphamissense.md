## 결론

Refs #621 (A, C). 사람 GRCh38의 `vep`가 AlphaMissense를 함께 받아 변이마다 점수와 분류를 쉬운 한국어로 돌려주고, 변이 해석 요청에 붙는 `variant_effect` 점검표(6항목)를 더했습니다. B(AlphaGenome 변이 점수)와 D(사전 계산 atlas)는 이 PR에 없습니다.

## Ensembl REST 확인 (2026-10-15, 읽기 전용 호출 7회)

- 요청: `POST https://rest.ensembl.org/vep/human/region?canonical=1&hgvs=1&mane=1&AlphaMissense=1`, 본문 `{"variants": ["7 140753336 labhq0 A T . . ."]}` (BRAF V600E, GRCh38). Ensembl release 116(`GET /info/data`).
- 응답 필드: `transcript_consequences[].alphamissense = {"am_pathogenicity": 0.9927, "am_class": "likely_pathogenic"}`. missense인 transcript에만 붙습니다(BRAF 24개 transcript 중 13개). MANE Select `ENST00000646891`(`mane_select: NM_004333.6`, `canonical: 1`)도 같은 값입니다.
- 같은 옵션이 `/vep/human/hgvs`(`ENST00000646891.2:c.1799T>A`)에서도 같은 값을 돌려줍니다. TP53 R175H(`17 7675088 C T`)는 0.9857 likely_pathogenic, BRCA1 frameshift(`17 43124027 ACT A`)는 transcript 70개 모두 `alphamissense` 없음입니다.
- 모르는 옵션(`NotARealPlugin=1`)과 사람이 아닌 종(`mus_musculus`)은 HTTP 200에 필드 없음입니다. 서버가 옵션을 조용히 무시하므로 필드가 없다는 것만으로 "점수 없음"을 단정할 수 없습니다.
- GRCh37 서버(`grch37.rest.ensembl.org`)도 점수를 주지만 분류 이름이 `pathogenic`(옛 이름)입니다. 지시대로 GRCh38 사람에만 요청하고, 옛 이름도 읽도록 번역표에 넣었습니다. GRCh37을 켜려면 `VEP_PLUGIN_ASSEMBLIES`에 더합니다.
- 분류 기준(Ensembl 플러그인 문서): `likely_benign` < 0.34, `likely_pathogenic` > 0.564, 그 사이 `ambiguous`.

## 바뀐 것

- `labhq/tools/annot.py`: 사람 GRCh38 VEP 요청에 `AlphaMissense=1`. 옵션이 캐시 키(`options`)에 들어가 이전 답이 완전한 답으로 다시 쓰이지 않습니다. 요약의 `alphamissense`는 MANE → canonical → 첫 transcript 순으로 고른 transcript의 점수만 읽습니다(`AlphaMissense: 0.99 (병원성 가능성 높음)`). 선택된 transcript에 점수가 없으면 `점수 없음 (이 transcript)`으로 두고, 다른 transcript의 점수는 `alphamissense_other`에 분리합니다. missense가 아니면 `해당 없음`입니다. Ensembl이 플러그인 요청을 거부하면(400·500) 플러그인 없이 한 번 더 보내 나머지 VEP 결과를 살리고 그 항목은 `unavailable`로 적고 캐시하지 않습니다.
- 출처 기록: 결과 `source.additional_sources`(와 `annotation_queries.jsonl`·결과 파일)에 AlphaMissense의 라이선스·이용 조건·인용, 결과 `note`에 한 줄 경고.
- `labhq/tools/annot_mcp.py`(도구 설명), `labhq/orchestrator/cso.py`(roster 한 줄).
- `variant_effect` topic: `output_types.yaml`, `topic_checklists.yaml`(6항목), `edam_map.yaml`(null, 맞는 EDAM topic이 하나로 정해지지 않아 local), `edam_subset.yaml`, `docs/vocabulary.md`(생성), `docs/reference/topic_checklists_sources.md`(PMID 9건, 2026-10-15 PubMed 대조).
- 의도해서 바꾼 pin: `test_output_types.py` `PLAN_PROMPT_SHA`, `test_output_types_research.py` `RESEARCH_PROMPT_SHA`(topic 규칙에 항목이 하나 늘어서), `test_output_vocab.py`의 key·topic 개수. 모두 #621 주석.
- `docs/manual.md`(공개 자원 조회 도구, topic 점검표).

## 라이선스 (지시와 다른 점)

지시는 "CC BY-NC-SA 4.0, 비상업"이었으나 원자료는 다릅니다. DeepMind README(`google-deepmind/alphamissense`, 2024-03-13 "Update AlphaMissense predictions database license" 이후)와 Ensembl 플러그인 문서(`VEP_plugins/AlphaMissense.pm`, release/116) 모두 예측값을 CC BY 4.0이라 적습니다. 플러그인 문서는 데이터 이용이 Google Cloud Platform 약관을 따른다고 덧붙입니다. 그래서 기록에는 CC BY 4.0(출처 표기 필요)과 "2024-03-13 전 배포본은 CC BY-NC-SA 4.0(비상업)"을 함께 적었습니다. PI가 비상업 조건을 그대로 두라고 하면 `ALPHAMISSENSE_SOURCE`의 문구만 고치면 됩니다. 임상 근거가 아니라는 고지는 DeepMind의 "임상용으로 검증·승인되지 않았다"를 따랐습니다.

## 확인

- 새 test 30건(`test_annot_alphamissense.py` 23, `test_variant_effect_topic.py` 7)과 고친 기존 assertion 1건(`test_annot.py`의 VEP 질의 문자열)이 main 코드에서 모두 실패하고 이 branch에서 통과합니다(main 쪽은 labhq·docs 변경을 stash해 돌림).
- 관련 pure test 1,200여 건(annot, vocab, checklist, prompt, semantics) 통과. PI 시험이 돌고 있어 프로세스를 띄우는 test(MCP stdio 서버, runner 연결, 하위 `python` 호출)는 로컬에서 돌리지 않았고 CI에 맡겼습니다.

## 남은 것

- B: AlphaGenome `score_variant` 전환. D: 사전 계산 atlas.
- AlphaMissense 데이터 판본은 REST가 알려 주지 않아 Ensembl release로만 기록합니다. 표 자체(Zenodo·GCS)를 받아 쓰는 대안은 쓰지 않았습니다.
