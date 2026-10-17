## 결론

PR #730의 마지막 P1을 반영해, 열린 read root와 겹치는 internal·restricted 구역의 Read 사전 허용을 없앴습니다. Fixes #725.

## 바뀐 것

- 통제 구역을 품거나 그 안에 있는 작업·project·참고 root에는 `Read(//root/**)`를 만들지 않습니다.
- 그 root의 Read는 realpath 승인 gate가 판정해, 실행 중 생긴 같은-root 별칭도 internal이면 ask로 보냅니다.
- 통제 구역과 겹치지 않는 root의 scoped Read 사전 허용은 유지합니다.

## 확인

- 새 회귀는 직전 head에서 1 failed·1 passed, 수정 뒤 internal·restricted·대조군 3 passed입니다.
- 관련 pytest는 808 passed·39 skipped 뒤 환경 오류 8건만 절대 `PYTHONPATH`로 다시 돌려 adapter 12 passed·1 skipped입니다.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 158,520자 → 158,685자(+165자).

## 남은 것

- push 뒤 CI 결과 확인과 P1 인라인 답글이 남았습니다. 리뷰 재요청과 병합은 총괄이 이어갑니다.
