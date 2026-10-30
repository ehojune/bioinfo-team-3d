## #1079 — disposable fixture 계획 검사

- JSON 계획의 누락·타입·충돌·만료를 offline에서 검사한다. 실제 보호는 `UNVERIFIED`다.
- `python -m pytest tests/test_boundary_fixture_plan.py -q`: 5 passed.
- 실제 VM, 권한, network와 teardown은 관측하지 않았다. 독립 검토가 필요하다.
