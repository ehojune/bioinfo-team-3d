## 단계
#1117 rev5: 후보 ④. 근거: [#1117 조사 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1117#issuecomment-6530661340).

## 판단
`test_backpressured_runner_delivery_has_a_bound_even_after_cancel`의 의도는 "취소 뒤에도 backpressure 대기가 영원하지 않다"이다. 시험은 전송 상한(`CLIENT_SEND_TIMEOUT_S`)을 0.05초로 줄이고 소켓을 `release` 전까지 막으므로, 마감 0.5초는 상한의 10배일 뿐 의미 있는 경계가 아니었다. 끝나지 않는 구현은 마감이 몇 초든 실패한다. 그래서 의도를 지킨 채 마감만 올릴 수 있다.

## 한 일
- 종료 마감 `wait({task}, timeout=0.5)`를 5초로, 진입 대기 `wait_for(entered.wait(), 1)`을 10초로. 단언 문구·`release` 구조는 그대로.

## 확인
- `-k backpressured` 2 passed(resolve, close).
- 변이 확인: 전송 상한을 100초로 바꾸면 두 건 모두 원래 메시지("held cancellation ... indefinitely")로 실패한다(약 11초).
- Windows inventory: `known_tests` 목록에만 있고 sha256 고정 없음.

## 하지 않은 것
- 느린 Windows 러너 재현은 CI에서 확인한다. 구현이 0.05초보다 오래 막히는 문제가 있는지는 이 변경으로 알 수 없다(5초 안에 끝나는 것만 확인).

## 리뷰 후 수정 (rev6)
- P1: 5초는 설정 상한(0.05초)의 100배라 1~4.9초 붙잡는 회귀를 가렸다. 상한을 0.25초로 두고 마감은 그 4배(1초)로 좁혔다. 마감이 같은 비율이라 러너 지터의 절대 여유(0.75초)는 이전 0.45초보다 크다.
- 소켓이 `release`가 아니라 timer 취소로 끝났는지(`send_timed_out`)를 단언한다.
- 변이 확인: 상한을 2초로 바꾸면 두 건 모두 실패한다. `-k backpressured` 2 passed.
- 한계: 마감은 여전히 실시간이라 매우 느린 러너에서는 1초를 넘길 수 있다. 가짜 clock은 쓰지 않았다.
