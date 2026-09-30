파일 쓰기 권한이 없어 대신 보고서를 바로 본문에 Markdown으로 제시합니다.

---

# GSE79973 Paired Tumor/Normal 분석 설계 보고서

- 작성일: 2026-10-01
- 성격: **분석 설계 문서(design/manifest)**. 이번 실행에서는 GEO/STRING/문헌 데이터를 실제로 내려받거나 계산하지 않았습니다. 모든 수치는 아래 "고정 snapshot" 항목만 인용하고, 그 외 수치(정확한 fold-change, p-value, NES, STRING score 등)는 추정하지 않고 "실행 시 산출" 항목으로 명시합니다.

---

## 1. 목적

`GSE79973` (위암 tumor/normal paired 마이크로어레이) 에 대해 아래 4단계를 다운로드 없이 재현 가능하도록 설계합니다.

1. Paired differential expression 분석
2. Pathway/GSEA 검증
3. STRING PPI 네트워크 검증
4. 관련 유전자에 대한 짧은 문헌 요약

---

## 2. 고정 Snapshot (입력 사실, 출처 명시)

출처: https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE79973

| 항목 | 값 |
|---|---|
| Accession | GSE79973 |
| Platform | GPL570 (Affymetrix HG-U133 Plus 2) |
| 설계 | tumor n=10, paired normal n=10 (환자 10명 × 2조건) |
| Series matrix 크기 | 약 4.7 MB |
| 기대 방향 — down in tumor | GKN1, GKN2, ATP4A, ATP4B, PGC, LIPF |
| 기대 방향 — up in tumor | COL1A1, COL11A1, SPP1, INHBA, MMP7, CTHRC1, THBS2 |
| Pathway 기대 | `HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION` NES > 0 (부호만 고정, 값은 미지정) |
| STRING 규약 | taxonomy `9606` (Homo sapiens), protein ID 형식 `9606.ENSP...` |

이 표에 없는 모든 수치(개별 logFC, p-value, 정확한 NES, STRING interaction score/개수, 개별 GSM accession 등)는 이번 문서에서 **추정하지 않습니다**. §7에서 이를 다시 명시합니다.

---

## 3. 재현 Manifest (다운로드 없이 제시하는 실행 레시피)

아래는 "나중에 실제로 실행할 사람"을 위한 매니페스트입니다. 이번 세션에서는 실행하지 않았습니다(`not_executed_in_this_run: true`).

```yaml
manifest:
  id: GSE79973-paired-tumor-normal-v1
  not_executed_in_this_run: true

  retrieval:
    source: "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE79973"
    accession: GSE79973
    platform: GPL570
    files_expected:
      - "GSE79973_series_matrix.txt.gz"   # 스냅샷 기준 약 4.7 MB
      - "GPL570.annot.gz (or SOFT family file)"  # probe -> gene symbol 매핑용
    integrity_check: "GEO가 제공하는 체크섬/파일 크기로 대조 (현재 미확인, 실행 시 확인)"

  environment:
    r: ">=4.3"
    bioconductor: ">=3.18"
    packages:
      - GEOquery      # series matrix 로드
      - limma         # paired DE 분석
      - fgsea         # GSEA
      - msigdbr       # Hallmark gene set (버전 실행 시 고정)
      - STRINGdb      # 또는 STRING REST API (version 실행 시 고정)
    note: "정확한 패키지/DB 버전은 이번 문서에서 확정하지 않음 — 실행 시점에 고정(pin)하고 보고서에 기록"

  steps:
    - id: 1
      action: "GEOquery::getGEO('GSE79973')로 series matrix 로드"
    - id: 2
      action: "pData(eset)에서 tumor/normal 라벨과 환자 pairing ID 파싱, 10쌍 여부 확인"
    - id: 3
      action: "QC: 발현값 분포(boxplot), PCA/MDS로 이상치·라벨 스왑 의심 샘플 탐색"
    - id: 4
      action: "log2 스케일/정규화 상태 확인 (series matrix가 이미 처리된 값인지 확인, 필요시만 재정규화)"
    - id: 5
      action: "GPL570 annotation으로 probe -> gene symbol 매핑 (다중 probe -> 1 gene은 IQR 최대 probe 등 규칙 사전 정의)"
    - id: 6
      action: "paired design 행렬 구성: model.matrix(~patient + group)"
    - id: 7
      action: "limma lmFit -> eBayes (patient를 blocking factor로 사용), 전체 유전자 DE 통계량 산출"
    - id: 8
      action: "사전 정의 패널(§2의 13개 유전자) 방향 일치 여부 확인 — 1차 QC gate"
    - id: 9
      action: "ranked list(t-statistic) 생성 -> fgsea(Hallmark gene sets)"
    - id: 10
      action: "HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION NES 부호 확인 (양수 기대)"
    - id: 11
      action: "유의 DEG 목록(임계값은 §4 참조, 실행 시 확정)을 STRING API/STRINGdb 입력으로 사용"
    - id: 12
      action: "STRING 응답에서 species=9606, stringId가 '9606.ENSP' 접두인지 검증"
    - id: 13
      action: "§8 체크리스트로 결과를 스냅샷 기대값과 대조"

  outputs:
    - DEG_table.tsv
    - GSEA_hallmark_results.tsv
    - STRING_network.tsv
    - STRING_network.png
    - QC_report.html
```

---

## 4. Paired 분석 설계 세부사항

- **데이터 유형**: Affymetrix 마이크로어레이 → RNA-seq 전용 도구(DESeq2/edgeR)가 아닌 **limma**를 표준 경로로 사용.
- **Pairing 처리**: 환자 ID를 blocking factor로 모델에 포함 (`~patient + group`), 또는 `duplicateCorrelation()` 사용.
- **통계 모델**: moderated t-test (limma `eBayes`), 다중검정 보정은 Benjamini-Hochberg(FDR).
- **제안 유의성 기준** (결과 아님, 파라미터 제안): `|log2FC| ≥ 1` & `adj.P.Val < 0.05`. 실제 분포를 본 뒤 조정 가능함을 명시.
- **1차 QC gate**: §2의 13개 유전자 패널 방향이 기대(6개 down / 7개 up)와 일치하는지 확인. 불일치가 많으면 tumor/normal 라벨 스왑 의심 → 원 메타데이터 재확인.
- **배치효과**: GEO characteristics_ch1/scan date 등 메타데이터에 배치 정보가 있는지 확인하는 단계를 포함하되, 존재 여부는 이번 문서에서 단정하지 않음.
- **샘플 식별자**: 개별 GSM accession은 스냅샷에 없으므로 이번 문서에서 나열하지 않음 — 실행 시 GEO 메타데이터에서 직접 확보.

---

## 5. Pathway / GSEA 설계

- **Ranking metric**: limma moderated t-statistic (대안: signed `-log10(p) * sign(logFC)`).
- **Gene set collection**: MSigDB Hallmark (`h.all`), 핵심 검증 대상은 `HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION`.
- **기대 검증 포인트**: NES(EMT) 부호가 양수인지만 확인 (스냅샷 고정값). 정확한 NES/FDR은 실행 후 산출 — 이번 문서는 값을 제시하지 않음.
- **교차검증용 보조 gene set** (정성적 제안, 기대 수치 없음): `REACTOME_EXTRACELLULAR_MATRIX_ORGANIZATION`, `HALLMARK_APICAL_JUNCTION` 등 — up/down 패널의 생물학적 일관성(ECM/섬유화 vs 상피 분화)을 다각도로 점검하는 용도.
- **재현성 요건**: MSigDB 버전, 종(Homo sapiens), fgsea permutation 수/seed를 보고서에 고정 기록.

---

## 6. STRING 네트워크 검증 설계

- **Input**: §4에서 산출된 유의 DEG 목록 (up-set/down-set 분리 권장).
- **Species 고정**: `9606` (스냅샷 규약).
- **ID 형식 검증**: STRING 응답의 `stringId`가 `9606.ENSP...` 형식인지 확인 — 다르면 매핑 오류로 판단.
- **Confidence threshold 제안**: medium(0.4)으로 넓게 스크리닝 후 high(0.7)로 핵심 모듈 재확인 (제안값이며 실제 edge 수/score는 실행 시 산출, 이번 문서는 미제시).
- **정성적 가설(검증 필요, 수치 아님)**: collagen/ECM 계열(COL1A1, COL11A1, THBS2, SPP1 등)이 하나의 조밀한 클러스터를 형성하고, 위점막 분화 마커(ATP4A/ATP4B/PGC/LIPF/GKN1/GKN2)는 별도의 "정상 위 기능" 모듈로 분리될 가능성 — STRING clustering(MCL) + functional enrichment(FDR)로 사후 검증 필요.
- **버전 고정**: STRING DB 버전(예: v12.x)은 실행 시점에 확정하여 보고서에 기록.

---

## 7. 짧은 문헌 요약 (일반 지식, study-specific 수치 인용 없음)

> 주의: 아래는 유전자 기능에 대한 **일반적·교과서적 배경지식**이며, GSE79973 데이터에서 직접 도출된 정량적 결과가 아닙니다. 이번 실행에서는 PubMed 등 실제 문헌 검색을 수행하지 않았으므로 저자/연도/PMID 등 구체적 인용은 포함하지 않습니다. 인용이 필요하면 별도 문헌 검색 단계(§9)를 거쳐야 합니다.

- **GKN1 / GKN2 (Gastrokine 1/2)**: 정상 위점막 표면점액세포에서 발현되는 분비단백질로, 위 상피 항상성·종양억제 기능과 연관된다고 알려져 있으며 위암에서 발현 소실이 특징적으로 보고되어 온 유전자군.
- **ATP4A / ATP4B**: 벽세포(parietal cell)의 H+/K+-ATPase 알파/베타 소단위. 위산 분비 기능의 핵심 마커이며, 벽세포 소실(위축성 위염, 장상피화생, 종양화)의 표지자로 흔히 사용됨.
- **PGC (pepsinogen C), LIPF (gastric lipase)**: 주세포(chief cell) 분화 마커로, 정상 위선 구조 소실 시 함께 감소하는 경향이 보고됨.
- **COL1A1 / COL11A1 / THBS2 / SPP1 / INHBA / MMP7 / CTHRC1**: 세포외기질(ECM) 구성·리모델링, 종양 관련 섬유아세포(CAF) 활성, 침윤/EMT 관련 신호와 연관되어 여러 고형암(위암 포함) 연구에서 반복적으로 상향 유전자로 보고되어 온 유전자군.
- **해석 틀**: 이 패널의 방향성(정상 위 분화 마커 하향 + ECM/간질 마커 상향)은 위암에서 흔히 기술되는 "정상 상피 분화 프로그램 소실 + 간질 리모델링/EMT 활성화"라는 일반적 서사와 부합하는 구조이며, `HALLMARK_EMT` NES 양수 기대와도 정성적으로 일치함.

---

## 8. 이번 실행에서 제공하지 않는(추정 금지) 수치 목록

- 개별 유전자 log2FC, p-value, adj.P.Val (13개 패널 포함)
- `HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION`의 정확한 NES 값, p-value, FDR
- STRING 네트워크의 edge 수, combined score, functional enrichment FDR
- 개별 샘플 GSM accession 및 환자 매핑
- 문헌의 구체적 저자/연도/PMID/정량적 인용

---

## 9. 실행 후 검사 가능한 체크리스트 (스냅샷 대조용)

- [ ] tumor 10 / paired normal 10, 총 20 샘플·10쌍 확인
- [ ] GKN1, GKN2, ATP4A, ATP4B, PGC, LIPF → log2FC(tumor vs normal) < 0
- [ ] COL1A1, COL11A1, SPP1, INHBA, MMP7, CTHRC1, THBS2 → log2FC(tumor vs normal) > 0
- [ ] `HALLMARK_EPITHELIAL_MESENCHYMAL_TRANSITION` NES > 0
- [ ] STRING 응답의 species == 9606, stringId가 `9606.ENSP`로 시작
- [ ] 위 5개 중 하나라도 불일치 시: 라벨링/매핑/정규화 단계 재검토 (추정치 발표 금지, 원인 규명 우선)

---

## 10. 한계 및 다음 단계

- 이번 문서는 **설계·매니페스트**이며 실제 계산 결과가 아닙니다. 실행하려면 (a) GEO에서 series matrix 다운로드, (b) R/Bioconductor 환경 고정, (c) §3 manifest 순서대로 실행, (d) §9 체크리스트로 자기검증이 필요합니다.
- 문헌 인용을 포함하려면 PubMed/Semantic Scholar 등 실제 검색 단계를 추가하고, 인용마다 원문 대조를 거쳐야 합니다.
- STRING/MSigDB는 버전에 따라 결과가 달라질 수 있으므로, 실행 시 사용한 버전을 보고서에 반드시 고정 기록해야 재현성이 보장됩니다.

---

파일로 저장하시려면 `GSE79973_paired_analysis_plan.md` 같은 이름으로 쓰기 권한을 허용해주시면 저장하겠습니다.
