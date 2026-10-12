## 결론

CP1 승인 시 집행 상한을 승인까지 쓴 비용 + 계획 예산으로 정한다. 요청 예산(없으면 정책 상한)을 넘으면 그 상한으로 자른다. 이어 가기 차수도 같은 규칙이다.

## 바뀐 것

- 승인 budget에 `spent_before_plan_usd`를 기록하고 승인 직전에 다시 집계한다.
- CP1 카드·직원 prompt에 계획 예산과 계획까지 쓴 비용을 함께 표시한다.
- `docs/manual.md` 예산 절과 연구 protocol 설명을 맞췄다. manual 과거 사례 한 문장을 덜었다(112,104 → 112,023자).

## 실행한 것

- 새 회귀 5건: main의 cso 코드에서 모두 실패, 변경 코드에서 관련 pytest 39 passed.
- 전체 pytest: 5,447 passed·66 skipped·10 failed. 실패는 CLI stream·MCP probe·semantics subprocess 로그 검사이며 예산 관련 실패는 없다. 실패 파일 재실행에서도 일부 재현됐다.
- 공개 검사·diff 검사 통과. 병합은 GitHub CI 결과로 판정한다.

## 미해결

- 로컬 전체 suite의 위 10건은 이번 예산 변경 범위 밖이다.

Refs #435

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
