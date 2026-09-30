# Brassicaceae plastome 구조 비교 연구 설계

> **상태:** 연구 설계서 — 서열 조립·polishing·circularization 및 실제 비교 분석은 수행하지 않음  
> **분석 대상:** 고정 RefSeq plastome 5개 레코드, 4개 종

## 1. 연구 목적

다음 항목을 accession 단위로 비교한다.

1. 고정 총길이와 실제 레코드 서열 길이의 일치 여부
2. `LSC–IRb–SSC–IRa` 사분구조와 각 구획 길이
3. 네 IR 경계 `JLB`, `JSB`, `JSA`, `JLA`의 위치와 인접 유전자
4. IR 확장·수축에 따른 경계 이동량
5. Arabidopsis lyrata 두 accession 사이의 종내 차이

표본이 5개뿐이므로 유의성 검정보다는 bp 단위 차이, 범위, 비율과 구조 사건을 기술한다.

## 2. 고정 입력 표

아래 표는 변경하지 않는 입력 원장이다. 현재 데이터베이스의 다른 accession이나 길이로 자동 교체하지 않는다.

| species | accession | length_bp |
|---|---|---:|
| Arabidopsis thaliana | NC_000932.1 | 154478 |
| Arabidopsis lyrata | NC_034379.1 | 154923 |
| Arabidopsis lyrata | NC_034365.1 | 154678 |
| Capsella rubella | NC_027693.1 | 154601 |
| Brassica rapa | NC_040849.1 | 153483 |

출처는 이 실행에서 고정된 [NCBI RefSeq plastome snapshot](https://www.ncbi.nlm.nih.gov/nuccore/NC_000932.1)이다.

### 고정 표에서 직접 검산되는 값

| 항목 | 값 |
|---|---:|
| 레코드 수 | 5 |
| 종 수 | 4 |
| 최소 길이 | 153483 bp |
| 최대 길이 | 154923 bp |
| 범위 | 1440 bp |
| 평균 | 154432.6 bp |
| 중앙값 | 154601 bp |
| 범위/평균 | 0.9324% |
| A. lyrata 두 레코드 차이 | 245 bp |

고정 표는 총길이만 제공하므로, 여기서 구획 길이나 IR 경계를 역산해 관측값처럼 사용하지 않는다.

## 3. 분석 단위와 레코드 관리

기본키는 종명이 아니라 `accession.version`이다.

- 다섯 행 모두 accession 수준 비교에 유지한다.
- Arabidopsis lyrata 두 accession을 합치거나 평균 서열로 만들지 않는다.
- 두 레코드의 서열 SHA-256, BioSample·voucher 정보와 완전성을 비교하여 `동일 서열`, `서로 다른 plastome`, `판정 불가`로 구분한다.
- 종 수준 요약에서는 A. lyrata에 두 배의 가중치를 주지 않는다. 두 레코드의 범위와 차이를 먼저 보고하고, 대표가 필요하면 결측 염기 수·완전성·provenance만으로 선택한 뒤 다른 accession으로 교체하는 민감도 분석을 한다.
- 현재 레코드의 길이나 분류가 고정 표와 달라도 원표는 수정하지 않고 별도 검증 열에 불일치 사유를 남긴다.

계획된 provenance 필드는 다음과 같다.

`source_accession`, `resolved_accession_version`, `retrieval_date`, `record_status`, `taxon_match`, `complete_plastome`, `observed_length_bp`, `length_matches_fixed`, `ambiguous_bases`, `sequence_sha256`, `decision`, `reason`

## 4. 사분구조 판정 방법

### 4.1 좌표 규칙

- 원본 서열과 annotation은 불변으로 보관한다.
- 분석용 사본만 회전·역상보완하며 원본 좌표 변환표를 남긴다.
- 1-based, 양끝 포함 좌표를 사용한다.
- 분석용 좌표 1은 JLA 직후 첫 LSC 염기로 정규화한다.
- 방향은 다음 순서로 고정한다.

`LSC → JLB → IRb → JSB → SSC → JSA → IRa → JLA → LSC`

| 경계 | 정의 |
|---|---|
| JLB | LSC / IRb |
| JSB | IRb / SSC |
| JSA | SSC / IRa |
| JLA | IRa / LSC |

SSC의 반대 방향은 plastome의 flip-flop isomer일 수 있으므로 곧바로 구조적 역위로 판정하지 않는다.

### 4.2 IR 검출

1. 각 원형 서열을 자기 자신 및 역상보완 서열과 정렬한다.
2. 초기 후보는 길이 `≥20000 bp`, 염기 동일도 `≥99.0%`인 비중첩 역방향 정렬쌍으로 찾는다.
3. 가장 긴 후보쌍의 끝점을 염기 수준으로 확장한다.
4. 두 IR 사이의 긴 단일사본 구간을 LSC, 짧은 구간을 SSC로 지정한다.
5. `IRa`와 `reverse-complement(IRb)`를 직접 전장 정렬하여 길이, coverage, identity를 계산한다.
6. 경계 유전자와 rRNA operon의 양쪽 IR 중복을 독립적인 보조 증거로 확인한다.

### 4.3 필수 산술 검산

총길이를 `N`이라 할 때 다음을 accession마다 계산한다.

```text
ε = N − (LLSC + LSSC + LIRa + LIRb)
ΔIR = |LIRa − LIRb|
IR identity (%) = matches / aligned_columns × 100
```

필수 조건은 `ε = 0 bp`이다. 이상적인 완성 합의서열에서는 `ΔIR = 0 bp`이고 두 IR이 100% 역상보완 관계일 것으로 기대하지만, 편차가 있으면 생물학적 변이·모호한 경계·레코드 오류를 구분하여 기록한다.

원점을 넘지 않는 구간 길이는 `end − start + 1`, 원점을 넘는 구간은 `(N − start + 1) + end`로 계산한다.

## 5. 예상 길이와 판정 기준

Arabidopsis thaliana의 공개된 구조는 `84170 + 17780 + 2×26264 = 154478 bp`로, 고정 총길이와 정확히 일치한다. 이는 양성 대조로 사용한다. [A. thaliana 완전 plastome 연구](https://academic.oup.com/dnaresearch/article/6/5/283/353928)

공개된 Capsella rubella plastome도 `83822 + 17855 + 2×26462 = 154601 bp`로 보고되어 같은 산술 검산을 만족한다. 다만 이 문헌값은 NC_027693.1의 분석 결과로 복사하지 않고, 해당 accession의 좌표에서 다시 산출한다. [C. rubella plastome 연구](https://pubmed.ncbi.nlm.nih.gov/26024136/)

| 검사항목 | 사전 기대 범위 | 용도 |
|---|---:|---|
| 전체 길이 | 각 고정 행의 `length_bp`와 정확히 일치 | 필수 |
| LSC | 약 82900–84700 bp | 점검용 |
| SSC | 약 17400–18100 bp | 점검용 |
| IRa, IRb 각각 | 약 25900–26600 bp | 점검용 |
| `ε` | 0 bp | 필수 |
| `ΔIR` | 기대값 0 bp | 편차 시 재검토 |
| IR 전장 coverage | ≥99.5% | 강한 구조 지지 |
| IR identity | ≥99.9%; 이상적 기대값 100% | 편차 시 재검토 |
| ambiguous base | 기대값 0 | 존재 위치 기록 |
| 경계 이동 `|ΔJ|` | 대체로 0–수백 bp | >1000 bp이면 우선 재검토 |

구획 범위는 합격·탈락 기준이 아니다. 외부 Brassicaceae 비교에서도 IR 길이 약 26035–26459 bp가 보고되어 위 범위와 부합하지만, 실제 계통 특이적 IR 확장·수축은 보존해야 할 결과일 수 있다. [Brassicaceae IR 경계 비교 연구](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0263310)

## 6. IR 경계 검증

각 경계의 양쪽 6 kb, 총 12 kb 창을 추출한다. 유전자 annotation과 독립적으로 nucleotide alignment를 확인하고 다음 값을 bp 단위로 기록한다.

`gene`, `strand`, `intact/partial/pseudogene`, `left_region_bp`, `right_region_bp`, `distance_to_junction_bp`, `gene_overlap_bp`

| 경계 | 예상 유전자 배열 | 검사 가능한 문헌 대조값 |
|---|---|---|
| JLB | LSC의 `rpl22`, 경계를 지나는 `rps19`, IRb의 `rpl2` | `rps19` 내부 경계가 전형적 |
| JSB | IRb 말단의 부분 `ψycf1`과 SSC의 `ndhF` | `ψycf1`이 SSC 쪽으로 1–4 bp; `ycf1/ndhF` 중첩 35–38 bp 사례 |
| JSA | 기능성 `ycf1`이 SSC/IRa 경계를 통과 | `ycf1` 중 약 1022–1034 bp가 IRa에 위치한 사례 |
| JLA | IRa의 `rpl2`, LSC의 `trnH-GUG`; 부분 `ψrps19` 가능 | `trnH-GUG`가 경계에서 LSC 쪽 2–30 bp인 사례 |

이 수치는 Brassicaceae 비교를 위한 대조 범위이지 다섯 accession의 정답값이 아니다. 범위를 벗어나도 자동 오류로 처리하지 않는다.

### 경계별 검증 절차

1. IR 자기정렬에서 얻은 반복 끝점과 네 junction을 일치시킨다.
2. 각 junction 창을 NC_000932.1의 상동 창과 정렬한다.
3. 원형 좌표 대신 인접 유전자 끝점에서 junction까지의 부호 있는 거리 `d`를 계산한다.
4. `ΔJ = d_target − d_NC_000932.1`로 경계 이동량을 보고한다. 양수는 IR이 상동 서열을 더 포함하는 방향으로 정의한다.
5. `rps19`와 `ycf1`의 완전 사본과 IR 경계에서 생긴 부분 사본을 따로 센다.
6. feature가 없더라도 상동 서열을 직접 검색하여 annotation 누락과 실제 결실을 구분한다.
7. `rps12`처럼 trans-splicing 또는 IR 중복의 영향을 받는 유전자는 단순 copy 수로 구조 변이를 판정하지 않는다.

원시 read가 추후 제공된다면 네 junction 모두에 대해 양쪽 구간을 각각 1 kb 이상 포함하는 독립 장독해 3개 이상을 보조 기준으로 사용할 수 있다. 이는 별도 assembly 검증 단계이며 이번 설계에서는 실행하지 않는다.

## 7. 비교 분석

NC_000932.1은 좌표·방향 기준일 뿐 조상 상태나 오류 없는 절대 기준으로 간주하지 않는다.

accession마다 다음 한 행을 산출하도록 설계한다.

```text
species, accession, fixed_length, observed_length, length_match,
LSC_bp, SSC_bp, IRa_bp, IRb_bp, epsilon_bp, delta_IR_bp,
IR_coverage_pct, IR_identity_pct, ambiguous_bases,
JLB, JSB, JSA, JLA, boundary_shift_bp, status, notes
```

비교 항목은 다음과 같다.

- 총길이 및 구획별 절대 차이와 백분율
- 네 경계의 signed shift
- IR에 들어가거나 나온 유전자·유전자 조각
- 고유 유전자 수와 IR 중복을 포함한 물리적 copy 수
- 동일 annotation 규칙에서의 유전자 순서와 방향
- A. lyrata 두 accession의 245 bp 차이가 어느 구획과 indel에 분포하는지
- 염기 치환 비교에서는 IR의 이중 가중을 피하기 위해 `LSC + SSC + IR 한 사본`을 별도로 분석

## 8. 판정 규칙

| 상태 | 기준 |
|---|---|
| `STRUCTURE_PASS` | 고정 길이 일치, `ε=0`, 두 장거리 IR과 네 junction 확인, IR identity 강한 지지 |
| `BOUNDARY_REVIEW` | 구획 경보 범위 이탈, `|ΔJ|>1000 bp`, 비전형 경계 유전자 또는 IR 불일치 |
| `ANNOTATION_DISCORDANT` | 서열에는 상동 유전자·부분 사본이 있으나 annotation만 다름 |
| `NOT_ANALYZABLE` | accession 미해결, partial record, 경계 부근 모호 염기 또는 IR 판정 불가 |

`NOT_ANALYZABLE` 레코드도 고정 입력 표에서는 제거하지 않는다. 대체 accession을 임의로 투입하지 않고 분석 제외 사유만 기록한다.

## 9. 예상 결과와 한계

고정 총길이가 153483–154923 bp로 좁고 알려진 Brassicaceae plastome 크기와 부합하므로, 다섯 레코드 모두 전형적인 사분구조와 보존된 `rps19–ycf1–ndhF–trnH-GUG` 경계를 보일 것으로 예상한다. 다만 이는 사전 가설이며 총길이만으로 IR 존재나 정확한 경계를 증명할 수 없다.

또한 다음 한계가 있다.

- 5개 레코드, 4개 종이므로 계통적 독립 표본에 대한 통계적 검정력이 없다.
- RefSeq 서열만으로 원래 assembly의 접합 정확성이나 heteroplasmy를 독립 검증할 수 없다.
- SSC 방향 차이는 물리적 isomer일 수 있다.
- pseudogene과 유전자 copy 수는 annotation 정책의 영향을 받는다.
- 외부 문헌의 구획값은 예상 범위와 양성 대조에만 사용하고 실제 accession 결과를 대신하지 않는다.

## 10. 재현성 체크리스트

- [ ] 고정 입력 표의 행·종명·accession·길이가 변경되지 않았는가
- [ ] 실제 서열 길이가 각 고정 길이와 정확히 비교되었는가
- [ ] 원본 서열의 SHA-256과 회수일이 기록되었는가
- [ ] 원본과 좌표 정규화 사본의 변환표가 보존되었는가
- [ ] 모든 레코드에 동일한 IR·annotation 규칙이 적용되었는가
- [ ] `ε=0`을 accession별로 확인했는가
- [ ] 두 IR의 길이·coverage·identity를 모두 보고했는가
- [ ] 네 junction의 경계 좌표와 인접 유전자를 bp 단위로 기록했는가
- [ ] 부분 사본과 기능성 사본을 구분했는가
- [ ] A. lyrata 두 accession을 종 수준에서 이중 가중하지 않았는가
- [ ] 범위 이탈을 자동 오류가 아닌 검토 대상으로 처리했는가
- [ ] 실제 plastome 조립을 수행하지 않았는가