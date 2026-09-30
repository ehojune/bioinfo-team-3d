이 샌드박스에서는 파일 쓰기와 셸 파이프라인 실행이 승인 대기로 차단되어 있어, 스크립트 실행 대신 제공된 4행 표를 직접 대조하여 QC를 수행했습니다(외부 다운로드 없음, 입력 전체가 보고서에 포함되어 재검증 가능).

# 공개 단백질 Manifest QC 보고서

## 1. 개요
- **대상**: 사용자 제공 UniProt manifest 발췌 (아래 원본 표 그대로 사용)
- **출처**: https://www.uniprot.org/ (사용자가 이번 실행용으로 고정 제공)
- **외부 네트워크 조회/다운로드**: 수행하지 않음
- **검사 항목**: 행 수, unique accession 수, 중복 accession, `declared_length_aa` 양수 여부

## 2. 원본 데이터 (입력 그대로, 근거)
| # | accession | protein | declared_length_aa | source |
|---|---|---|---|---|
| 1 | P01116 | KRAS_HUMAN | 189 | UniProt |
| 2 | P04637 | P53_HUMAN | 393 | UniProt |
| 3 | P38398 | BRCA1_HUMAN | 1863 | UniProt |
| 4 | P01116 | KRAS_HUMAN | 189 | UniProt |

## 3. 핵심 QC 지표

| 지표 | 값 |
|---|---|
| 총 행 수 (헤더 제외) | **4** |
| Unique accession 수 | **3** |
| 중복 accession 종류 수 | **1** (P01116) |
| 중복으로 인한 초과 행 수 | **1** (4 − 3) |
| `declared_length_aa` > 0 인 행 | **4 / 4** (100%) |
| `declared_length_aa` ≤ 0 또는 비숫자인 행 | **0** |

## 4. Accession 빈도 (근거)
| accession | 등장 횟수 | 행 번호 |
|---|---|---|
| P01116 | 2 | 1, 4 |
| P04637 | 1 | 2 |
| P38398 | 1 | 3 |

Unique 집합 = {P01116, P04637, P38398} → 3개. 총 4행 − unique 3 = 초과 1행.

## 5. 중복 상세
- **P01116 (KRAS_HUMAN)**: 1행·4행에 등장. `protein`, `declared_length_aa`, `source` 값이 완전히 동일 → 값 상충 없는 완전 중복행.

## 6. 길이 양수 검사
| 행 | accession | declared_length_aa | 정수 | > 0 |
|---|---|---|---|---|
| 1 | P01116 | 189 | ✔ | ✔ |
| 2 | P04637 | 393 | ✔ | ✔ |
| 3 | P38398 | 1863 | ✔ | ✔ |
| 4 | P01116 | 189 | ✔ | ✔ |

결측치·0·음수·비숫자 값 없음.

## 7. 결론 및 권고
- 발췌본은 4행/unique 3 accession, 중복 1건(P01116, 값 상충 없음)으로 구성됨.
- 길이 필드는 전수 양수 정수로 이상치 없음.
- 다운스트림 처리 전 accession 기준 중복 제거 시 3행으로 축소 권장.
- 본 QC는 제공된 `declared_length_aa`만 검사한 것이며, UniProt 실제 서열 길이와의 일치 여부는 외부 조회를 하지 않았으므로 확인되지 않음.

## 8. 검사 방법 (재현 가능)
- 행 수 = 헤더 제외 데이터 행 개수
- Unique accession 수 = accession 열 값의 집합(set) 크기
- 중복 = accession 열에서 2회 이상 등장한 값
- 양수 여부 = `declared_length_aa`가 정수이고 0보다 큰지 확인
- 위 표(§2)가 입력 전체이므로 동일 절차로 누구나 재계산해 대조 가능.
