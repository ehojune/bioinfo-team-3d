# #621 rev22 — Atlas 응답 종료 경쟁 수정

- 변경: disconnect 확인이 취소를 삼켜도 조회가 끝나면 watcher가 종료하도록 stop flag를 먼저 설정한다. 정상 disconnect의 조회 취소와 제품 deadline은 유지한다.
- 시험: backend 생성 때 포착하고 deadline을 실행 중/시작 전 소진으로 나눠 timeout 기록·backend 취소·실행 coroutine 종료를 확인한다. watcher 취소를 한 번 삼키는 회귀도 추가했다.
- 검증: 수정 전 종료 경쟁 1 failed·deadline 2 passed. 수정 후 Atlas 전체 148 passed·1 skipped, 관련 lookup 49 passed·1 skipped. deadline 두 경로와 watcher 회귀를 각각 10회 반복해 직렬 30 passed·4-worker 병렬 30 passed.
- 한계: 최초 CI 원인은 미확정이다. CI 한도·직렬화 정책은 바꾸지 않았다. 실제 Atlas 조회·설치 Codex sandbox shell은 UNVERIFIED이며 #621은 열린 상태로 둔다.
- 근거: [배정과 이전 진단](https://github.com/ehojune/bioinfo-team-3d/issues/621#issuecomment-6547919066). 최종 head CI·리뷰·병합 결과는 PR에 기록한다.
