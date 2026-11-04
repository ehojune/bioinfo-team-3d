## 결론

PR #573에 최신 main(`d56253a`)을 병합했습니다. 충돌은 `labhq/gateway/server.py` 한 곳이었고 두 쪽 의도를 모두 남겼습니다.

## 바뀐 것

- 종료 처리: CP4 기한 타이머 취소와 편집자 저장소 close를 함께 실행합니다.
- 검토 대기 초안은 편집자 게시·GitHub 전송·검색 색인을 시작하지 않습니다. 최종 결정이 색인 알림과 편집자 게시를 예약합니다. bundle을 다시 만드는 요청은 갱신된 bundle 기록이 게시를 예약합니다.
- 검색 색인은 `review_pending` 초안을 건너뜁니다. 시작 시 과거 요청 재처리에서도 같습니다.
- 매뉴얼 CP4 절에 수용 뒤 시작하는 항목을 적었습니다.
- 앞선 봇 지적(P1 GitHub 전송 보류, P2 workdir 없는 요청의 bundle 재생성)의 수정 `bbcf48d`가 로컬에만 있어 PR head에 없었습니다. 그대로 보존해 이 병합과 함께 push합니다.

## 확인

- 회귀: 수용 뒤 색인 알림·편집자 게시를 확인하는 통합 test를 추가했습니다(`test_cp4_accept_wakes_recall_and_publishes_editor_after_review`).
- Windows 전체 pytest 6222개 통과. 처음 한 번은 `PYTHONUTF8=1` 때문에 `tasklist` 한국어 출력 해석이 실패해 1개가 떨어졌고, 그 변수를 빼고 다시 통과했습니다.
- Node test 45개 파일을 하나씩 통과했습니다.

## 남은 지적

- 코드 리뷰(Codex)는 한도 소진으로 돌리지 못했습니다.
- PI/trial 실행·재시작, 리뷰 요청, 병합은 하지 않았습니다.

Refs #90

🤖 Generated with Claude Code for the labhq dev lead
