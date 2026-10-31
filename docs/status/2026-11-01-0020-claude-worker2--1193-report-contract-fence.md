# #1193 후속: 보고서 각주 처리가 길이가 다른 코드 펜스를 알아본다

- 결론: 결함이 있었다. `reader_markdown(footnotes=True)`의 코드 보호 정규식(`` ```.*?``` ``)이 4개 이상 백틱·물결표 펜스를 통째로 보호하지 못해, 그 안의 `[^1]`이 "근거" 번호로 바뀌었다(재현: 4백틱 펜스 안에 3백틱 펜스가 든 문서의 `open[^1]`이 `open[근거 1]`로 바뀜).
- 바뀐 것: `labhq/report_contract.py`에 `code_spans`·`mask_code`(portfolio에 있던 스캐너를 옮김)를 두고 `reader_markdown`이 그 span으로 코드를 보호한다. `labhq/portfolio.py`는 `mask_code`를 가져다 쓴다(중복 제거). `tests/test_report_contract.py`에 3건 추가.
- 실행한 것: `test_report_contract`·`test_result_storage`·`test_report_markdown`·`test_report_pdf`·`test_portfolio_edition`·`test_portfolio_branch_folders`·`test_request_bundle` 280 passed, 2 skipped(수정 전 새 시험 2건은 실패 확인).
- 미해결/미검증: 전체 suite는 CI에 맡긴다. `report_markdown.py`·`report_pdf.py`의 다른 코드 처리 정규식은 이번 범위 밖이라 보지 않았다.
