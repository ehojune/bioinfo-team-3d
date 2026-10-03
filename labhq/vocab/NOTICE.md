# EDAM 출처와 라이선스

`edam_map.yaml`(후보 라벨)과 `edam_subset.yaml`(ID·라벨·조상 ID)에는 EDAM 온톨로지의 일부가 들어 있다.
`output_types.yaml`(labhq 자체 key·정의)에는 EDAM 내용이 없다.

| 항목 | 값 |
|---|---|
| 원본 | EDAM — The ontology of data analysis and management, EDAM contributors (https://github.com/edamontology/edamontology/graphs/contributors) |
| 릴리스 | `1.25.20260626T1230Z` (파일 안 `owl:versionInfo` `1.25-20260626T1230Z`) |
| 파일 | https://github.com/edamontology/edamontology/releases/download/1.25.20260626T1230Z/EDAM.owl |
| SHA-256 | `278c5e606004c872311cd9d9d385eb578f01c1a3a38cb9ebeaa5e125e818ecc8` (GitHub 릴리스 자산 digest와 받은 파일이 같음) |
| 라이선스 | CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0 (파일 머리 `dcterms:license`) |
| 인용 | https://doi.org/10.7490/f1000research.1118900.1 (파일 머리 `citation`) |

바꾼 것: 38개 key가 가리키는 용어(31개)만 골랐다. ID, 첫 라벨, 조상 ID만 옮겼고 정의·동의어·주석은 옮기지 않았다.
`scripts/edam_subset.py`가 원본 파일에서 기계적으로 뽑는다. 원본 파일은 저장소에 넣지 않는다.

`edam_map.yaml`과 `edam_subset.yaml`은 같은 CC BY-SA 4.0 조건으로 제공한다. 저장소의 문서·데이터도 CC BY-SA 4.0,
코드는 GPL-3.0이다(PI 결정 #252, docs/manual.md '라이선스').
