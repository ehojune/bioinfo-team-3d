## 결론

PR #593에서 완료된 채용 단계의 파견직 runner를 재개 대기에서 제외했습니다.

## 바뀐 것

- `recruit.step_ids` 가운데 미완료 단계가 있는 active receipt만 기다립니다.
- 파견직 단계가 끝났으면 정규직·리뷰 단계를 재개하며, 미완료라면 기존처럼 기다립니다.

## 확인

- main 코드 회귀: 1 failed, 1 passed. 수정 뒤 같은 회귀 2 passed.
- 관련 pytest 99 passed. `scripts/check_public.sh` 통과. 전체 pytest는 CI에 맡겼습니다.

## 남은 지적

- P2 4266513167과 4266592247은 이번 범위에서 고치지 않았습니다.
- 봇 리뷰 대기·지적 대응·병합은 총괄이 이어받습니다.

Refs #591
