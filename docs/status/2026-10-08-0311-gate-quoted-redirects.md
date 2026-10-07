## 결론

PR #481의 봇 리뷰 P1을 `caba362`에서 고쳤습니다. sed·curl·wget option을 확정하지 못하면 따옴표를 가리지 않고 원문을 검사합니다.

## 바뀐 것

- `-e'…'`·`-e"…"`·`--expression='…'`처럼 붙은 shell word 조각을 합쳐 sed script로 판별합니다.
- 알려진 sed option과 묶음을 명시해 해석하고, 변수·미지원 option·script file은 fail closed로 처리합니다.
- curl·wget도 확인한 option만 데이터 인수로 다루며, config와 미지원 option은 원문 검사로 돌립니다.

## 실행한 것

- 관련 policy test: 252 passed.
- Windows 전체 pytest: 4702 passed, 57 skipped.
- `scripts/check_public.sh`, `git diff --check` 통과.

## 미해결

push 뒤 CI 확인 전입니다.

Closes #479

🤖 Generated with Codex (gpt-5) for the labhq dev lead
