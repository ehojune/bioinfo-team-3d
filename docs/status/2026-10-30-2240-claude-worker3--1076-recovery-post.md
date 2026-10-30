## 단계
#1076 rev5: `test_recovery_fake_check[gateway]`의 Windows `ReadTimeout` 3회. 원인 조사: [#1076 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1076#issuecomment-6521284783).

## 한 일
1. `scripts/recovery_fake_check.py`: `timed_post()`가 두 POST(`/api/requests`, `/api/approvals/<id>`)에 읽기 상한 `POST_TIMEOUT_S = 10`을 주고, 시간 초과(`httpx.TimeoutException`)면 `TimeoutError: POST <호출> timed out after <초>s (<오류 종류>, limit 10s)`로 바꿔 receipt의 `failure`에 남긴다. 클라이언트의 2초 상한은 `/api/health` 반복에 그대로 두고 전체 90초 deadline도 그대로다.
2. `tests/test_recovery_fake_check.py`: POST가 더 긴 상한으로 나가는지, 4종 시간 초과가 호출·경과·오류 종류를 담은 `TimeoutError`가 되는지(승인 id는 `<id>`로 가림), 시간 초과가 아닌 오류는 건드리지 않는지 시험 6건.

## 확인한 것·못한 것
- 이 PC에서 같은 시험을 3번 돌려 두 POST는 10~70 ms였고 재시작 직후 첫 health가 약 0.5초 걸렸다. CI Windows pool(worker 4개)에서 2초를 넘는 원인은 재현하지 못했다.
- 상한을 올리는 것은 한 번의 우연을 가리는 효과가 있지만, 10초에도 걸리면 `failure`가 어느 호출인지 알려 다음 조사가 가능하다.

## 테스트 결과
Windows `test_recovery_fake_check`·`test_repeat_fake_check` 39 passed.
