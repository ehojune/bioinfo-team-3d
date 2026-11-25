# #1504 · CP2 전 감사 설정

Closes #1504

- 인스턴스 설정의 `research.audit_before_cp2`와 `research.audit_repair_before_cp2`는 모두 기본값이 `false`입니다. Gateway는 연구 요청의 첫 저장에 두 값을 넣습니다.
- 실행과 재개는 저장값을 씁니다. 이전 환경변수로 이미 저장한 값도 유지됩니다. 저장 키가 없는 이전 요청은 해당 옵션을 OFF로 처리합니다.
- 아래 회귀시험은 246개 통과했습니다. 동기·비동기 요청에서 OFF → ON → OFF로 바꿔 첫 저장과 이전 요청의 저장값을 확인했습니다. CP2에서는 현재 설정과 환경변수를 저장값과 반대로 둔 경우도 확인했습니다.

```sh
python -m pytest -q tests/test_research_cp1.py tests/test_research_cp2.py tests/test_research_cp4.py tests/test_research_report.py
```

- 제품 코드 순증 −11줄. `cso.py`·`server.py`·`daemon.py` 합계는 21,872줄(이번 변경 +1, 기준 22,125줄 대비 −253)입니다.
- 실제 M7 실행과 배포는 #1490 r15 범위입니다.
