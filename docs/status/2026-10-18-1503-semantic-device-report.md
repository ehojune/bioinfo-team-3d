## 결론

PR #743을 최신 `origin/main`과 합치고 자동 리뷰 지적 8건을 고쳤습니다. Fixes #737.

## 바뀐 것

- 보고서 문자열을 공용 게시 정화 경로로 보내 Authorization credential과 모든 절대경로를 가립니다.
- 현재 요청의 최신 shadow 관측만 판정하고, worker 완료 뒤 관측을 요청 snapshot에 고정합니다.
- 연구 계약 요청에만 생성하며 실행 당시 checklist 판본을 보존합니다.
- CP2 카드에서 실제 `semantic_report.md`를 열고 내용을 펼칠 수 있습니다.
- #742의 오류 형식(`code·device·stage·at·안전한 설명`)을 쓰며 예외 문구는 싣지 않습니다.

## 확인

- 최신 main 병합 뒤 충돌 영향 95 passed, 관련 pytest 673 passed·2 skipped입니다.
- 웹 회귀는 CP2 링크 9 passed, #742 카드 17 passed입니다.
- `git diff --check`와 공개 금지 항목 수동 검사는 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 90초 무출력이라 중단했습니다.
- `docs/manual.md`: 최신 main 158,699자 → branch 158,984자(+285자, LF 정규화).

## 남은 것

- 지적 8곳 답글은 마쳤습니다. 수정 head의 CI 확인만 남았고 리뷰 재요청과 병합은 하지 않습니다.
