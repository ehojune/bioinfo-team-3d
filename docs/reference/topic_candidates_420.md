# topic 후보 조사 (#420)

topic 어휘를 넓히려고 2026-10-06에 여러 출처에서 후보를 모은 결과다. PI 검토 전 후보이며, 채택된 topic만 `labhq/vocab/`에 들어간다. 찾아볼 때만 읽는다.

| 파일 | 내용 |
|---|---|
| [topic_candidates_420.tsv](topic_candidates_420.tsv) | topic 73행: 기존 20, 새 후보 38, 다른 후보로 합칠 별칭 11, 기각 4. 열: key·정의·상태·standard/trendy·출처 수·출처 종류·예시 id·EDAM |
| [topic_candidates_420_sources.tsv](topic_candidates_420_sources.tsv) | 서로 다른 출처 134개와 각 출처가 다룬 topic, 확인 결과 |

## 출처

| 종류 | 수 |
|---|---:|
| 논문(리뷰 50, 방법 14) | 64 |
| 교과서 | 4 |
| 튜토리얼·워크플로 목록(nf-core·Galaxy 등) | 33 |
| 블로그·뉴스레터(인코덤 포함) | 14 |
| 학회·세미나 | 9 |
| 포럼 | 6 |
| 기타 웹 | 4 |
| **합계** | **134** |

## 방법

- 출처 갈래 넷(논문·교과서 / 튜토리얼·워크플로 목록 / 블로그·뉴스레터·포럼 / 학회)을 Codex(gpt-5.6-luna) 넷이 따로 웹 검색으로 조사하고, gpt-5.6-sol이 id 기준으로 중복을 합쳤다(136행 → 134개).
- 조사 지침: 직접 열어 본 출처만, PMID·DOI·ISBN·URL로 적는다.
- 확인(2026-10-06, LLM 없이 스크립트): PMID는 PubMed esummary에서 존재와 제목 단어 절반 이상 일치, DOI는 doi.org handle API, ISBN은 Open Library·Google Books, URL은 GET. 134개 중 129개 확인, 5개는 Biostars·Bioinformatics Stack Exchange가 스크립트 접근을 403으로 막아 `blocked`(사이트는 실재).
- EDAM id는 labhq의 EDAM 부분집합(`labhq/vocab/edam_subset.yaml`)에 있는 것만 적었고 없으면 비웠다.

## 한계

- 출처 수는 후보가 실제로 쓰이는 정도의 대략적 신호다. 출처 수가 1~2인 후보는 한 갈래에서만 나왔다.
- 기각 4개(`foundation_model_embeddings`·`long_read_sequencing`·`pathway_enrichment`·`wgs_quality_control`)는 분석 영역이 아니라 방법·하위 단계라서 뺐다.
