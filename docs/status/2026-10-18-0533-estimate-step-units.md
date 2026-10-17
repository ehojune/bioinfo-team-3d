## 결론

PR #731에서 비용 실측을 계획 `step_id` 단위로 합치고, `DE` 약어와 이름 붙은 부정 분석을 요청 범위 검사에 반영했습니다. Fixes #728.

## 바뀐 것

- 재시도·wake·결과 교정의 여러 `cost_item`을 같은 계획 단계 비용으로 합칩니다.
- 독립된 `DE`는 DEG로 감지하고 다른 단어 속 `de`는 제외합니다.
- `경로 분석은 하지 말고`, `without pathway analysis` 같은 제외 문구는 요청 분석에서 뺍니다.
- 실행 상한의 `max(remaining, planned)` 보호는 유지합니다.

## 확인

- 새 회귀는 main 상태에서 4건 실패했고 수정 뒤 4건 통과했습니다.
- 관련 pytest는 375 passed·1 skipped입니다. 상대 `PYTHONPATH` 때문에 실패한 CP1 2건은 절대 경로로 다시 실행해 2 passed입니다.
- gateway의 `step_id` 비용 기록 test 1건도 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 넘게 무출력 정체되어 중단했습니다.
- `docs/manual.md`: 158,685자 → 158,777자(+92자).

## 남은 것

- push 뒤 CI 결과 확인. 리뷰 요청과 병합은 총괄이 이어갑니다.
