## 단계
#877 rev10(A안 1단계): 웹 작업판에 요청 시간 상한과 `model_tier_mismatch` 경고. 설계: [#877 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/877#issuecomment-6526354927), 승인: A안.

## 한 일
1. `Hub.snapshot()`의 요청 허용 목록과 `request.created` data에 `wall_clock`.
2. `state.js`: `applyWallClock`(비열거 `q.wallClock`, 상태 digest 불변), `deadlineLabel(q, now)`(`labhq status`와 같은 문구), `engineCostLabel`이 `model_tier_mismatch` 경고를 읽음.
3. `index.html` 요청 패널 `req-meta`에 활성 요청일 때만 시간 상한 줄. 15초 재그림이 이미 있어 별도 타이머 없음.

## 후속 수정 (리뷰)
- 남은 시간 문구(`deadlineText`)를 요청 render cache key에 넣어 15초 타이머가 카운트다운을 다시 그리게 했다(P2). 이 key 식을 잘라 평가하는 `tests/web_local_jobs_675.cjs`의 context에 `deadlineText`를 더해 `ReferenceError`를 고쳤고(회귀), 문구가 바뀌면 key가 바뀜을 단언한다.
- manual 한·영 `작업 시간` 절에 웹 작업판의 시간 상한 줄, snapshot 뒤 PI 대기의 오차, 선언 등급과 다른 모델 비용 줄을 적었다(P1).

## 알려진 오차 (A안)
snapshot 뒤에 시작한 PI 대기는 다음 snapshot까지 빼지 않아 남은 시간이 실제보다 PI 대기만큼 짧게 보일 수 있다(일찍 경고하는 쪽).

## 테스트
- `tests/web_state.cjs`(꺼짐·켜짐·모름·waiting_pi·지난 기한·snapshot 뒤 대기·request.created·digest 불변), `tests/web_cost_by_agent.cjs`(경고 문구, ':'가 든 모델명, 옛 summary, index.html 연결), `tests/test_active_request_cancel.py`(snapshot과 request.created가 wall_clock을 싣는지) 추가.
- 로컬: 위 `.cjs`와 `web_issue87`·`web_issue106`을 node로 직접 돌려 통과, `test_active_request_cancel.py` 28 passed. pytest 안의 node 시험 6건(`web_state`·`web_issue87`·`web_issue106`·`replay_boundary` 3건)은 main에서도 `node.exe not found`(pytest 임시 홈)로 실패하는 로컬 환경 문제라 CI에서 확인한다.

## 하지 않은 것
- 3D의 요청별 줄. 서버가 `held_s` 변경을 이벤트로 알리는 B안. 실제 브라우저 렌더 확인.
