## 단계
#1168: helper를 거치는 경로에서 `await` 뒤 낡은 상태로 쓰는 지점. 근거: [#1168 점검 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1168#issuecomment-6538696689).

## 한 일 (시험을 먼저 쓰고 수정 전 13건 실패를 확인한 뒤 고쳤다)
- `Hub._set_status`: 되살리는 값(활성 상태)은 요청이 `cancelled`이거나 `cancelling`이면 쓰지 않고 돌아온다. `interrupted`·`failed`는 그대로 통과한다. 다른 터미널 상태(`done`·`failed`)는 막지 않는다. 모델 거절은 `failed` 요청을 같은 id로 `waiting_for_runner`로 되돌려 재시도하기 때문이다(시험으로 고정).
- `Hub.resume_when_ready`: 루프 맨 앞과 `await aresume_agents` 뒤, 두 곳에서 `_ended_by_cancel`을 본다. `track_request`로 감싸이지 않은 호출(복구된 카드의 만료 타이머)은 취소가 task를 멈추지 못한다.
- `Orchestrator.run_request`의 `except Exception`: 취소됐거나 취소 중이면 `failed` 기록과 `request.failed`를 만들지 않는다. 취소 쪽 터미널 기록과 충돌한다.
- 시험은 기존 `tests/test_request_lifecycle.py`에 넣었다(인벤토리 claim 밖): `_set_status`가 cancelled/cancelling을 되살리지 않음, 막지 말아야 할 것(failed 재개, failed·interrupted 쓰기, 활성끼리 이동), 직접 호출한 resume이 기다리는 사이의 취소, 취소 뒤 단계 오류.

## 확인
- `test_request_lifecycle.py` 65 passed. 취소·재개·모델 거절·quota·재시작·facilities 계열은 아래 결과(PR 본문).

## 하지 않은 것
- `daemon.py`(runner) 쪽의 같은 부류와 helper 밖의 직접 쓰기는 보지 않았다.
- 복구된 facilities 타이머가 취소 때 이미 멈추는지는 확인하지 않았다. 이번 수정은 그 경우에도 안전하다.

## 후속 (같은 PR)
- `_ended_by_cancel`을 메서드로 두었더니 `test_resume_receipt_clock`(가짜 hub에 `Hub.resume_when_ready`만 붙임) 3건이 `AttributeError`로 죽었다(main에서는 통과). 모듈 함수로 옮겼다.
- 취소·재개·모델 거절·quota·재시작·facilities·interrupt 계열 1549 passed, 18 skipped(0 failed). `test_request_lifecycle.py` 65 passed 포함.
