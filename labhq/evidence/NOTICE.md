# OKF Loremaster 출처와 라이선스

`literal_check.py`는 OKF Loremaster의 원문 대조 코드를 수정해 옮겼다.

| 항목 | 값 |
|---|---|
| 원본 | [PFreda-Lab/okf-loremaster](https://github.com/PFreda-Lab/okf-loremaster) |
| 커밋 | `ead3819` |
| 원본 파일 | `src/okf_loremaster/verification.py`, `src/okf_loremaster/schemas/limits.py`, `tests/test_verification.py` |
| 옮긴 항목 | `Quantity`, `quantities_in`, `_is_sign`, `is_supported`, `normalize`, `Source.holds_quote`·`holds`·`sentence_for`, `_windowed`, `sentences`, `MAX_LOCATED_QUOTE_WORDS` |
| 라이선스 | Apache-2.0 ([전문](LICENSE-APACHE-2.0.txt)) |

바꾼 것: OKF schema 의존을 없애 인용문·수치만 입력받게 했고, Python 3.10에서 동작하도록 작성했다. `normalize`는 ASCII만 남기던 원본과 달리 한글을 포함한 Unicode 문자·숫자를 보존한다. 원본 test 중 이 범위의 사례만 옮기고 두 변경점의 회귀 test를 더했다.

원본에는 저작권 줄과 `NOTICE` 파일이 없다. 이 문서는 출처와 labhq의 변경을 표시하며 Apache-2.0 조건을 바꾸지 않는다.
