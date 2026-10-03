> labhq 모의 시운전 10차(2026-10-03)가 만든 최종 보고서 원문입니다. 문장 끝의 `[[claim:<단계>/<claim>]]`은
> 그 수치의 근거 claim 표시이고, labhq가 기계로 검사합니다(앵커 58개, 문제 0). 경로는 각 단계 작업 폴더 기준입니다.

# GSE10072 폐선암 대 정상 폐조직 재분석: 최종 보고서

## 1) 결론

**짝 구조와 주 모형**
- GEO title에 붙은 환자 ID를 기준으로 보면 107개 샘플은 환자 74명에게서 나왔습니다. 그중 종양·정상 완전 짝은 33쌍입니다 [[claim:s2_acquire_normcheck/pairing_status]].
- 사전 규칙(완전 짝 10쌍 이상)에 따라 주 모형은 107개 샘플 전체에 적합한 혼합모형 `expr ~ tissue + (1|patient)`입니다 [[claim:s2_acquire_normcheck/pairing_status]].

**차등발현(DE)**
- 주 모형 기준으로 FDR<0.05이면서 |log2FC|≥1인 DE 유전자는 554개입니다. 상향 186개, 하향 368개입니다 [[claim:s5_de/de_primary_result]].
- 민감도 모형 7개에서도 결과는 안정적입니다. 주 모형과 각 민감도 모형 모두에서 DE인 유전자 중 방향이 바뀐 유전자는 없습니다 [[claim:s5_de/de_robustness]].

**pathway**
- 종양 쪽에는 증식·대사 프로그램이 농축됐습니다. Myc Targets V1, E2F Targets, G2-M Checkpoint, Glycolysis, mTORC1 등입니다 [[claim:s7_gsea_ora/gsea_result]].
- 정상 쪽에는 혈관·면역·기질 프로그램이 농축됐습니다. Myogenesis, TNF-alpha/NF-kB, Complement and coagulation cascades 등입니다 [[claim:s7_gsea_ora/gsea_result]].

**STRING 허브**
- 상위 400개 DE 유전자로 만든 네트워크의 허브는 CDK1, CCNB1, TOP2A 등 세포주기 유전자입니다 [[claim:s8_string_hub/string_hubs]].
- 다만 이 허브 목록은 입력 크기에 좌우됩니다. 상위 200개를 입력하면 허브가 혈관내피 유전자로 바뀌고, 두 목록에 공통인 유전자가 하나도 없습니다 [[claim:s8_string_hub/string_hubs]]. 따라서 허브 결론은 "입력을 이렇게 골랐을 때"라는 조건이 붙은 결과로만 봐야 합니다.

**k-means 군집**
- 조직 라벨 없이 군집화해도 k=2가 최적입니다. 두 군집은 조직 라벨과 거의 일치합니다(ARI 0.926) [[claim:s9_kmeans/kmeans_clusters]].

**해석상 주의**
- 모든 결과는 관찰 데이터에서 본 연관입니다. 인과를 뜻하지 않습니다 [[claim:s5_de/de_primary_result]].
- 정상 쪽 신호 일부가 조직의 세포 구성 차이에서 왔을 수 있다는 해석은 아직 가설입니다 [[claim:s11_bio_interpretation/bio_interpretation]].

## 2) claim별 근거

경로는 각 단계 작업폴더(`task_<id>/outputs/...`) 기준입니다.

| 단계 | 작업폴더 |
|---|---|
| s1 | task_c370ee2781_engineer |
| s2 | task_60ffdcff7a_analyst |
| s3 | task_019d450d11_data_steward |
| s4 | task_4a06507fe7_qc_reviewer |
| s5 | task_12b4e3a04e_analyst |
| s6 | task_6dced3d3ef_qc_reviewer |
| s7 | task_13d55e979b_analyst |
| s8 | task_c1383236ea_analyst |
| s9 | task_14df50e92a_analyst |
| s10 | task_681682d3ff_qc_reviewer |
| s11 | task_5642db5166_biologist |
| s12 | task_a6c0ec4dd0_analyst |

**환경 (s1)** — `outputs/env/requirements.lock.txt`, `outputs/env/import_check.txt`
- PI가 승인한 패키지와 그 의존성을 작업폴더의 `.venv`(Python 3.12.10)에 설치했습니다. 필수 패키지 9개의 import와 pip check가 모두 통과했습니다 [[claim:s1_env/env_ready]].

**데이터 확보 (s2)** — `outputs/download_log.tsv`, `outputs/sample_metadata.tsv`
- Series Matrix(107 샘플 × 22,283 프로브)와 GPL96.annot을 GEO에서 받았습니다. gzip 무결성을 확인했고 sha256을 기록했습니다 [[claim:s2_acquire_normcheck/data_obtained]].
- 종양 58개, 정상 49개입니다. 이는 GEO의 실험 설계 기술과 일치하고, 조직 라벨은 107개 모두 확정됐습니다 [[claim:s2_acquire_normcheck/data_obtained]].
- CEL 원본(RAW.tar)은 존재만 확인했고 내려받지 않았습니다 [[claim:s2_acquire_normcheck/data_obtained]].

**짝 구조 (s2)** — `outputs/pairing_audit.tsv`, `outputs/pairing_summary.json`
- 환자 74명 가운데 완전 짝은 33쌍입니다. 종양만 있는 환자는 25명, 정상만 있는 환자는 16명입니다 [[claim:s2_acquire_normcheck/pairing_status]].

**정규화 상태 (s2)** — `outputs/normalization_report.md`, `outputs/norm_boxplot_density.png`, `outputs/pca.png`, `outputs/outlier_candidates.tsv`, `outputs/expression_gene_level.tsv`
- Series Matrix 값은 이미 log2 척도의 저자 RMA 정규화값이라 변환하지 않았습니다. 값 범위는 3.67–15.25, 샘플별 중앙값은 7.37–7.48입니다 [[claim:s2_acquire_normcheck/normalization_status]].
- 가장 큰 변이축은 조직 유형이며 PC1이 분산의 27.3%를 설명합니다 [[claim:s2_acquire_normcheck/normalization_status]].
- 조직 내부 기준으로 이상치 후보 어레이는 5개입니다 [[claim:s2_acquire_normcheck/normalization_status]].
- 최종 분석 행렬은 12,502 유전자 × 107 샘플입니다 [[claim:s2_acquire_normcheck/normalization_status]].

**매니페스트 (s3)** — `outputs/data_manifest.json`, `outputs/checksum_verification.tsv`, `outputs/metadata_dictionary.tsv`
- 원본 압축파일 2개의 sha256을 다시 계산했더니 기록과 일치했습니다 [[claim:s3_manifest/manifest_complete]].

**데이터 QC (s4)** — `outputs/qc_data_report.md`, `outputs/qc_data_verdict.json`
- 종합 판정은 PASS입니다. 체크섬 항목만 WARN인데, 파생 산출물 11개는 비교할 이전 기록이 없어 이번에 처음 기록했기 때문입니다 [[claim:s4_qc_data/data_qc_verdict]].

**DE 주 결과 (s5)** — `outputs/de_primary.tsv`, `outputs/de_summary.json`, `outputs/volcano.png`, `outputs/top50_heatmap.png`
- 대표 유전자는 다음과 같습니다 [[claim:s5_de/de_primary_result]].
  - 하향: FAM107A, CDH5, PECAM1, VWF, TCF21, AGER, FABP4, CLDN18, SFTPC
  - 상향: SPP1(log2FC 4.4), MMP12, TOP2A, COL1A1, COL10A1, CEACAM5
- 혼합모형이 수렴하지 않은 유전자는 5개(0.04%)입니다. 사전 규칙대로 이 5개는 짝 moderated t 결과로 대체했습니다 [[claim:s5_de/de_primary_result]].

**DE 견고성 (s5)** — `outputs/de_sensitivity.tsv`, `outputs/de_concordance.tsv`
- 주 모형 대비 log2FC의 Spearman 상관은 0.985–0.999입니다 [[claim:s5_de/de_robustness]].
- DE 집합 Jaccard는 0.86–0.98입니다. 흡연+성별을 보정하면 0.94입니다 [[claim:s5_de/de_robustness]].

**DE QC (s6)** — `outputs/qc_de_report.md`, `outputs/qc_de_verdict.json`
- 판정은 PASS입니다. 혼합모형 미수렴률이 5% WARN 기준보다 훨씬 낮고, p 분포도 정상입니다 [[claim:s6_qc_de/de_qc_verdict]].

**GSEA (s7)** — `outputs/gsea_hallmark.tsv`, `outputs/gsea_kegg.tsv`, `outputs/gsea_top_nes.png`, `outputs/gsea_enrichment_plots.png`
- 설정: 주 혼합모형의 Wald z로 순위를 매긴 gseapy prerank, permutation 1000회 [[claim:s7_gsea_ora/gsea_result]].
- 종양 쪽 상위 셋은 모두 q<0.001입니다 [[claim:s7_gsea_ora/gsea_result]].
  - Hallmark: Myc Targets V1(NES 3.13), E2F Targets(3.11), G2-M Checkpoint(2.89)
  - KEGG: Cell cycle(2.45), Proteasome(2.40), DNA replication(2.33)
- q<0.05인 셋은 Hallmark 27/50개, KEGG 104/301개입니다 [[claim:s7_gsea_ora/gsea_result]].

**ORA (s7)** — `outputs/ora_results.tsv`, `outputs/ora_dotplot.png`
- 상향 집합:
  - E2F Targets(OR 11.9, FDR 1.1e-15), G2-M Checkpoint(OR 10.5), EMT(OR 8.6) [[claim:s7_gsea_ora/ora_result]]
  - KEGG p53 signaling(OR 11.3) [[claim:s7_gsea_ora/ora_result]]
- 하향 집합:
  - TNF-alpha/NF-kB(OR 5.9, FDR 1.4e-10) [[claim:s7_gsea_ora/ora_result]]
  - Complement and coagulation cascades(OR 7.5) [[claim:s7_gsea_ora/ora_result]]
- EMT는 ORA 상향에서만 유의하고 GSEA에서는 q<0.05에 들지 않았습니다 [[claim:s7_gsea_ora/ora_result]].

**STRING 허브 (s8)** — `outputs/string_mapping.tsv`, `outputs/string_edges.tsv`, `outputs/hub_genes.tsv`, `outputs/hub_sensitivity.tsv`, `outputs/hub_literature_overlap.tsv`, `outputs/string_log.md`
- 입력 400개 중 398개가 STRING에 매핑됐고 edge는 495개입니다 [[claim:s8_string_hub/string_hubs]].
- degree 상위 10개 허브는 CDK1, CCNB1, RRM2, CDC20, CCNB2, BUB1B, TOP2A, MAD2L1, TPX2, NUSAP1입니다. 문헌에서 반복 보고된 허브 7개 중 3개(CDC20, CCNB2, TOP2A)와 겹칩니다 [[claim:s8_string_hub/string_hubs]].
- 이 claim의 판정은 partially_supported입니다. 입력 크기에 따라 허브 목록이 바뀌기 때문입니다 [[claim:s8_string_hub/string_hubs]].

**k-means (s9)** — `outputs/kmeans_silhouette.tsv`, `outputs/kmeans_assignments.tsv`, `outputs/cluster_composition.tsv`, `outputs/cluster_markers.tsv`, `outputs/cluster_ora.tsv`, `outputs/kmeans_pca.png`, `outputs/kmeans_stability.tsv`
- k=2 결과(silhouette 0.249):
  - 군집 C1: 종양 58개 + 정상 2개 [[claim:s9_kmeans/kmeans_clusters]]
  - 군집 C2: 정상 47개 [[claim:s9_kmeans/kmeans_clusters]]
- 유전자 1000개·5000개로 바꿔도 최적 k는 2입니다. ARI는 각각 0.926과 1.000으로 안정적입니다 [[claim:s9_kmeans/kmeans_clusters]].
- 차선 해 k=3에서는 종양 13개로 된 증식형 소군집이 나옵니다. 그러나 이 분할은 덜 안정적이라(ARI 0.70) 서술용으로만 씁니다 [[claim:s9_kmeans/kmeans_clusters]].

**하류 QC (s10)** — `outputs/qc_downstream_report.md`, `outputs/qc_downstream_verdict.json`
- 모듈별 판정:
  - s7 GSEA/ORA: PASS
  - s8 STRING 허브: WARN (허브가 입력 크기에 따라 바뀜)
  - s9 k-means: WARN (군집 marker 검정이 짝 샘플을 독립 표본으로 취급)
- 조회 실패를 음성 결과처럼 쓴 경우나 몰래 다른 방법으로 대체한 경우는 없었습니다 [[claim:s10_qc_downstream/downstream_qc_verdict]].

**생물학적 해석 (s11)** — `outputs/bio_interpretation.md`
- 종양-정상 차이는 두 축으로 정리됩니다 [[claim:s11_bio_interpretation/bio_interpretation]].
  - 종양 쪽 상향: 증식·대사·기질
  - 정상 쪽 상향: 폐포 상피·내피·평활근
- 흡연+성별을 보정해도 DE 신호는 유지됩니다(Jaccard 0.945) [[claim:s11_bio_interpretation/bio_interpretation]].

**문헌 대조 (s11)** — `outputs/literature_concordance.tsv`
- Landi 2008이 보고한 NEK2·TTK·PRC1은 이번 분석에서도 종양 상향 DE입니다(log2FC 1.14–1.48) [[claim:s11_bio_interpretation/literature_concordance]].
- 다만 대조한 재분석 문헌 대부분이 GSE10072를 다시 쓴 것이라 이 일치는 독립 검증이 아닙니다 [[claim:s11_bio_interpretation/literature_concordance]].

**종합 보고서와 색인 (s12)** — `outputs/final_report.md`, `outputs/results_index.tsv`
- s1–s11 산출물을 종합했습니다. 색인은 57행입니다 [[claim:s12_report/final_report]].

## 3) 확립되지 않은 것

**세포 구성**
- 세포 구성(purity, 내피·폐포 세포 비율)이 DE와 정상 쪽 pathway 신호에 얼마나 기여하는지는 모릅니다. deconvolution을 하지 않았습니다.

**정규화**
- 저자 RMA 값과 CEL을 직접 RMA 재처리한 값이 일치하는지는 확인하지 않았습니다. R이 없어 범위 밖으로 두었습니다.
- 기술 반복을 평균한 값이 어느 GSM인지는 GEO 메타데이터로 알 수 없습니다. 그래서 분산에 미친 영향도 판단할 수 없습니다.
- 이상치 후보가 기술적 결함인지 생물학적 이질성인지 판정하지 않았습니다. 정상 샘플 2개(GSM254632, GSM254723)가 종양 군집에 들어간 원인도 같은 이유로 미확인입니다.

**짝 구조**
- patient_id는 GEO 공식 필드가 아니라 title 문자열 패턴에서 해석한 값입니다. 임상 필드와 정합성은 확인했지만 완전한 검증은 아닙니다.
- GEO가 공표한 제3자 체크섬이 없습니다. 그래서 GEO 서버 원본과 독립적으로 대조하지 못했습니다.

**DE 방법**
- Python으로 구현한 moderated t·MixedLM이 limma/lme4와 수치로 같은지 직접 대조하지 못했습니다. 무작위 데이터 단위 검증만 했습니다.
- 소표본 자유도 보정(Kenward-Roger/Satterthwaite)을 하면 p값과 DE 판정이 어떻게 바뀌는지 평가하지 않았습니다.
- 흡연×조직 상호작용은 검정하지 않았습니다. 따라서 "흡연자 종양에서 mitotic 유전자가 더 높다"는 Landi 2008의 핵심 주장도 재현하지 않았습니다.
- 흡연 상태로 샘플이 갈리는지는 PCA 그림을 눈으로 본 평가일 뿐입니다.

**GSEA·ORA**
- 최신 MSigDB Hallmark·KEGG 판으로 해도 GSEA 결과가 같은지 확인하지 않았습니다. 이번에는 Enrichr 2020/2021 판만 썼습니다.
- gene-set permutation은 유전자 간 상관을 보존하지 못합니다. 그래서 GSEA q값이 낙관적일 수 있습니다.
- DE 컷오프를 바꿨을 때 ORA가 어떻게 달라지는지는 확인하지 않았습니다.

**STRING 허브**
- 허브 견고성을 평가하지 않았습니다. 방향별 입력, |log2FC| 순 입력, 무작위 네트워크 대비 유의성 모두 해 보지 않았습니다.
- UBE2T가 유전자 행렬에 없는 원인과 AQP4가 STRING에 매핑되지 않은 원인은 확인하지 않았습니다.

**k-means**
- 환자 구조를 반영한 군집 marker 유의성은 계산하지 않았습니다.
- 부트스트랩·서브샘플 기반 군집 안정성은 평가하지 않았습니다.
- k=3 증식형 소군집과 흡연의 연관은 검정하지 않았습니다.

**해석·외부 비교**
- 외부 독립 코호트(TCGA-LUAD 등)와 비교하지 않았습니다. 프로토콜 범위 밖입니다.
- TNF-α/NF-κB 하향이 수술 시 허혈·채취 스트레스 때문인지, 실제 염증 차이 때문인지 모릅니다.
- Ni & Sun 2019(PMID 31698633)에서 상향·하향 라벨이 반대인 원인은 원문을 확인하지 않았습니다.

**실패하거나 비어 있던 조회** (실패나 빈 결과는 증거도 아니고, 부재의 증명도 아닙니다)
- PubMed 일괄 메타데이터 조회(17 PMID): 출력이 접근 금지 경로에 저장돼 열지 않았습니다. 다시 조회하지 않은 5편(41491963, 40826767, 40361912, 40282196, 37056815)은 읽지 못했습니다.
- PubMed 질의 2건은 0 hits였습니다. 질의가 지나치게 결합적이었을 가능성이 있습니다.
  - "lung adenocarcinoma integrated bioinformatics GEO hub genes CDK1 CCNB1 TOP2A protein-protein interaction"
  - "lung adenocarcinoma tumor purity stromal immune admixture gene expression ESTIMATE"
- 리뷰어가 GEO 웹 원문에 다시 접속하려 했으나 CAPTCHA와 접근 오류로 실패했습니다.

**기록된 방법 변경**
- 이상치 규칙: 방향과 기준을 바로잡았습니다. PCA 거리는 "초과"로 판정했고, 조직 내부 기준을 썼습니다.
- 혼합모형 옵티마이저: 순차 fallback을 썼습니다.
- 민감도 분석: 계획보다 추가했습니다(raw-MAD 이상치 제외, GLS SE).
- ORA: 유전자셋 크기를 15–500으로 제한했습니다.
- k-means: 최적 k가 2라서 계획한 비교가 무의미해져, 차선 해 k=3을 추가했습니다. 군집 marker ORA는 방향별로 검정했습니다.
- 환경: 초기 pip 설치에서 TEMP/TMP를 지정하지 않았습니다.

## 4) 한계

리뷰어 판정은 accept입니다. P1 문제는 없었고 P2 지적은 7건입니다.

1. **세포 구성 해석의 강도 (P2)**
   - s11 해석 메모 요약은 "(B)의 상당 부분은 세포 구성 차이로 설명된다"고 단정합니다. purity 추정을 하지 않았으므로 이 보고서는 "기여했을 가능성이 있으나 기여도는 확인하지 않았다"로 제한합니다 [[claim:s11_bio_interpretation/bio_interpretation]].
2. **"독립적 재현" 표현 (P2)**
   - GSEA, ORA, 허브, 군집은 모두 같은 발현 행렬과 같은 DE 결과에서 나왔습니다. 따라서 네 분석의 일치는 "동일 데이터의 여러 분석에서 일관된 패턴"일 뿐, 독립 검증으로 세지 않습니다 [[claim:s10_qc_downstream/downstream_qc_verdict]].
3. **군집 marker의 선택 편향 (P2)**
   - 같은 데이터로 군집을 만들고 그 군집의 marker를 검정했습니다. 그래서 Welch p와 BH-FDR은 군집 선택 과정을 반영하지 않습니다. 짝 샘플 독립 가정 문제와는 별개의 문제입니다.
   - 따라서 `cluster_markers.tsv`와 `cluster_ora.tsv`는 효과 크기와 순위 중심의 서술적 특징으로만 씁니다 [[claim:s9_kmeans/kmeans_clusters]].
4. **135→122→107 샘플 처리 경로 (P2)**
   - s12 보고서는 원 논문 135 샘플과 GEO 107 값의 차이 원인이 "미확인"이라고 썼습니다. 리뷰어가 원문에서 처리 경로를 확인했습니다: 135개 어레이를 정규화한 뒤 종양세포 비율이 낮은 13개를 제외했고, 남은 122개에서 기술 반복을 평균해 107개 값을 얻었습니다.
   - 따라서 남은 미확인 사항은 "어느 GSM이 반복 평균값인지" 하나뿐입니다 [[claim:s2_acquire_normcheck/normalization_status]].
5. **QC의 CI 공식 구분 누락 (P2)**
   - CI 오차가 컸던 5개 행은 모두 미수렴으로 짝 moderated t로 대체된 행입니다. 이 행들은 정규 임계값이 아니라 t 임계값을 씁니다. s6 QC는 두 CI 공식을 구분하지 않고 통과를 설명했습니다.
   - 즉 주 결과에도 moderated t 대체 행 5개가 들어 있습니다 [[claim:s6_qc_de/de_qc_verdict]].
6. **"환자 분산 0" 표현 (P2)**
   - "36%의 유전자에서 환자 분산이 0인 경계해이며 OLS와 같다"는 표현은 과합니다. 실제 의미는 "환자 분산이 0.01 미만인 낮은 분산 플래그"이며, 정확한 경계해가 아닌 유전자도 포함됩니다. 이 플래그를 근거로 환자 효과가 중요하지 않다고 해석하면 안 됩니다 [[claim:s5_de/de_primary_result]].
7. **재현성 산출물 미보존 (P2)**
   - 분석 코드, 선택 프로브 대응표, 사용한 GMT 사본이 `.tmp`에만 있고 결과 색인의 수집 대상이 아닙니다. 그래서 자체 구현한 moderated t를 포함해 전체 분석을 재현하기 어렵습니다 [[claim:s12_report/final_report]].
8. **참고: P3 지적**
   - 방향 뒤집힘 0건은 "두 모형 모두에서 DE인 유전자" 범위에서만 성립합니다. 전체 유전자로 넓히면 부호가 바뀐 유전자가 있습니다 [[claim:s5_de/de_robustness]].
   - 소표본 보정이 p를 키운다는 s12 문장은 평가하지 않은 내용입니다.

**방법상 한계 (대체 방법)**
- CEL RMA 재처리 대신 저자 정규화값을 썼습니다 [[claim:s2_acquire_normcheck/normalization_status]].
- limma 대신 statsmodels MixedLM과 Python moderated t를 썼습니다. Wald z에는 소표본 자유도 보정이 없습니다 [[claim:s5_de/de_primary_result]].

## 5) 결론을 바꿀 관찰과 다음 단계

**결론을 바꿀 관찰**
- **세포 구성 보정 후 정상 쪽 신호 감소**: purity를 보정했을 때 내피·폐포·혈관 신호가 크게 줄면, 정상 쪽 pathway를 "조직 프로그램 차이"로 읽는 해석이 약해집니다.
- **외부 코호트 불일치**: TCGA-LUAD 등 독립 코호트에서 DE 방향이나 증식 축이 재현되지 않으면, 이번 결과는 GSE10072에만 해당하는 결과로 격하됩니다.
- **소표본 보정 후 DE 집합 변화**: Satterthwaite나 Kenward-Roger 보정, 또는 R limma/lme4와 대조했을 때 DE 집합이 크게 달라지면 DE 수치를 다시 확정해야 합니다.
- **허브 순위의 불안정**: 방향별 입력, |log2FC| 순 입력, 무작위 네트워크 대비 검정에서 세포주기 허브가 유지되지 않으면, 허브 결론은 철회해야 합니다.
- **흡연×조직 상호작용**: 상호작용이 유의하면 주 추정치(흡연 비보정 평균 차이) 해석에 단서를 달아야 합니다.

**다음 단계** (모두 새 계획과 CP1 승인이 필요합니다)
1. **재현성 보존 (추가 분석 없음)**: 분석 코드, 프로브 대응표, GMT 사본을 영구 산출물로 옮기고 결과 색인과 연결합니다. P2 지적 1, 2, 4, 6의 문구를 상류 보고서에서 수정합니다.
2. **세포 구성 추정**: ESTIMATE나 xCell류 도구로 세포 구성을 추정하고 보정합니다. 승인 목록 밖 패키지가 필요하면 설치 전에 승인을 받습니다.
3. **허브 견고성 검사**: 방향별·|log2FC| 순 입력과 무작위 네트워크 대비 검정을 합니다.
4. **흡연 상호작용 검정**: tissue×smoking 상호작용 모형을 적합합니다.
5. **외부 코호트 검증**: TCGA-LUAD로 검증합니다. 범위가 확장되므로 PI 승인이 필요합니다.

CP2 evidence review: approved.
PI note: 승인합니다. 33쌍 MixedLM, DE 554(양성 대조 NEK2·TTK·PRC1 상향), 민감도 7모형 방향 뒤집힘 0, 방법 변경은 모두 method_changes에 기록됨, 거부 근거 0. STRING 허브 순위 불안정은 partially_supported로 표시된 그대로 둡니다.

실패한 조회 — 증거도 부재 증명도 아님:
- s11_bio_interpretation/ev_search_empty (not_found): PubMed 질의 2건은 결과가 없었다(0 hits).; query: 'lung adenocarcinoma integrated bioinformatics GEO hub genes CDK1 CCNB1 TOP2A protein-protein interaction'; 'lung adenocarcinoma tumor purity stromal immune admixture gene expression ESTIMATE' (PubMed, no date filter)
- s11_bio_interpretation/ev_unread_pmids (unavailable): 재분석 17개 PMID를 한꺼번에 메타데이터 조회했더니 출력이 ~/.claude 아래 파일로 저장됐다. 그 경로는 랩 규칙상 열 수 없어 작은 묶음으로 다시 조회했다. 5편(41491963, 40826767, 40361912, 40282196, 37056815)은 열람하지 못했다.; 초기 일괄 조회 출력이 접근 금지 경로에 저장되었다. 이 5편은 예산·범위 때문에 다시 조회하지 않았다.; query: GSE10072 lung adenocarcinoma hub genes

Step status and output paths:
- s1_env: done; outputs: task_c370ee2781_engineer/outputs/env/requirements.lock.txt, task_c370ee2781_engineer/outputs/env/import_check.txt
- s2_acquire_normcheck: done; outputs: task_60ffdcff7a_analyst/outputs/sample_metadata.tsv, task_60ffdcff7a_analyst/outputs/pairing_audit.tsv, task_60ffdcff7a_analyst/outputs/pairing_summary.json, task_60ffdcff7a_analyst/outputs/norm_boxplot_density.png, task_60ffdcff7a_analyst/outputs/pca.png, task_60ffdcff7a_analyst/outputs/sample_corr_heatmap.png, task_60ffdcff7a_analyst/outputs/outlier_candidates.tsv, task_60ffdcff7a_analyst/outputs/expression_gene_level.tsv, task_60ffdcff7a_analyst/outputs/probe_filter_log.tsv, task_60ffdcff7a_analyst/outputs/download_log.tsv, task_60ffdcff7a_analyst/outputs/normalization_report.md
- s3_manifest: done; outputs: task_019d450d11_data_steward/outputs/data_manifest.json, task_019d450d11_data_steward/outputs/checksum_verification.tsv, task_019d450d11_data_steward/outputs/metadata_dictionary.tsv
- s4_qc_data: done; outputs: task_4a06507fe7_qc_reviewer/outputs/qc_data_report.md, task_4a06507fe7_qc_reviewer/outputs/qc_data_verdict.json
- s5_de: done; outputs: task_12b4e3a04e_analyst/outputs/de_primary.tsv, task_12b4e3a04e_analyst/outputs/de_sensitivity.tsv, task_12b4e3a04e_analyst/outputs/de_summary.json, task_12b4e3a04e_analyst/outputs/de_concordance.tsv, task_12b4e3a04e_analyst/outputs/volcano.png, task_12b4e3a04e_analyst/outputs/top50_heatmap.png, task_12b4e3a04e_analyst/outputs/pvalue_hist.png, task_12b4e3a04e_analyst/outputs/modt_validation.md, task_12b4e3a04e_analyst/outputs/de_run_log.md
- s6_qc_de: done; outputs: task_6dced3d3ef_qc_reviewer/outputs/qc_de_report.md, task_6dced3d3ef_qc_reviewer/outputs/qc_de_verdict.json
- s7_gsea_ora: done; outputs: task_13d55e979b_analyst/outputs/gsea_hallmark.tsv, task_13d55e979b_analyst/outputs/gsea_kegg.tsv, task_13d55e979b_analyst/outputs/gsea_top_nes.png, task_13d55e979b_analyst/outputs/gsea_enrichment_plots.png, task_13d55e979b_analyst/outputs/ora_results.tsv, task_13d55e979b_analyst/outputs/ora_dotplot.png, task_13d55e979b_analyst/outputs/geneset_log.md
- s8_string_hub: done; outputs: task_c1383236ea_analyst/outputs/string_mapping.tsv, task_c1383236ea_analyst/outputs/string_edges.tsv, task_c1383236ea_analyst/outputs/hub_genes.tsv, task_c1383236ea_analyst/outputs/string_network.png, task_c1383236ea_analyst/outputs/hub_literature_overlap.tsv, task_c1383236ea_analyst/outputs/hub_sensitivity.tsv, task_c1383236ea_analyst/outputs/string_log.md
- s9_kmeans: done; outputs: task_14df50e92a_analyst/outputs/kmeans_silhouette.tsv, task_14df50e92a_analyst/outputs/kmeans_silhouette.png, task_14df50e92a_analyst/outputs/kmeans_assignments.tsv, task_14df50e92a_analyst/outputs/cluster_composition.tsv, task_14df50e92a_analyst/outputs/cluster_markers.tsv, task_14df50e92a_analyst/outputs/cluster_ora.tsv, task_14df50e92a_analyst/outputs/kmeans_pca.png, task_14df50e92a_analyst/outputs/kmeans_heatmap.png, task_14df50e92a_analyst/outputs/kmeans_stability.tsv
- s10_qc_downstream: done; outputs: task_681682d3ff_qc_reviewer/outputs/qc_downstream_report.md, task_681682d3ff_qc_reviewer/outputs/qc_downstream_verdict.json
- s11_bio_interpretation: done; outputs: task_5642db5166_biologist/outputs/bio_interpretation.md, task_5642db5166_biologist/outputs/literature_concordance.tsv
- s12_report: done; outputs: task_a6c0ec4dd0_analyst/outputs/final_report.md, task_a6c0ec4dd0_analyst/outputs/results_index.tsv

비용: 확인 $16.15 + 추정 $4.32 (claude_code 확인 $16.15 · codex 추정 $4.32); 추정은 판본 있는 API 가격표 환산이며 청구액이 아닙니다