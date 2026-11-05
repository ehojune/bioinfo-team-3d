## 단계
#1278(#910 8차): 게시용 코드의 비밀값 가리기가 `token = r["x"].split(",")[0]`를 `token = <redacted-secret>,")[0]`로 바꿔 `python_syntax`로 결과 저장 전체가 실패한 문제.

## 한 일
- 원인은 `labhq/portfolio.py`의 `clean_publication_text`·`scan_publish_files`가 쓰는 `_ASSIGNMENT`였다(`result_storage.py`가 아님).
- 코드 파일(.py·.r·.sh)은 값이 리터럴일 때만 가린다(`_redact_code_literals`). 따옴표 문자열은 따옴표·접두를 남기고, 6자리 이상 숫자와 셸 단어는 `"<redacted-secret>"`로 바꾼다. 표현식·호출·조회는 그대로다.
- `scan_publish_files`는 코드 파일에 같은 규칙을 쓰고, 문서는 기존 규칙에 따옴표로 감싼 가림 표시만 인정한다. `SECRET_PATTERNS`는 그대로다.
- 시험 15건(`tests/test_result_storage_redact_1278.py`): 수정 전 13건 실패.

## 확인
- Windows: 새 시험 15건과 `test_portfolio_edition.py`·`test_result_storage.py`·`test_portfolio_branch_folders.py` 합쳐 105 passed.

## 하지 않은 것
- 문서(.md 등)의 가리기 규칙은 바꾸지 않았다. manual은 claim 범위 밖이다.
- 병합 뒤 `req_7e1eeb8237` 결과 저장 재시도는 병합 후 한다.
