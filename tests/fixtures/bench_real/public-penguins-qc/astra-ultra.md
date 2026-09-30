**Palmer Penguins 발췌본 QC 보고서**

분석 대상은 사용자가 제공한 고정 발췌본 5행이다. 표기된 출처는 [Palmer Penguins 저장소](https://github.com/allisonhorst/palmerpenguins)이며, 아래 수치는 제공된 행만으로 계산했다. 헤더는 행 수에서 제외하고, 빈칸은 결측으로 처리했다.

| 검사 항목 | 결과 | 확인 근거 |
|---|---:|---|
| 전체 행 수 | 5행 | 데이터 행 1–5 |
| 고유 species 수 | 2종 | Adelie, Gentoo |
| species별 행 수 | Adelie 3행, Gentoo 2행 | 합계 3 + 2 = 5행 |
| `body_mass_g` 결측 | 1행 / 5행 = **20%** | 5번째 데이터 행: Gentoo, Biscoe, male |
| `body_mass_g` 비결측 | 4행 | 3750, 3800, 3250, 4500 g |
| `flipper_length_mm` 결측 | 0행 / 5행 = 0% | 관측값 181, 186, 195, 211, 230 mm |
| `flipper_length_mm` 최솟값–최댓값 | **181–230 mm** | 5개 관측값 기준 |
| `flipper_length_mm` 범위 폭 | **49 mm** | 230 − 181 = 49 mm |

`body_mass_g` 결측값은 대체하지 않았다. 해당 행에는 `flipper_length_mm = 230`이 있으므로 지느러미 길이 범위 계산에 포함했다.

**해석 한계:** 이 자료는 5행의 발췌본이며, 추출 방식과 대표성이 확인되지 않았다. 따라서 종별 구성, 체중 결측률 20%, 지느러미 길이 범위 181–230 mm는 이 발췌본에만 해당한다. **원본 데이터 전체를 대표하지 않으며**, 원본 전체의 품질이나 분포로 일반화할 수 없다.