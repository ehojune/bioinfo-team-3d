## 결론

Fixes #667. PR #668. StructuredOutput JSON 파싱 오류를 같은 세션에서 재시도하되, 유효한 최종 결과와 예산 한도를 먼저 확인합니다.

## 바뀐 것

- `runner.structured_output_retries`: 기본 2회. 오류 원문의 고정 문구로 판정하며 resume 비용·사용량을 합산합니다.
- 같은 turn의 최종 `structured`가 유효하면 잔존 `parse_error`가 있어도 다시 호출하지 않습니다.
- 각 resume 전 task 한도와 요청 잔여 한도 중 작은 금액에서 이전 호출 비용을 뺍니다.
- parse 오류는 retry wrapper가 직접 보존하고, 일반 tool error를 결과에 담는 기존 `general_result_contract` 경계는 유지합니다.
- 한도 초과는 ‘형식 오류’ 카드 한 번으로 알리고, 자동 재계획으로 한도를 초기화하지 않습니다.
- 공통 직원·비서실장 prompt에 원문 근거와 원인 추측 금지 규칙을 넣었습니다. 통역에도 도구 오류 근거를 전달합니다.
- 사용자 텍스트·명령·정규식은 계획 prompt에서 고쳐 쓰지 않습니다. 재시도 지시문에서 모델이 자기 JSON의 경로와 escape를 고치게 합니다.
- `research.result_corrections`와 별도입니다. output types off 계약과 prompt hash 고정값은 그대로입니다.

## 확인

- P1 회귀 2건: 수정 전 2 failed → 수정 후 전체 15 passed
- 관련 `structured_output`/general evidence/CSO/login pytest 273 passed. 전체 suite는 CI에 맡깁니다.
- `scripts/check_public.sh` 통과. patch-notes·notes-index 검사와 push 후 CI 상태는 PR에 기록합니다.

## 남은 것

실제 직원 CLI 실험은 하지 않았습니다. Claude 이벤트 변환부터 runner·CSO 카드까지 모의 오류로 검증했습니다. 리뷰 요청과 병합은 하지 않습니다.
