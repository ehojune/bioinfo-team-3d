파일 쓰기 권한도 승인되지 않았습니다. 대신 보고서 전체를 아래에 Markdown으로 직접 제시합니다.

---

# Brassicaceae Plastome 비교 설계 보고서

**작성일**: 2026-10-01
**성격**: 비교 분석 *설계 문서* — 실제 서열 다운로드·조립·주석(annotation)은 수행하지 않았다. "확인값"만 문헌 근거가 있고, 나머지는 검증 대상 "예상값"이다.

## 1. 목적과 범위

고정된 RefSeq 5건(Brassicaceae 4종, *Arabidopsis lyrata*는 2개 accession)을 입력으로,
1. accession·길이·종 이름을 원본 그대로 보존하고,
2. 엽록체 게놈의 quadripartite 구조(LSC–IRb–SSC–IRa)를 분해하는 절차를 설계하고,
3. IR 경계(4개 junction) 검증 방법과 예상 범위를 제시한다.

## 2. 고정 입력 데이터 (변경 없이 보존)

출처: https://www.ncbi.nlm.nih.gov/nuccore/NC_000932.1 (사용자 제공, 이 실행에서 고정)

| species | accession | length_bp |
|---|---|---|
| Arabidopsis thaliana | NC_000932.1 | 154478 |
| Arabidopsis lyrata | NC_034379.1 | 154923 |
| Arabidopsis lyrata | NC_034365.1 | 154678 |
| Capsella rubella | NC_027693.1 | 154601 |
| Brassica rapa | NC_040849.1 | 153483 |

### 2.1 데이터 품질 플래그

- **동일 종 중복 accession**: *A. lyrata*가 NC_034379.1(154,923 bp)과 NC_034365.1(154,678 bp) 두 건, 245 bp 차이. 서로 다른 아종/생태형 또는 독립 조립본일 가능성. 표는 그대로 두되, 비교 설계에서는 이 쌍을 "종내(intraspecies) 변이 대조군"으로 활용(§6).
- **주석 파이프라인 이질성**: NC_000932.1은 1999년 최초 발표(Sato et al. 1999) 이후 재주석된 오래된 항목이고, 나머지는 상대적으로 최근 RefSeq 제출본으로 추정(accession 번호대 기반 추정, 실시간 재확인은 아님). IR 경계에 걸치는 유전자(rps19, ycf1) 표기가 파이프라인마다 달라질 수 있어 비교 전 동일 파이프라인 재주석(GeSeq/PGA) 권장.
- **외부 실시간 조회 미수행**: 이번 실행에서 NCBI efetch 조회 및 로컬 파일 저장을 각 1회 시도했으나 도구 권한이 승인되지 않아 중단했다. 따라서 표의 길이값 외 IR/LSC/SSC 세부 좌표는 실측되지 않았다(§7).

## 3. Quadripartite 구조 개관

```
        LSC (Large Single Copy, ~53–56%)
   ┌───────────────────────────┐
  JLA                         JLB
  IRa ═══════════════════════ IRb   (IRa, IRb: 역상보 동일, 각 ~17%)
  JSA                         JSB
   └───────────────────────────┘
        SSC (Small Single Copy, ~11–12%)
```

4개 접합부(junction) 표기는 IRscope 관례(Amiryousefi et al. 2018)를 따른다: JLB(LSC→IRb), JSB(IRb→SSC), JSA(SSC→IRa), JLA(IRa→LSC). Brassicaceae 내에서 이 구조는 매우 보존적이며, 종간 차이는 대개 IR 경계(특히 JSB/JSA, ycf1/ndhF 부근)의 수백 bp 수준 확장·축소로 나타난다.

## 4. 비교 설계

### 4.1 구조 분해(LSC/SSC/IR 길이 산출) 절차

1. NCBI efetch로 각 accession의 GenBank flat file(`rettype=gbwithparts`)과 FASTA 확보.
2. `repeat_region` 주석이 있으면 좌표 직접 파싱(Biopython `SeqIO`).
3. 없으면 전체 서열을 자기 자신과 BLASTN(`-strand both`)/`nucmer`로 정렬해 ~26 kb 역상보 완전일치 구간 2개를 탐지 → IRa/IRb.
4. `Total = LSC + SSC + 2×IR` 항등식으로 내적 정합성 확인.

### 4.2 IR 경계(4-junction) 검증 방법

| Junction | 정의 | 보존적 앵커 유전자 (LSC/IR 측 ↔ IR/SSC 측) | 비고 |
|---|---|---|---|
| JLB | LSC → IRb | rpl22 (LSC) ↔ rps19 (IRb) | rps19은 경계에 걸치거나 바로 안쪽 |
| JSB | IRb → SSC | ycf1 가유전자(ψ, IRb) ↔ ndhF (SSC) | 대부분 피자식물에서 **가장 가변적** |
| JSA | SSC → IRa | ndhF (SSC) ↔ ycf1 (IRa, 전체 길이) | JSB의 거울상 |
| JLA | IRa → LSC | rps19 (IRa) ↔ trnH-GUG, psbA (LSC) | JLB의 거울상 |

검증 절차: (1) 각 junction 좌우 500 bp 창 추출 → (2) 창 내 유전자 주석이 위 앵커와 일치하는지 5개 accession 전체 대조 → (3) IRscope 스타일로 4-junction 좌표를 나란히 정렬해 NC_000932.1 대비 상대 이동량(±bp) 계산 → (4) ycf1 정합성 체크: IRb 내 가유전자 길이 ≈ 전체 ycf1 CDS 길이 − SSC 쪽 부분 길이.

### 4.3 내부 정합성 체크리스트 (검사 가능한 수치)

- [ ] `Total = LSC + SSC + 2×IR`, 오차 0 bp
- [ ] IRa·IRb 역상보 동일성 ≥ 99%
- [ ] 4개 junction 모두 앵커 유전자 쌍 확인
- [ ] IR 영역 GC 함량이 LSC/SSC보다 근소하게 높음(rRNA 오페론 포함)
- [ ] *A. lyrata* 두 accession 간 구조 차이 < *A. thaliana*–*Capsella rubella* 간 차이(종내 < 종간 가정 검증)

### 4.4 실행 예시 명령 (미실행)

```bash
# 1) 서열/주석 다운로드 (예시, 미실행)
for acc in NC_000932.1 NC_034379.1 NC_034365.1 NC_027693.1 NC_040849.1; do
  curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=${acc}&rettype=gbwithparts&retmode=text" -o "${acc}.gb"
done

# 2) IR 자기정렬 탐지 (예시, 미실행)
makeblastdb -in NC_000932.1.fasta -dbtype nucl
blastn -query NC_000932.1.fasta -db NC_000932.1.fasta -outfmt 6 -evalue 1e-50 \
  | awk '$1==$2 && $9>$10'

# 3) 특징 파싱 (예시, 미실행)
python3 - <<'PY'
from Bio import SeqIO
rec = SeqIO.read("NC_000932.1.gb", "genbank")
anchors = {"rps19","rpl22","ycf1","ndhF","trnH-GUG","psbA","rpl2"}
for f in rec.features:
    if f.type == "gene" and f.qualifiers.get("gene", [""])[0] in anchors:
        print(f.qualifiers["gene"][0], f.location)
PY
```

## 5. 예상 범위 (계산 근거 포함)

### 5.1 확인값: *Arabidopsis thaliana* (NC_000932.1)

Sato et al. 1999(DNA Research 6(5):283–290) 보고 구조. 표의 총 길이(154,478 bp)와 정확히 일치:

| 구간 | 길이(bp) | 비율 |
|---|---|---|
| LSC | 84,170 | 54.49% |
| SSC | 17,780 | 11.51% |
| IR (×1) | 26,264 | 17.00% |
| IR (×2) | 52,528 | 34.00% |
| **합계** | **154,478** | **100.00%** |

검산: `84,170 + 17,780 + 2×26,264 = 154,478` ✓

### 5.2 추정값: 나머지 4건 (비율 기반 투영, **미검증**)

방법: A. thaliana 확인 비율(54.49% / 11.51% / 17.00%×2)을 각 accession의 고정 총 길이에 적용한 점추정 후, 계통 거리에 따라 허용폭을 다르게 부여했다. **§4.1–4.2 절차로 실측 확인 필요.**

| species | accession | length_bp(고정) | LSC 예상(bp) | SSC 예상(bp) | IR 예상(bp, ×1) | 허용폭 사유 |
|---|---|---|---|---|---|---|
| Arabidopsis thaliana | NC_000932.1 | 154478 | **84,170**(확인) | **17,780**(확인) | **26,264**(확인) | Sato et al. 1999 |
| Arabidopsis lyrata | NC_034379.1 | 154923 | 83,700–85,300 | 17,600–18,100 | 26,000–26,700 | 근연속, 좁은 허용폭(±~1%) |
| Arabidopsis lyrata | NC_034365.1 | 154678 | 83,600–85,100 | 17,600–18,100 | 25,950–26,650 | 상동; 두 accession 차이=종내 변이 |
| Capsella rubella | NC_027693.1 | 154601 | 83,600–85,000 | 17,600–18,000 | 25,950–26,650 | Camelineae족 내 근연 |
| Brassica rapa | NC_040849.1 | 153483 | 82,800–84,500 | 17,400–17,950 | 25,700–26,500 | 다른 tribe(Brassiceae), 허용폭 확대(±~1.5–2%) |

재현 방법: `점추정 = 총 길이(bp) × 해당 비율`.

### 5.3 일반 배경 참고치 (accession별 확인 대상 아님)

Brassicaceae 포함 대부분 피자식물에서 널리 보고되는 일반 구성: 고유 유전자 약 110–130개(IR 중복 1회 계수), 단백질코딩 약 79–87개·tRNA 약 30개·rRNA 4개. IR 영역 GC 함량이 LSC/SSC보다 근소하게 높음(rRNA 오페론 포함).

## 6. 종간 vs 종내 변이 해석 틀

- **종내 대조**: NC_034379.1 ↔ NC_034365.1(같은 *A. lyrata*, 245 bp 차이) — 4-junction 좌표 차이를 배경 잡음 기준선으로 사용.
- **근연 종간**: *A. thaliana* ↔ *A. lyrata* ↔ *Capsella rubella*(모두 Camelineae족) — 작은 이동(수십~수백 bp) 예상.
- **원연 종간**: *Brassica rapa*(Brassiceae족) vs 나머지 — 가장 큰 차이(특히 JSB/JSA) 예상, 검증 우선순위 최상위.

검사 가능한 핵심 가설: 실측 junction 이동량이 "종내 차이 < 근연종간 차이 < 원연종간 차이" 순서를 따르는가.

## 7. 한계 및 후속 작업

- 이번 실행은 서열 다운로드·조립·정렬·재주석을 전혀 수행하지 않았다. §5.2 수치는 A. thaliana 실측 비율의 산술적 투영이며 실측값이 아니다.
- WebFetch(NCBI efetch 조회)와 로컬 파일 저장(Write) 시도가 각각 권한 미승인으로 중단되어, GenBank feature 수준의 실좌표는 확보하지 못했다.
- 후속 우선순위: (1) 5개 accession GenBank flat file 확보 → (2) §4.1–4.2로 LSC/SSC/IR 실측 및 4-junction 좌표 산출 → (3) §5.2 예상 범위와 실측값 대조해 가설 기각/채택 → (4) *Brassica rapa*의 JSB/JSA 경계 우선 정밀 검증.
- 참고문헌 서지정보는 학습된 지식 기반이며 이번 실행에서 실시간 재확인을 하지 않았으므로, 정식 인용 전 원문 대조 권장.

## 8. 참고 문헌

1. Sato S, Nakamura Y, Kaneko T, Asamizu E, Tabata S. Complete structure of the chloroplast genome of *Arabidopsis thaliana*. *DNA Research* 1999;6(5):283–290.
2. Amiryousefi A, Hyvönen J, Poczai P. IRscope: an online program to visualize the junction sites of chloroplast genomes. *Bioinformatics* 2018;34(17):3030–3031.
3. Tillich M, et al. GeSeq – versatile and accurate annotation of organelle genomes. *Nucleic Acids Research* 2017;45(W1):W6–W11.
4. Greiner S, Lehwark P, Bock R. OrganellarGenomeDRAW (OGDRAW) v1.3.1. *Nucleic Acids Research* 2019;47(W1):W59–W64.
5. Qu XJ, Moore MJ, Li DZ, Yi TS. PGA: batch annotation of plastomes. *Plant Methods* 2019;15:50.
6. NCBI Nucleotide, https://www.ncbi.nlm.nih.gov/nuccore/NC_000932.1 (사용자 제공 출처)

---

파일 저장(Write)과 외부 조회(WebFetch) 권한이 모두 승인되지 않아, 위 보고서를 채팅 응답으로 직접 제공했습니다. 로컬 파일로 저장하려면 권한을 허용해 주시면 동일 내용을 `brassicaceae_plastome_comparison_design.md`로 기록하겠습니다.
