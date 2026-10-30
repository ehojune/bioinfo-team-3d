## 단계
#1117 rev4: 후보 ②·③. 근거: [#1117 조사 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1117#issuecomment-6530661340).

## 한 일
- `tests/test_gateway_publish_durable.py`: `asyncio.wait_for(x, 1~2)` 18곳을 10초로. 모두 완료·이벤트 도착을 기다리는 곳이고 마감이 곧 단언인 곳은 없다. 실패하면 `TimeoutError`로 보인다.
- `tests/test_durable_writer.py`: `max(reads) < 0.1`을 `< 0.3`으로. 정지한 commit은 합계 1.2초라 읽기가 commit을 기다렸다면 그 이상이 되어 여전히 실패한다(관측 0.1049로 실패한 적 있음).

## 확인
- 두 파일 54 passed.
- Windows inventory: 두 파일 모두 `known_tests` 목록에만 있고 sha256 고정이 없어 수정하지 않았다.

## 하지 않은 것
- 느린 Windows 러너에서의 재현은 CI에서 확인한다.
- 후보 ④(`test_approval_durable_606`)는 마감이 단언이라 이번 범위가 아니다.
