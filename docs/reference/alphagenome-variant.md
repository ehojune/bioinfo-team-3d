# AlphaGenome 변이 점수 — 조회용 근거

공식 SDK·문서를 2026-10-15 확인했다. 실제 API는 호출하지 않았다.

| 계약 | 근거·해석 |
|---|---|
| 호출 | [SDK v1 dna_client](https://github.com/google-deepmind/alphagenome/blob/main/src/alphagenome/models/v1/dna_client.py): `score_variant(interval, variant, variant_scorers, organism=HOMO_SAPIENS)`. `ontology_terms` 인자가 없어 tidy 표에서 필터한다. |
| scorer | [권장 목록·tidy_scores](https://github.com/google-deepmind/alphagenome/blob/main/src/alphagenome/models/v1/variant_scorers.py): `RECOMMENDED_VARIANT_SCORERS`의 차이 scorer를 사용. `_ACTIVE`는 대립유전자 활성도여서 제외한다. 설치된 SDK의 목록·설정·`is_signed`를 출처와 캐시 키에 남긴다. |
| 입력 | GRCh38, 1-based 변이 좌표. 기본 1MB=1,048,576 bp. 16KB·100KB·500KB도 선택 가능. 변이당 SDK 호출 한 번, 호출당 최대 10개, allele 1,000 bp, scorer 20개. SDK는 RPC 실패 시 최대 5회 시도하므로 최대 50회 RPC 시도가 생길 수 있다. 연결 30초·항목 300초. |
| 점수 | [scoring 설명](https://www.alphagenomedocs.com/variant_scoring.html): RNA-seq, CAGE/PRO-cap, 접근성, histone, TF는 부호 있는 차이. splice·contact·PAS의 절대 차이는 방향을 복원하지 않는다. 유전자 정보가 없는 행에는 유전자를 추정해 붙이지 않는다. |
| quantile | [FAQ](https://www.alphagenomedocs.com/faqs.html): 흔한 변이 분포로 보정. signed scorer는 백분위 0~1을 -1~1로 재척도한다. `abs(quantile_score)`로 정렬하고 ≥0.99를 큰 효과 후보로 표시한다. 이는 labhq 표시 기준이며 유의성·병원성 판정 기준이 아니다. quantile이 없으면 비교 순위를 보류한다. |
| 출력 | SDK tidy 열·원 수치 유지, NaN/Inf는 null. 조직/세포 이름은 대소문자 무시한 정확 일치, ontology는 CURIE 일치. 두 필터를 함께 주면 교집합. 답은 상위 1~100행(기본 20), 파일·캐시는 상위 1,000행. 필터 전후 행 수·잘림 여부 기록. |
| 판본 | 설치 SDK 버전, API v1, 명시적으로 선택한 ALL_FOLDS. 서버 build ID는 SDK가 공개하지 않아 미확인으로 기록. 서버 내부 갱신까지 캐시 키가 구분하지는 못한다. |
| 이용 조건 | [공식 README의 Terms](https://github.com/google-deepmind/alphagenome#terms), [API 약관](https://alphagenome.google/terms): 이 API 출력은 비상업용이며 다른 ML 모델 학습에 사용 금지. 연구 예측이며 임상 판단·의료 조언에 사용 금지. SDK 코드의 Apache-2.0과 API 출력 조건은 별개다. |

검증: `tests/test_annot_alphagenome_variant.py`의 SDK/API mock. 키 값은 설정 파일 경로에서만 읽는 기존 MCP 초기화 절차를 따른다. 테스트는 무작위 sentinel만 쓰며 실제 키 파일을 읽지 않는다.
