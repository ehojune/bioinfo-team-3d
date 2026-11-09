# 2026-11-09 claude-worker1 — #1295 r14: D단계 3차와 60분 창 상한·노드 고정·포트 확인

## 결론
D단계 3차 접속은 로그인 1회로 성공했습니다(명령 전부 exit 0, 잡 제출 없음). 1·2차 실패는 게이트웨이 창의 원격 계정과 프로필 사용자 이름이 달라서였습니다. 60분 창 로그인 상한, 큐·노드 고정 검사, 접속 전 포트 주인 확인 절차를 넣었습니다.

## 바뀐 것
- `labhq/hpc_login_budget.py`: 시간 창 상한(`per_window`·`window_minutes`), scope `window` 오류와 다시 접속 가능 시각, `budget_for`가 사이트 프로필의 창 상한을 읽음
- `labhq/hpc_site.py`: 프로필 `port_owner_check`·`pinned_nodes`·`login_limit_per_window`·`login_window_minutes`, 질문 4개(끝에 추가), `pin_submit`
- manual 08·15(한·영), `docs/reference/hpc-d-stage-1295.md` 3차 결과

## 실행한 것
- `pytest tests/test_hpc_login_budget_1295.py tests/test_hpc_site_profile_1300.py`: 131 passed
- 실제 사이트 접속 1회(D단계 3차, 결과는 reference 문서)

## 미해결
- `pin_submit`을 `Scheduler.submit_args`에 연결하는 일: `labhq/tools/scheduler.py`는 이번 배정 범위 밖이라 후속
- runner 피드의 상한 알림 문구는 scope `window`에도 "상한을 올리면 다시 확인합니다"를 붙임(`runner/daemon.py`, 범위 밖). 창이 지나면 상태 조회는 자동으로 다시 시작됨
- `hpc_session` 출력에 bracketed-paste 제어 문자·프롬프트 조각이 섞임(#1332 범위)
