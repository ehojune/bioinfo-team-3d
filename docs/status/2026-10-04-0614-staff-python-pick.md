# runner 요약: 직원이 쓸 python 명령까지 고른다 (#380 후속)

**결론:** #380은 PATH의 `python3`를 먼저 골랐는데, 이 PC에서 `python3`는 분석 패키지가 없는 3.14(WindowsApps 별칭)이고 `python`이 pandas가 있는 3.12였습니다. 이제 세 명령을 모두 검사해 분석 패키지가 가장 많은 것을 고르고(동률이면 python3·python·py 순), CSO 능력 줄에 `Python=3.12.10 run as \`python\``처럼 명령까지 알립니다.

**검증:** 이 PC 실측 `python` 3.12.10 선택. 새 test 2개(패키지 많은 쪽 선택·후보 없음 fallback, 능력 줄에 알려진 명령만 표시), 관련 test 219 passed.
