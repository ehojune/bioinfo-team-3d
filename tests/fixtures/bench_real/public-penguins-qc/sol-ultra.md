# Palmer Penguins 발췌본 QC 보고서

## 검사 범위

[Palmer Penguins 공개 데이터](https://github.com/allisonhorst/palmerpenguins)에서 제공된 5개 행의 발췌본만 검사했다. 헤더는 행 수에서 제외했으며, 빈 `body_mass_g` 셀은 결측값으로 판정했다.

## QC 결과

| 검사항목 | 결과 | 계산 근거 |
|---|---:|---|
| 데이터 행 수 | **5행** | 헤더 제외 |
| 고유 species 수 | **2종** | `{Adelie, Gentoo}` |
| Adelie 행 수 | **3행 (60%)** | `3 / 5 × 100` |
| Gentoo 행 수 | **2행 (40%)** | `2 / 5 × 100` |
| `body_mass_g` 결측 | **1건 (20%)** | 빈 셀 1개, `1 / 5 × 100` |
| `body_mass_g` 유효값 | **4건 (80%)** | `3750, 3800, 3250, 4500` |
| `flipper_length_mm` 최솟값 | **181 mm** | 관측값 중 최소 |
| `flipper_length_mm` 최댓값 | **230 mm** | 관측값 중 최대 |
| `flipper_length_mm` 관측 범위 | **181–230 mm** | 유효값 5개 기준 |
| `flipper_length_mm` 범위 폭 | **49 mm** | `230 − 181` |

`body_mass_g` 결측은 `Gentoo / Biscoe / bill_length_mm 50.0 / flipper_length_mm 230 / male` 행에서 확인된다. `flipper_length_mm` 값은 `181, 186, 195, 211, 230`으로, 이 변수에는 발췌본 내 결측이 없다.

## 한계

이 결과는 **제공된 5행 발췌본에만 유효**하다. 표본 추출 방식이 제시되지 않았고 species도 Adelie와 Gentoo만 포함되어 있으므로, 원본 전체를 대표하는 표본으로 볼 수 없다. 따라서 위의 species 비율, `body_mass_g` 결측률 및 `flipper_length_mm` 범위를 원본 전체 데이터의 통계로 일반화해서는 안 된다.