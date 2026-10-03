## 2026-10-03 · 게이트 — 플래그 뒤 경로와 tee, Get-FileHash 옆 따옴표 행

- 결론: 8차 모의 시운전 오탐(Get-FileHash 옆 manifest 행의 `<GSM>/suppl/`을 리다이렉트로 읽음)을 고쳤다. 같이 찾은 놓친 쓰기도 막았다. `Set-Content -Encoding utf8 C:/밖/x`처럼 플래그가 경로보다 앞이거나, bash `tee`, `sc`·`Export-Csv`·`Tee-Object`로 작업 폴더 밖에 쓰는 경우다.
- 바뀐 것: `labhq/policy.py` `_named_write_targets`·`_ps_writer_targets`. 모르는 플래그(접두 약어 포함)의 다음 단어도 목적지로 보고(과보고 쪽), `-Value`·`-Encoding` 같은 텍스트 인자만 뺀다. Get-FileHash·Import-Csv·Export-Csv를 데이터 명령에 넣었다.
- 실행한 것: 전체 test 3526 passed. 새 test 15건 중 13건은 수정 전 실패(나머지 2건은 과보고 방지 확인).
- 미해결: 변수·splatting·별칭 전부는 여전히 읽지 않는다(문서화된 한계, 경로 가드는 sandbox가 아님).
- 근거: `tests/test_shell_write_quotes.py`.
