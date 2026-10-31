# #1200 rev8·rev9 — AlphaGenome 준비 상태를 runner가 보고

- 결론: CSO가 계획에 쓰는 직원 명단의 AlphaGenome 도구 표시를 gateway가 자기 키 파일·패키지로 판단하던 것을, runner가 등록할 때 보고한 `alphagenome_ready`로 바꿨다(#1246 결함 2). 키가 gateway와 다른 계정이나 PC의 runner에 있어도 명단에서 도구가 빠지지 않는다.
- 재현(main): runner가 준비됐다고 보고해도 gateway에 키가 없으면 `Orchestrator._alphagenome_ready`가 거짓이라 명단에서 `alphagenome`·`alphagenome_variant`가 빠졌다. 시험을 먼저 써서 main에서 7건 실패를 확인했다.
- 바꾼 것: `labhq/runner/daemon.py`(`capabilities()`에 `alphagenome_ready` — 이 runner의 키 파일과 클라이언트가 있는지, 값은 보내지 않고 사실만, 프로브 실패는 `False`), `labhq/orchestrator/cso.py`(`_alphagenome_ready`가 runner 보고를 읽음: 하나라도 준비됐다고 하면 참, 준비 안 됨만 보고되면 거짓, 보고가 아예 없는 이전 runner만 있으면 예전처럼 gateway 자체 확인), `tests/test_annot_literature.py`(+7건), `tests/test_cso.py`(capabilities 비교 시험이 이 PC의 키 파일·클라이언트 유무에 따라 달라지지 않게 `annot_keys.read_key`와 `alphagenome_client_available`을 고정).
- 시험: runner의 네 조합 보고와 키 값 비누출, 키 없는 gateway가 runner 보고로 명단에 올림, 준비 안 됨 보고가 gateway의 키보다 우선, 하나만 준비돼도 충분, 이전 runner의 fallback. `test_cso.py`의 시험은 가짜 키 파일이 있는 홈에서도 통과함을 확인했다.
- 미검증: 실제로 다른 계정의 runner에서 gateway 명단이 바뀌는지(단위 시험까지). manual은 이 PR 범위에 넣지 않았다.
