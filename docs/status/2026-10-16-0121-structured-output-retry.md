## 결론

Fixes #667. StructuredOutput JSON 파싱 오류를 모든 직원 task의 runner 공통 경로에서 같은 세션으로 재시도합니다.

## 바뀐 것

- `runner.structured_output_retries`: 기본 2회. 오류 원문의 고정 문구로 판정하며 resume 비용·사용량을 합산합니다.
- 한도 초과는 ‘형식 오류’ 카드 한 번으로 알리고, 자동 재계획으로 한도를 초기화하지 않습니다.
- 공통 직원·비서실장 prompt에 원문 근거와 원인 추측 금지 규칙을 넣었습니다. 통역에도 도구 오류 근거를 전달합니다.
- 계획 prompt의 Windows 경로 값만 `/`로 바꿉니다. 원본 요청과 정규식은 보존합니다.
- `research.result_corrections`와 별도입니다. output types off 계약과 prompt hash 고정값은 그대로입니다.

## 확인

- origin/main `326c841`: 새 회귀 11 failed / 1 passed → branch 12 passed
- 관련 pytest 합계 487 passed / 7 skipped(중복 실행 제외). 전체 suite는 CI에 맡깁니다.
- `scripts/check_public.sh` 통과. patch-notes·notes-index 검사와 push 후 CI 상태는 PR에 기록합니다.

## 남은 것

실제 직원 CLI 실험은 하지 않았습니다. Claude 이벤트 변환부터 runner·CSO 카드까지 모의 오류로 검증했습니다. 리뷰 요청과 병합은 하지 않습니다.
