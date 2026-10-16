## 결론

PR #696에서 큰 연구 계획의 StructuredOutput 오류를 진단·복구하고, 계획 크기를 설정값으로 경고합니다. Fixes #679.

## 바뀐 것

- Claude partial stream을 합쳐 실패 원문 전체를 작업 폴더 `.tmp/structured_output_failures/`에 저장합니다.
- 재시도 prompt가 비ASCII 문자를 `\u` escape로 바꾸지 않게 하고, 잘림 의심 plan은 설명·반복·큰 표를 줄여 갈래별로 요약하게 합니다.
- `orchestrator.plan_max_chars`(기본 50,000자)를 넘는 유효한 plan은 실제 문자 수와 상한을 경고로 기록하되 자르지 않습니다.

## 확인

- 새 회귀는 main 코드에서 6 failed, branch에서 관련 pytest 21 passed입니다.
- `scripts/check_public.sh`, `git diff --check`가 통과했습니다.
- 고정 prompt hash는 바뀌지 않았습니다. `docs/manual.md`는 153,365자에서 153,459자로 94자 늘었습니다.

## 남은 것

- 실제 직원 CLI 재현은 하지 않았습니다. CI 결과는 최종 push 뒤 확인합니다.
- 리뷰 요청과 병합은 하지 않습니다. 남은 지적 없음.
