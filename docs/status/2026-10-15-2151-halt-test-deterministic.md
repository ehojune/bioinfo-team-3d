## 결론

PR #648에서 Windows CI의 staff-control flake를 사건 순서 검증으로 고쳤습니다. 원인은 제품 결함이 아니라 `sleep(0.1)` 뒤 `t1`이 시작됐다고 가정한 test 시간 의존입니다. Refs #59.

## 바뀐 것

- `t1`의 fake CLI 시작 뒤 `t2`를 제출
- `t2`가 semaphore 대기에 들어간 뒤 halt 전송
- 제품 halt 경로와 단언 강도는 그대로 유지

## 확인

- 수정 test 20회 연속 통과
- 관련 pytest 5 passed
- `scripts/check_public.sh` 통과

## 남은 것

- push 뒤 CI를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.
