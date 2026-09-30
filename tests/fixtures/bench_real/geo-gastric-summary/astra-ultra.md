**GSE79973 paired tumor/normal 분석·검증 설계 보고서**

이 보고서는 **고정 snapshot을 바탕으로 한 분석 계획**이다. 발현 데이터·주석 파일·gene-set 파일·STRING 네트워크는 다운로드하지 않았으며 분석도 실행하지 않았다. 문헌과 공식 방법 문서만 열람했다. 아래에서 **제공값**, 제공값의 **산술적 귀결**, **제안 설정값**, **미산출 결과**를 구분한다.

**대상과 근거.** 제공된 [GSE79973 snapshot의 출처](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE79973)는 GPL570, tumor 10개와 paired normal 10개, series matrix 약 4.7 MB이다. 따라서 총 20시료·10쌍은 제공값에서 계산된다. 정확한 파일 크기, GSM 목록, 환자별 대응표, 전처리 방식은 확인되지 않았다. 약 4.7 MB를 정확한 바이트 수로 환산하지 않는다.

| 검사 대상 | Snapshot에 제시된 방향 | 향후 판정 |
|---|---|---|
| GKN1, GKN2, ATP4A, ATP4B, PGC, LIPF | 감소: 6개 | `tumor − normal`의 log₂FC < 0 |
| COL1A1, COL11A1, SPP1, INHBA, MMP7, CTHRC1, THBS2 | 증가: 7개 | `tumor − normal`의 log₂FC > 0 |
| `HALLMARK_EMT` | NES 양수 기대 | NES > 0 여부와 유의성을 별도 판정 |
| STRING | 사람 taxonomy `9606` | 반환 종과 `9606.ENSP…` ID 확인 |

유전자 방향은 **재현할 검사 기준**이며, 이번 실행에서 확인한 발현 결과가 아니다. `HALLMARK_EMT`는 manifest에서 공식 명칭인 `HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION`과 연결해 기록한다. 실제 분석에는 고정한 GMT의 해당 항목을 사용한다. [MSigDB 공식 항목](https://www.gsea-msigdb.org/gsea/msigdb/cards/HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION.html)

**Paired 차등발현 분석.** 주효과는 같은 환자의 종양과 정상 사이 발현 차이로 정의한다.

1. **시료 대응을 먼저 확정한다.** `GSM`, `patient_id`, `condition`, 원문 pairing 근거, 확인 가능한 batch를 기록한다. 환자마다 tumor와 normal이 하나씩 있는지 검사하고 matrix 열 순서를 대응표와 일치시킨다. GSM 순서나 파일명 유사성만으로 짝을 추정하지 않는다.
2. **전처리 상태를 확인한다.** Series matrix의 로그 변환·정규화 설명과 값 분포를 확인한다. 이미 정규화된 log₂ 발현값이면 그대로 사용한다. 상태가 불명확하면 변환을 임의 적용하지 않고 확인 필요로 남긴다. QC에는 결측, 중복 시료, 발현 분포, PCA/MDS, 시료 간 상관을 포함한다. 기대 방향과 다르다는 이유로 시료를 제외하지 않는다.
3. **Probe를 유전자 단위로 정리한다.** 버전을 고정한 GPL570 주석으로 매핑한다. 미매핑 및 여러 유전자에 연결되는 probe는 주분석에서 제외하고 제외표에 남긴다. 같은 유전자에 여러 probe가 있으면 전체 시료 평균 발현이 가장 높은 probe를 대표로 선택한다는 규칙을 사전에 고정한다. 동률은 probe ID 순으로 처리한다. 최소 P값이나 최대 |t|를 기준으로 대표 probe를 선택하지 않는다.
4. **환자 효과를 포함한 limma 모형을 적합한다.** Normal을 기준 수준으로 두고 `~ patient_id + condition`의 `conditionTumor` 계수를 검정한다. 동일 환자를 고정효과로 처리하면서 `duplicateCorrelation`을 중복 적용하지 않는다. Batch를 추가할 때는 조건·환자 효과와의 혼동 및 설계행렬의 rank를 먼저 확인한다. 복잡한 설계와 경험적 베이즈 분산 추정은 limma의 방법론에 근거한다. [Ritchie 등, limma 원저](https://pubmed.ncbi.nlm.nih.gov/25605792/)

모형은 다음과 같다.

\[
y_{gi}=\alpha_g+\gamma_{g,\mathrm{patient}(i)}
+\beta_g I(\mathrm{Tumor}_i)+\epsilon_{gi},
\qquad \beta_g=\log_2FC(\mathrm{Tumor}-\mathrm{Normal})
\]

완전한 10쌍에 결측·추가 공변량이 없는 경우, 설계행렬의 rank는 \(1+9+1=11\), 잔차 자유도는 \(20-11=9\)이다. 이는 **설계상 계산값**이며 실행 결과가 아니다.

DEG의 **제안 기준**은 BH 조정 P값 `< 0.05`와 `|log₂FC| ≥ 1`이다. 전체 검정 유전자에 대해 log₂FC, moderated t, 원 P값, BH 조정 P값을 보존한다. 방향 검사 유전자에는 환자별 차이와 probe 간 방향 불일치도 함께 보고한다. 방향 일치와 DEG 기준 충족은 별도 열로 기록한다.

**Pathway와 GSEA 검증.** 선택한 DEG의 과대표현과 전체 발현 순위의 편향을 각각 검사한다.

| 분석 | 입력·배경 | 사전 설계 | 향후 보고값 |
|---|---|---|---|
| GO Biological Process·Reactome ORA | 상승·하강 DEG를 각각 입력. 배경은 QC·매핑·차등발현 검정을 통과한 전체 고유 유전자 중 해당 DB에 주석된 유전자 | 한쪽 Fisher/초기하 검정. 두 방향과 두 DB에서 수행한 전체 검정에 BH 적용 | 입력·배경 수, 경로 크기, 겹친 유전자 수, enrichment 비율, P값·FDR |
| Hallmark GSEA | DEG로 제한하지 않은 전체 분석 가능 유전자의 **signed moderated t** 순위 | 큰 양수가 tumor 증가가 되도록 내림차순 정렬. 고유 gene ID 사용 | ES, NES, P값·조정값, 실제 검사된 set 크기, leading-edge 유전자 |
| 안정성 검사 | 동일 전처리·주석·gene-set 정의 유지 | 환자 한 쌍씩 제외해 재분석 | marker 및 EMT 방향 유지 여부, 특정 환자에 대한 의존성 |

ORA에서 살펴볼 생물학적 가설은 ECM·collagen 관련 변화와 위 조직 기능 관련 변화이다. **유의한 경로가 이미 발견되었다는 뜻은 아니다.** ORA 배경을 전체 인간 유전자나 DEG 목록으로 대체하지 않는다.

GSEA의 **제안 실행 설정**은 `fgseaSimple`, gene-set permutation `10,000회`, `seed=79973`, `gseaParam=1`, 양방향 점수, 분석 유전자와 교집합을 취한 set 크기 `15–500`이다. 패키지 버전과 동률 처리 규칙을 고정하고, 전체 검사 Hallmark의 P값에 BH를 적용해 `< 0.05`를 유의 기준으로 사용한다. 이 수치들은 모두 설계 선택값이다. [fgsea 공식 문서](https://bioconductor.org/packages/release/bioc/manuals/fgsea/man/fgsea.pdf)

Pairing은 **순위를 만드는 limma 단계**에 반영된다. Preranked GSEA의 gene-set permutation은 환자 내 라벨 교환 검정과 다르다. GSEA 공식 문서도 paired 분석에서 외부의 paired 검정으로 순위를 만들도록 권고한다. [GSEA 공식 FAQ](https://docs.gsea-msigdb.org/GSEA/GSEA_FAQ/)

추가 검증으로 환자 내 tumor/normal 라벨만 교환하는 제한 순열 검정을 설계한다. 모든 유전자에 동일한 교환을 적용하고 매번 모형·순위·ES를 다시 계산한다. 10쌍의 가능한 교환 패턴은 **\(2^{10}=1,024\)개**이며, 이는 이론적 경우의 수이다. 교환가능성 가정과 검정 방향을 기록하고, 이 결과를 주분석의 NES·조정값과 구분한다.

EMT는 다음처럼 판정한다.

- `NES > 0`, 조정값 기준 충족: 기대 방향과 통계적 근거가 함께 관찰됨.
- `NES > 0`, 조정값 기준 미충족: 기대 방향에 부합하나 유의한 enrichment 근거는 부족함.
- `NES ≤ 0`: 기대와 불일치. contrast 방향, 매핑, gene-set 식별, QC를 점검하고 결과를 그대로 보고함.
- 미매핑·실행 실패: 판정 불가.

**STRING 검증.** 분석에서 선정된 DEG 전체를 주입력으로 하고 상승·하강 방향을 노드 속성으로 표시한다. Snapshot의 13개 marker는 별도 설명용 목록으로 유지한다.

- `species=9606`으로 매핑하고 입력 gene ID·symbol, 반환 preferred name, 실제 `9606.ENSP…` ID, 매핑 상태를 보존한다. 누락·다중 매핑을 기록하며 ENSP 번호를 임의로 채우지 않는다.
- **제안 설정**은 functional network, API `required_score=700`, `add_nodes=0`이다. `required_score=400`을 민감도 분석으로 사용한다. 버전과 증거 채널을 고정하고 고립 노드도 보존한다.
- 관측·기대 edge 수, node 수, PPI enrichment P값, 기능 enrichment FDR을 향후 기록한다. 배경은 전체 검정 유전자를 STRING에 매핑한 집합으로 지정한다. API는 실험 배경을 `background_string_identifiers`로 받을 수 있다. [STRING 공식 API](https://string-db.org/help/api/)
- Text mining에만 의존하는 연결과 실험·curated database 근거가 있는 연결을 구분해 표시한다. Leading-edge 유전자와의 겹침도 기록하되, 같은 입력과 기존 지식을 재사용하는 검증이라는 점을 명시한다.

STRING의 functional edge는 기능적 연관을 나타내며 직접 결합, 활성화·억제 방향, 위암에서의 인과관계를 입증하지 않는다. 네트워크 score도 이 발현 분석의 P값이 아니다. [STRING score 설명](https://string-db.org/help/scores/)

**짧은 문헌 요약.** 외부 연구의 효과 크기나 유의확률을 GSE79973의 결과로 가져오지 않고 정성적으로 대조한다.

| 원저 | 확인된 내용 | 이번 설계에서의 의미 |
|---|---|---|
| Moss 등, *Clinical Cancer Research* (2008) | 위암에서 GKN1·GKN2 감소를 관찰하고 단백질 및 RNA 측정으로 확인했다. | Snapshot의 GKN1/GKN2 감소 방향을 뒷받침한다. 이 자료의 효과 크기나 예후 연관성을 대신 증명하지 않는다. [원저](https://pubmed.ncbi.nlm.nih.gov/18593995/) |
| Kumar 등, *Cancer Discovery* (2022) | 위암 단일세포·공간 분석에서 종양 상피의 LIPF 감소와 섬유아세포 일부의 THBS2·CTHRC1 증가를 보고했다. | Snapshot의 방향과 정성적으로 부합한다. Bulk EMT·ECM 신호를 해석할 때 상피와 기질세포의 기여를 함께 고려할 근거다. [원저](https://pmc.ncbi.nlm.nih.gov/articles/PMC9394383/) |

이를 토대로 “위 조직의 분화·기능 관련 발현 감소와 기질 재편성에 부합할 가능성”을 **해석 가설**로 둔다. Bulk 자료의 양의 EMT NES만으로 암세포 자체의 EMT나 전이를 확정하지 않는다.

**재현 manifest 항목.** 아래는 향후 실행에 필요한 명세다. 미확보 값은 `null`, 미실행 단계는 `not_run`으로 둔다. 버전·해시가 비어 있는 현재 상태는 재현 실행을 완료한 상태가 아니다.

| Manifest 묶음 | 기록할 항목 | 현재 확정 상태 |
|---|---|---|
| 실행 범위 | `mode`, snapshot 원문·식별값, 보고서 작성일, 다운로드·실행 여부 | `design_only`, 데이터 다운로드·분석 미실행 |
| GEO 입력 | accession, platform, 제공된 출처 URL, 실제 matrix URL·파일명, 취득 시각, 정확한 bytes, SHA-256 | `GSE79973`, `GPL570`, 출처 URL, 약 4.7 MB만 확정. 나머지 `null` |
| 시료 대응 | GSM–patient–condition 표, pairing 원문 근거, matrix 열 순서, batch, 제외 사유 | tumor 10·normal 10·10쌍. 실제 대응표 `null` |
| 전처리·QC | 원자료 처리 설명, 로그·정규화 판정 근거, 결측·필터 규칙, QC 결과, 포함 시료 목록 | 실행 시 기록 |
| 주석 | GPL570 annotation 출처·버전·해시, gene namespace, 매핑·제외표, 대표 probe 규칙 | 평균 발현 기반 선택 규칙 제안. 파일·매핑 결과 `null` |
| 차등발현 | design matrix, 기준 수준, contrast, limma 및 `eBayes` 인자, 검정 집합, BH 적용 범위, DEG 기준 | `~ patient_id + condition`, `Tumor−Normal`, 제안 기준 명시 |
| Pathway | DB별 release·GMT 또는 매핑 파일 해시, 입력·배경 gene ID 목록, 크기 필터, 다중검정 범위 | 파일·버전·목록 `null` |
| GSEA | 순위 파일·해시, t 통계량 정의, 동률 규칙, 함수·인자, seed, permutation 방식, EMT alias | 제안 설정 명시. 순위·결과 `null` |
| STRING | DB release·버전 URL, 종, 실제 ID 매핑표, 입력·배경, score·network type·추가 노드, 요청·원응답·해시 | taxonomy `9606`, ID 형식만 확정. 실제 응답 `null` |
| 실행 환경 | OS, R·Bioconductor·패키지 버전, locale, RNG 설정, 병렬 설정, `sessionInfo()`, 코드 commit·해시 | 전부 실행 시 고정 |
| 산출물·문헌 | DEG 전체표, 방향 검사표, ORA/GSEA 결과, 네트워크 node/edge 표, QC 그림, 각 해시; 문헌 DOI/PMID·열람일·근거 문장 | 본 설계 보고서 외 분석 산출물 미생성 |

**검사 가능한 수치와 현재 판정.**

| 검사 | 기대·계산·제안값 | 이번 실행의 관측 결과 |
|---|---|---|
| 시료 구성 | 제공값: tumor 10, normal 10 / 계산값: 총 20·10쌍 | 실제 matrix 대조 미실행 |
| 모형 구조 | 완전한 기본 설계에서 rank 11, 잔차 자유도 9 | 미산출 |
| Marker 방향 | 감소 6개, 증가 7개, 합계 13개 | 일치 수 `null`; 미매핑은 별도 집계 |
| DEG | 제안: BH `<0.05`, `|log₂FC|≥1` | DEG 수·개별 log₂FC·P값 모두 `null` |
| EMT GSEA | 제공된 기대: NES > 0 | NES·유의확률·leading edge 모두 `null` |
| Paired 라벨 교환 | 계산값: 가능한 패턴 1,024개 | 검정 미실행 |
| STRING | 종 `9606`, ID 형식 `9606.ENSP…` | 매핑 수·node/edge 수·enrichment 모두 `null` |

`null`은 미확인 또는 미산출을 뜻하며, 0이나 비유의를 뜻하지 않는다.