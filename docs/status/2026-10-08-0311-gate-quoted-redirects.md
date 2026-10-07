## 결론

v0.5 시운전에서 읽기와 작업 폴더 안 쓰기만 하던 curl·sed pipeline과 quoted heredoc script 작성이 승인 카드를 띄운 오탐을 고쳤습니다.

## 바뀐 것

- 따옴표와 quoted heredoc의 `>`는 같은 줄에 인수를 실행할 수 있는 명령이 없을 때 데이터로 처리합니다. 불확실한 명령은 raw scan을 유지합니다.
- sed의 `w`·`W`·`s///w`·`-i` 쓰기를 따로 찾고, `e`·`s///e`·`-f`·변수 script는 raw scan으로 남깁니다.
- curl·wget의 출력 옵션을 쓰기 대상으로 잡고, config 옵션은 raw scan을 유지합니다.
- 실제 시운전 두 건과 shell 실행·sed·curl·wget 반대 사례를 회귀 test로 고정했습니다.

## 실행한 것

- 관련 policy·gate test: 241 passed.
- Windows 전체 pytest: 4691 passed, 57 skipped. 첫 실행의 비관련 WinError 5 한 건은 단독 통과 뒤 전체 재실행에서 재발하지 않았습니다.
- `scripts/check_public.sh`, `git diff --check` 통과.

## 미해결

없음.

Closes #479

🤖 Generated with Codex (gpt-5) for the labhq dev lead
