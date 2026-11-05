# #1216 — Git Bash에서 clean_install_check이 새 clone에서 죽는 문제

- 결론: 원인은 `git` 경로가 아니라 환경 변수였다. Git Bash가 내보내는 `MSYSTEM=MINGW64`가 점검 환경에 그대로 넘어가고 PATH는 System32로 좁혀지면, PATH 맨 앞의 `mingw64\bin\git.exe`가 시작하다 죽는다(exit 3221225477, 0xC0000005). `Git\cmd\git.exe`는 같은 환경에서도 통과해서 CI는 영향이 없다.
- 재현: Git Bash에서 `_isolated_env`로 만든 환경으로 `clone --local`을 돌렸다. `mingw64\bin\git.exe`는 3221225477, `cmd\git.exe`는 0. 변수를 하나씩 빼 보니 `MSYSTEM`만 빼면 통과했다.
- 고침: `_isolated_env`가 `MSYS*`·`MINGW*`·`EXEPATH`·`ORIGINAL_*` 변수를 뺀다. 점검은 어차피 셸과 격리하는 것이라 git 경로를 고르는 것보다 원인을 없애는 쪽이다. 어느 셸에서든 같다.
- 시험(`tests/test_clean_install_check.py`, 2건): Git Bash 변수가 환경에 없다(모든 OS) · Windows에서 `MSYSTEM`이 설정된 채 PATH의 모든 `git.exe`(`cmd`와 `mingw64\bin`)가 격리 환경으로 실제 clone을 한다. 옛 코드에서는 둘 다 실패하고(실제 clone은 3221225477) 지금 통과한다.
- 실행: `test_clean_install_check.py`·`test_clean_install_diagnostics.py` 85건 통과.
- 미검증: 전체 `clean_install_check.py` 실행(설치·mock 요청까지). 새 clone 단계만 재현·확인했다.
