## 2026-10-03 · #298 모의 시운전 — 따옴표·주석·heredoc 안의 `>`를 쓰기 대상으로 보지 않음

- 결론: 게이트의 셸 쓰기 검사가 문자열·주석·heredoc 본문 속 `>`를 리다이렉트로 읽어 이유 없이 승인 카드를 내던 오탐을 줄였다. 진짜 리다이렉트는 그대로 묻는다. 2차 모의 시운전의 두 건(PowerShell `.Replace('…<s1 venv>…')`, `python - <<'EOF'` 본문 속 `<s1 venv>`)이 더는 뜨지 않는다.
- 바뀐 것: `_shell_write_targets`가 리다이렉트·명령 이름을 찾기 전에 문자열·주석·heredoc 본문·PowerShell here-string·이스케이프를 같은 길이로 가린다. 가리는 것은 줄의 모든 명령이 인용문을 텍스트로만 읽을 때뿐이다(echo·cat·printf·grep·cd·ls, 하위 명령이 데이터뿐인 git, PowerShell Write-Output·Get/Set-Content·문자열 메서드, 인라인 python·Rscript·node 코드에 프로세스 호출이 없을 때). 그 밖의 명령, 셸이 다르게 읽을 여지(짝 없는 따옴표, 큰따옴표 안의 `$( )`, 구분자에 `$`가 있는 heredoc, `\r`, 괄호 안 `<<`)는 예전처럼 원문을 훑는다. 원문 훑기도 `>`마다 대상을 읽는다.
- 실행한 것: `tests/test_shell_write_quotes.py` 104건 통과(회귀 40건은 수정 전 모두 실패). Git Bash·Windows PowerShell에서 실제로 실행해 표식 파일이 생긴 명령 128개를 대조했고 수정 뒤 가려지는 것은 0개다(수정 전 bash 20, PowerShell 12). `scripts/check_public.sh` 통과.
- 미해결: 이름을 쪼갠 `b''ash`, 명령 위치가 아닌 곳의 따옴표 이름(`env "bash" -c`), alias, python·node가 `os.system`·`subprocess`로 셸을 부르는 경우는 못 잡는다. 셸 문법상 리다이렉트가 아닌 `>`(`[[ a > b ]]`, PowerShell `(1 > 2)`)는 오탐으로 남는다. `"$(cat <<'EOF' … EOF)"` 커밋 메시지 형태는 원문 훑기로 돌아가 카드가 뜬다.
- 근거: `labhq/policy.py`, `tests/test_shell_write_quotes.py`.
