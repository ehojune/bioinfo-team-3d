## 결론

PR #730에서 internal 구역을 가리키는 별칭 Read를 Claude 승인 gate로 보냅니다. Fixes #725.

## 바뀐 것

- 작업·project·참고 폴더의 internal 별칭에 Read/Grep ask 규칙을 붙였습니다.
- internal 구역이 있으면 bare Read를 폴더별 규칙으로 좁혀 스캔 누락과 실행 중 생성 별칭도 실제 경로로 판정합니다.
- internal 구역이 없을 때의 사전 허용 동작은 유지했습니다.

## 확인

- 새 회귀는 구현 전 2 failed, 구현 뒤 2 passed입니다.
- 관련 pytest: 491 passed, 27 skipped.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 158,402자 → 158,520자(+118자).

## 남은 것

- push 뒤 CI 결과 확인. 리뷰와 병합은 총괄이 이어갑니다.
