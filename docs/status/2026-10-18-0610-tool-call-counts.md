## 결론

PR #734에서 직원 세션의 도구 호출 수와 실패 수를 작업 결과·요청 묶음·정리본 실행 기록에 남깁니다. Fixes #701.

## 바뀐 것

- Claude Code·Codex 이벤트를 도구 이름별로 세고 MCP 이름은 `mcp:서버.도구`로 맞춥니다.
- 이어서 한 turn·재시도·결과 교정의 집계를 합칩니다.
- 실행 기록 표에는 단계·직원·도구 이름·호출 수·실패 수만 두고 입력값은 넣지 않습니다.

## 확인

- 새 회귀는 main 상당 코드에서 1 failed, 수정 뒤 2 passed입니다.
- 관련 pytest 174 passed·3 skipped, adapter 재실행 12 passed·1 skipped, 이어서 하기·교정 경로 43 passed입니다.
- `scripts/check_public.sh`와 `git diff --check`가 통과했습니다.
- `docs/manual.md`: 158,685자 → 158,785자(+100자).

## 남은 것

- push 뒤 CI 결과 확인이 남았습니다. 봇 리뷰 대기·지적 대응·병합은 총괄이 이어갑니다.
