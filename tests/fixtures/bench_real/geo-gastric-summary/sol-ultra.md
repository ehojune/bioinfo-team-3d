# GSE79973 paired 위암 분석 및 pathway·GSEA·STRING 검증 설계

> **분석 상태:** 고정 snapshot에 대한 사전 분석 계획이다. 발현행렬 다운로드, 차등발현·경로·GSEA 계산 및 STRING API 호출은 수행하지 않았다. 따라서 log2FC, P값, FDR, NES, DEG 수, 네트워크 크기 등은 모두 **미산출**이다.  
> **방향 정의:** 모든 효과와 순위 통계량은 `Tumor − Normal`이다.

## 1. 고정 사실과 판정 대상

[GSE79973 공식 GEO 레코드](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE79973)는 paired normal을 인접 비종양 위점막으로 기술한다. 따라서 건강인 정상 위와의 비교로 확대 해석하지 않는다.

| 항목 | 고정값 또는 수학적 귀결 | 현재 관찰값 |
|---|---:|---|
| Accession / platform | `GSE79973` / `GPL570` | 고정 |
| 표본 구조 | Tumor 10 + paired Normal 10 = 20 arrays | 고정 |
| 완전한 pair | 10 | 고정 |
| series matrix 크기 | 약 4.7 MB | snapshot 기재값 |
| paired model 설계 rank | 11 | 실행 시 검증 |
| 원자료 잔차 자유도 | \(20-11=9\) | 실행 시 검증 |
| 감소 방향 대조 | 6 genes | 미계산 |
| 증가 방향 대조 | 7 genes | 미계산 |
| 전체 방향 대조 | 13 genes | 미계산 |
| `HALLMARK_EMT` | `NES > 0` 예상 | 미계산 |
| paired sign-flip 구성 | \(2^{10}=1{,}024\) | 설계값 |
| STRING taxon | `9606` | 미조회 |
| STRING ID 형식 | `9606.ENSP...` | 미조회 |

다음은 결과로 간주하지 않는다.

- DEG 수와 pathway 수
- 개별 유전자의 log2FC·P·FDR
- EMT NES의 크기와 유의성
- STRING 매핑률·node·edge·hub·enrichment P값
- 환자 임상정보, subtype, tumor purity 및 생존 연관성

## 2. 표본 및 표현행렬 무결성

분석 시작 조건은 다음과 같다.

1. 표현행렬에 중복 없는 표본 열이 정확히 20개 있어야 한다.
2. Tumor와 Normal이 각각 10개여야 한다.
3. `pair_id`가 10개이고 각 pair에 Tumor 1개와 Normal 1개가 있어야 한다.
4. 모든 표본의 platform이 `GPL570`이어야 한다.
5. 열 순서나 accession 번호의 홀짝만으로 pair를 추정하지 않고 GEO의 환자 표기를 근거로 매핑한다.
6. 한 표본을 기술적 이유로 제외하면 주분석에서는 상대 표본도 함께 제외한다.
7. 연령·성별·병기처럼 snapshot에 없는 공변량은 추정하지 않고 `NA/not reported`로 둔다.

필수 sample manifest 열은 다음과 같다.

```text
series_accession, sample_accession, matrix_column, platform_id,
sample_title_raw, tissue_raw, pair_id, condition,
pairing_source, included, exclusion_reason
```

### processed matrix에 한정한 QC

이번 계획은 series matrix 재분석이므로 CEL 수준의 array image, RLE, NUSE, RNA degradation QC는 할 수 없다. 이후 CEL 기반 RMA 분석을 수행한다면 별도의 분석과 manifest로 관리한다.

- source metadata의 scale과 전처리법을 확인한다.
- 이미 log2·정규화된 행렬에 log2 또는 quantile normalization을 다시 적용하지 않는다.
- scale을 확정할 근거가 없으면 분포만 보고 추정하지 않고 분석을 중단한다.
- density·boxplot, 결측률, sample–sample correlation, PCA/MDS, hierarchical clustering을 확인한다.
- PCA에는 조건과 pair를 함께 표시하고 paired samples를 선으로 연결한다.
- PCA에서 생물학적으로 분리된다는 이유만으로 표본을 제거하지 않는다.
- 표본 배제는 서로 독립적인 기술 QC 계열 두 가지 이상이 지지할 때만 허용하며, DE 결과를 보기 전에 결정한다.

## 3. GPL570 probe-to-gene 규칙

1. 버전을 고정한 GPL570 annotation과 `hgu133plus2.db`/`org.Hs.eg.db` 매핑을 사용한다.
2. 기본 gene key는 `ENTREZID`, 표시는 승인된 gene symbol을 사용한다.
3. control probe, unmapped probe, 한 probe가 여러 gene에 모호하게 매핑되는 경우는 gene-level 주분석에서 제외한다.
4. 한 gene에 여러 probe가 있으면 조건과 P값을 보지 않고 전체 20개 배열에서 median expression이 가장 높은 probe를 대표로 선택한다. 동률은 probe ID 사전순으로 푼다.
5. 모든 probe별 결과를 보조표에 보존하고, 표본별 probe 중앙값으로 합친 결과를 민감도 분석으로 제시한다.
6. 최소 P값이나 최대 \(|\mathrm{logFC}|\) probe를 사후 선택하지 않는다.
7. GSEA 입력은 gene당 정확히 한 행이어야 한다.

Annotation source, release, package version, 매핑표 SHA-256 및 제외된 probe 수를 기록한다.

## 4. paired 차등발현 분석

완전한 1:1 pair에는 환자 고정효과를 포함한 limma 모형을 사용한다. 이는 [limma paired-sample 지침](https://bioconductor.org/packages/release/bioc/vignettes/limma/inst/doc/usersguide.pdf)과 일치한다.

\[
Y_{g,s}=\alpha_g+\gamma_{g,\mathrm{pair}(s)}
+\beta_g I(s=\mathrm{Tumor})+\epsilon_{g,s}
\]

```r
meta$condition <- factor(meta$condition, levels = c("Normal", "Tumor"))
meta$pair_id   <- factor(meta$pair_id)

design <- model.matrix(~ pair_id + condition, data = meta)

stopifnot(
  nrow(meta) == 20L,
  nlevels(meta$pair_id) == 10L,
  all(table(meta$pair_id, meta$condition) == 1L),
  qr(design)$rank == 11L
)

fit <- limma::lmFit(expr_gene, design)

fit_zero <- limma::eBayes(
  fit, trend = TRUE, robust = TRUE
)

fit_lfc1 <- limma::treat(
  fit, lfc = 1, trend = TRUE, robust = TRUE
)
```

- `conditionTumor > 0`: Tumor에서 증가
- `conditionTumor < 0`: Tumor에서 감소
- 완전자료의 원자료 residual df: 9
- 기술용 순위와 GSEA: 0을 검정한 `fit_zero`의 moderated t
- 주 DEG 정의: `treat(lfc=1)`의 전체 gene universe BH FDR `<0.05`
- 이는 \(|\mathrm{log2FC}|>1\)을 직접 검정하는 사전 기준이며, 관찰 결과가 아니다.
- 같은 `pair_id`를 설계행렬에 넣은 상태에서 `duplicateCorrelation(block=pair_id)`을 중복 적용하지 않는다.
- batch가 확인되더라도 조건과 완전히 교락되지 않고 설계가 full rank일 때만 추가한다.

보고 열은 다음으로 고정한다.

```text
entrez_id, symbol, representative_probe, n_mapped_probes,
log2FC_T_minus_N, CI95_low, CI95_high, AveExpr,
moderated_t, raw_P, BH_FDR, treat_P, treat_BH_FDR,
n_pairs_expected_direction, direction_check
```

## 5. 사전 지정 방향성 검사

| 기대 방향 | 유전자 |
|---|---|
| Tumor에서 감소, `log2FC < 0` | GKN1, GKN2, ATP4A, ATP4B, PGC, LIPF |
| Tumor에서 증가, `log2FC > 0` | COL1A1, COL11A1, SPP1, INHBA, MMP7, CTHRC1, THBS2 |

판정은 다음처럼 분리한다.

- **방향 검사 통과:** 감소 6/6, 증가 7/7, 전체 13/13의 gene-level log2FC 부호가 기대와 일치
- **pair 일관성:** 각 gene에서 기대 방향인 pair 수를 `0/10`부터 `10/10`까지 그대로 보고
- **통계적 지지:** 각 gene의 CI, P값과 전체 검정군 BH FDR을 별도로 보고
- 부호가 0이거나 반대이면 불일치로 센다.
- 불일치 시 contrast 방향, sample label, pair mapping, probe annotation을 감사하되 기대 패널에 맞추기 위해 분석법을 바꾸지 않는다.

이 패널은 라벨·contrast 오류를 검출하는 양성 대조이지 독립 검증자료가 아니다.

## 6. pathway over-representation analysis

Up과 Down DEG를 분리한다.

- Up: `treat(lfc=1)` BH FDR `<0.05`, 양의 log2FC
- Down: 동일 기준, 음의 log2FC
- 배경 \(U\): paired model에서 실제 검정되었고 해당 pathway library에 매핑된 고유 gene 전체
- gene-set 크기: \(U\)와 교집합한 뒤 15–500
- 최소 overlap: 3 genes
- 검정: 단측 hypergeometric/Fisher ORA
- 다중검정: library와 방향별 전체 term에 BH 보정
- 보고 기준: BH \(q<0.05\) 및 enrichment ratio \(>1\)

각 term마다 다음 수치를 남긴다.

\[
N=|U|,\quad K=|\mathrm{DEG}|,\quad M=|\mathrm{term}\cap U|,
\quad k=|\mathrm{DEG}\cap \mathrm{term}|
\]

```text
library, release, direction, term_id, term_name,
N, K, M, k, enrichment_ratio, odds_ratio,
raw_P, BH_FDR, overlap_genes
```

기준을 만족하는 DEG가 없으면 cutoff를 완화하지 않고 `ORA 입력 없음/평가 불가`로 보고한다.

## 7. preranked GSEA와 paired 검증

snapshot의 `HALLMARK_EMT`는 MSigDB canonical name인 `HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION`으로 명시한다. 이 gene set은 wound healing, fibrosis, metastasis와 관련된 EMT 프로그램으로 정의된다. [MSigDB 공식 카드](https://www.gsea-msigdb.org/gsea/msigdb/cards/HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION.html), [Hallmark 구축 논문](https://pmc.ncbi.nlm.nih.gov/articles/PMC4707969/)

### Preranked 분석

- 순위: 전체 검정 gene의 paired moderated t 내림차순
- 양의 순위: Tumor-high
- collection: 버전·GMT checksum을 고정한 human Hallmark 전체
- `minSize=15`, `maxSize=500`
- weighted statistic, \(p=1\)
- `fgseaMultilevel`, `eps=0`, seed `79973`, 단일 thread
- 동률은 `moderated_t → log2FC → symbol` 순으로 결정하고 동률 수를 기록
- Hallmark 전체에 대해 FDR을 계산
- ES, NES, nominal P, BH FDR, 실제 set size, leading edge를 보고

[GSEA 원 논문](https://pubmed.ncbi.nlm.nih.gov/16199517/)은 전체 유전자 순위를 이용하는 방법을 제시한다. Gene-set permutation 방식에서는 `FDR <0.05`를 쓰도록 [GSEA FAQ](https://docs.gsea-msigdb.org/GSEA/GSEA_FAQ/)의 구분을 적용한다.

| 판정 | 사전 조건 |
|---|---|
| 방향 일치 | `NES > 0` |
| 통계적 지지 | `NES > 0` 및 `FDR q < 0.05` |
| 방향만 일치 | `NES > 0`, 그러나 `q ≥ 0.05` |
| 방향 불일치 | `NES ≤ 0` |
| 평가 불가 | ID overlap 또는 set size 기준 미충족 |

### Pair를 보존한 귀무분포

일반적인 20표본 무제약 label shuffle은 pair 구조를 깨므로 사용하지 않는다.

1. 각 pair에서 \(D_{g,i}=T_{g,i}-N_{g,i}\)를 계산한다.
2. 10개 pair의 부호를 독립적으로 뒤집는 \(2^{10}=1{,}024\)개 구성을 모두 열거한다.
3. 각 구성에서 동일한 ranking과 EMT ES를 다시 계산한다.
4. 양의 단측 exact P는 다음과 같다.

\[
p_{\mathrm{exact}}=
\frac{\#\{ES_b\ge ES_{\mathrm{observed}}\}}{1{,}024}
\]

전수열거에 관찰 구성이 포함되므로 별도 `+1` 보정은 하지 않는다. 최소 가능한 단측 P는 \(1/1{,}024\)이다. 다만 이 검정은 귀무가설 아래 pair 내 교환 가능성 또는 차이값의 대칭성을 가정한다.

추가로 10회의 leave-one-pair-out 분석을 실시한다. 8/10 이상에서 NES가 양수이면 `directionally stable`로 표시하되 독립 유의성 검정으로 사용하지 않는다.

`강건한 EMT 지지`라는 표현은 다음을 모두 만족할 때만 사용한다.

- preranked `NES > 0`
- Hallmark 전체 보정 `q < 0.05`
- paired exact ES \(>0\), \(p_{\mathrm{exact}}\le0.05\)
- leave-one-pair-out 중 최소 8/10에서 NES \(>0\)

## 8. STRING 검증

### 입력 집합

- 주 네트워크: 양의 `treat(lfc=1)` DEG
- EMT 집중 네트워크: `EMT leading edge ∩ 양의 DEG`
- EMT가 통계적으로 지지되지 않으면 두 번째 네트워크를 확증적 EMT 네트워크로 부르지 않는다.
- 고정 방향 대조 7개만으로 만든 네트워크는 순환적 검증이므로 주분석에 사용하지 않는다.

### ID 매핑과 요청 설정

[STRING 공식 API](https://string-db.org/help/api/)에 따라 gene ID를 먼저 매핑하고 반환된 STRING ID를 후속 요청에 사용한다.

```text
species            = 9606
network_type       = functional
required_score     = 700
add_nodes          = 0
caller_identity    = <recorded>
```

- API host는 반드시 version-specific 주소로 고정한다.
- 반환 `ncbiTaxonId`는 전부 `9606`이어야 한다.
- 모든 mapped ID는 `^9606\.ENSP[0-9]+$`에 일치해야 한다.
- `queryItem`, `preferredName`, 입력 Entrez/symbol을 함께 보존한다.
- 다중·충돌·미매핑 결과를 자동 선택하지 않고 별도 보고한다.
- taxon과 ID 형식은 mapped ID의 100%가 통과해야 한다.
- 매핑률이 90% 미만이면 음성 네트워크 결과를 해석하지 않고 `mapping coverage 불충분`으로 표시한다.

주 네트워크는 [STRING score 설명](https://string-db.org/help/scores/)에 따라 `required_score=700`을 사용하며, 400과 900에서 민감도 분석을 한다. `add_nodes=0`으로 입력하지 않은 이웃 단백질의 유입을 막는다.

### 검사 항목

```text
n_input_unique
n_mapped_unique / n_input_unique
n_unmapped
n_ambiguous
n_nodes_with_edges
n_isolated_nodes
n_edges
n_expected_edges
mean_degree
clustering_coefficient
PPI_enrichment_P
```

검증 조건은 다음과 같다.

- edge 양 끝이 모두 제출한 mapped ID 집합에 포함
- 비입력 node 0개
- self-loop 및 중복 무방향 edge 0개
- 모든 edge score가 0.7 이상
- enrichment background는 전체 검정 universe를 STRING ID로 매핑한 집합
- 단일 사전 지정 네트워크의 PPI enrichment는 \(P\le0.05\)일 때 지지로 표시
- 여러 네트워크를 검사하면 BH 보정 후 \(q<0.05\) 적용

STRING의 기본 functional edge는 직접 물리결합뿐 아니라 간접 기능적 연관도 포함한다. 따라서 네트워크 유의성은 인과성·직접 결합·위암 특이성을 증명하지 않는다. STRING 자체 pathway enrichment도 같은 입력과 지식베이스를 재사용하므로 독립 검증이 아니다. [STRING 방법론 논문](https://pubmed.ncbi.nlm.nih.gov/36370105/)

## 9. 짧은 문헌 해석

GKN1/GKN2, ATP4A/ATP4B, PGC, LIPF의 감소는 표면점액·벽세포·주세포를 포함한 정상 위선 분화 및 기능 프로그램의 소실과 일치한다. 독립 위암 단일세포 연구는 ATP4A를 벽세포, LIPF·PGC를 주세포 계통과 연결했으며, 일부 위암 아형은 이 분화 표지를 유지할 수도 있음을 보여준다. 따라서 이 패턴을 모든 위암의 보편적 탈분화로 확대하지 않는다. [위암 단일세포 연구](https://pmc.ncbi.nlm.nih.gov/articles/PMC7873416/), [GKN1/GKN2 동시 소실 연구](https://pmc.ncbi.nlm.nih.gov/articles/PMC9373792/)

COL1A1, COL11A1, INHBA, CTHRC1, THBS2의 증가는 CAF·collagen·ECM 재편과 잘 맞는다. 위암 단일세포·공간 연구는 INHBA–FAP fibroblast 축과 COL1A1의 연관, fibroblast cluster의 THBS2·CTHRC1 발현을 지지한다. [위암 single-cell atlas](https://pmc.ncbi.nlm.nih.gov/articles/PMC9394383/)

SPP1은 위암에서 SPP1-positive macrophage가 중요한 공급원일 수 있고, MMP7은 전형적인 stromal marker라기보다 종양 상피의 침윤성 matrix-remodelling 표지에 가깝다. [SPP1 위암 연구](https://pubmed.ncbi.nlm.nih.gov/36612160/), [MMP7 위암 조직 연구](https://pubmed.ncbi.nlm.nih.gov/8949652/)

공식 Hallmark EMT 카드에는 COL1A1, COL11A1, SPP1, INHBA, CTHRC1, THBS2가 포함되지만 MMP7은 포함되지 않는다. 따라서 양의 bulk EMT NES는 세포자율적 epithelial-to-mesenchymal conversion의 직접 증거가 아니라 CAF·macrophage·정상 위선 세포 감소가 섞인 **EMT/mesenchymal–stromal 재편 프로그램**으로 해석하는 것이 안전하다.

## 10. 다운로드 없는 재현 manifest

`null`은 값을 추정하지 않았음을 뜻한다. 실제 실행 시 해당 값을 채우지 않으면 완전한 재현 실행으로 간주하지 않는다.

```yaml
manifest_version: "1.0"
analysis_id: "GSE79973_paired_plan_v1"
status:
  mode: "plan_only"
  data_downloaded: false
  analysis_executed: false

snapshot:
  source: "user-provided fixed snapshot"
  capture_time_utc: null
  accession: "GSE79973"
  platform: "GPL570"
  organism: "Homo sapiens"
  n_tumor: 10
  n_normal: 10
  n_pairs: 10
  series_matrix_size_mb_approx: 4.7
  source_url: "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE79973"

input_artifact:
  retrieved: false
  resolved_url: null
  filename: null
  byte_size: null
  sha256: null
  http_etag: null
  http_last_modified: null

sample_manifest:
  filename: "sample_manifest.tsv"
  sha256: null
  required_columns:
    - series_accession
    - sample_accession
    - matrix_column
    - platform_id
    - sample_title_raw
    - tissue_raw
    - pair_id
    - condition
    - pairing_source
    - included
    - exclusion_reason
  assertions:
    n_samples: 20
    n_tumor: 10
    n_normal: 10
    n_complete_pairs: 10
    one_tumor_and_one_normal_per_pair: true

preprocessing:
  source_processing_text: null
  source_scale: null
  transform_applied: null
  renormalized: false
  missing_value_rule: "complete 20-sample rows for primary model"
  qc_exclusions_frozen_before_de: true

annotation:
  probe_namespace: "GPL570 probe set"
  primary_gene_key: "ENTREZID"
  display_key: "approved gene symbol"
  annotation_package: "hgu133plus2.db"
  annotation_version: null
  orgdb_version: null
  mapping_table: "probe_gene_map.tsv"
  mapping_sha256: null
  ambiguous_mapping_rule: "exclude from gene-level primary analysis"
  multi_probe_rule: "highest median expression across all samples"
  tie_break: "lexical probe_set_id"

differential_expression:
  contrast: "Tumor - Normal"
  design_formula: "~ pair_id + condition"
  expected_design_rank: 11
  expected_residual_df: 9
  method: "limma"
  ebayes_trend: true
  ebayes_robust: true
  treat_lfc: 1
  multiple_testing: "BH"
  deg_fdr: 0.05

direction_controls:
  down:
    - GKN1
    - GKN2
    - ATP4A
    - ATP4B
    - PGC
    - LIPF
  up:
    - COL1A1
    - COL11A1
    - SPP1
    - INHBA
    - MMP7
    - CTHRC1
    - THBS2
  expected_down_matches: "6/6"
  expected_up_matches: "7/7"
  expected_total_matches: "13/13"
  observed_matches: null

ora:
  libraries_and_releases: null
  universe: "all tested genes mapped to each library"
  directions_separate: true
  min_set_size: 15
  max_set_size: 500
  min_overlap: 3
  fdr: 0.05

gsea:
  collection: "MSigDB Human Hallmark"
  collection_release: null
  gmt_sha256: null
  rank_metric: "paired limma moderated t"
  rank_direction: "positive = Tumor-high"
  algorithm: "fgseaMultilevel"
  gsea_param: 1
  min_size: 15
  max_size: 500
  eps: 0
  seed: 79973
  threads: 1
  prespecified_set: "HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION"
  expected_nes_sign: "positive"
  fdr: 0.05
  paired_null: "exhaustive within-pair sign flips"
  paired_null_configurations: 1024
  leave_one_pair_out_runs: 10
  directional_stability_required: ">=8/10 positive"

string:
  api_version: null
  versioned_base_url: null
  request_time_utc: null
  species: 9606
  expected_id_regex: "^9606\\.ENSP[0-9]+$"
  network_type: "functional"
  required_score: 700
  sensitivity_scores: [400, 900]
  add_nodes: 0
  background: "all tested genes successfully mapped to STRING"
  mapping_request_sha256: null
  mapping_response_sha256: null
  network_request_sha256: null
  network_response_sha256: null
  enrichment_response_sha256: null

software:
  R_version: null
  Bioconductor_version: null
  packages:
    limma: null
    AnnotationDbi: null
    hgu133plus2.db: null
    org.Hs.eg.db: null
    fgsea: null
  renv_lock_sha256: null
  session_info_sha256: null
  code_commit: null
  container_image_digest: null
  locale: null
  operating_system: null

outputs:
  - sample_manifest.tsv
  - probe_gene_map.tsv
  - paired_de_all.tsv
  - direction_controls.tsv
  - ora_results.tsv
  - gsea_hallmark.tsv
  - gsea_emt_signflip.tsv
  - string_mapping.tsv
  - string_edges.tsv
  - string_enrichment.tsv
  - sessionInfo.txt
  output_sha256_manifest: null
```

## 결론

현재 확정할 수 있는 것은 **10쌍의 paired 설계, 13개 방향성 검사 항목, EMT의 양의 NES 가설, STRING의 human taxon 9606 및 `9606.ENSP...` 형식**뿐이다. 실제 DEG·pathway·NES·STRING 네트워크 결과는 모두 미산출이며, 위 판정 기준과 manifest가 채워진 뒤에만 결과로 보고한다.