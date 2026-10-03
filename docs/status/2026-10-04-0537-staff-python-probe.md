# runner 소프트웨어 요약: 직원이 쓰는 python을 검사 (#376 후속)

**결론:** #376의 runner 요약이 `sys.executable`(labhq 자체 venv, pandas 없음)을 검사해서, T3 재실행에서 CSO가 "numpy/pandas도 없다"며 설치를 물었습니다. 직원 셸은 PATH의 Python 3.12(pandas 있음)를 씁니다. 이제 PATH의 `python3` → `python` → `py`를 검사하고, PATH에 없을 때만 labhq interpreter로 돌아갑니다.

**검증:** 새 test 2개(PATH 우선·없으면 labhq interpreter, runner가 staff_python을 씀), 관련 test 217 passed.
