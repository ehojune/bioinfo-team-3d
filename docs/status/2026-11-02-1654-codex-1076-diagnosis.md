# #1076 rev31 — Windows 실패 진단

- 대상: [run 41355252741, Windows job](https://github.com/ehojune/bioinfo-team-3d/actions/runs/41355252741/job/124993880374), checkout `66bd398c5ef6fa0cdb9cadd43e4a99688d5c035f`.
- 로컬 Windows Python 3.12: 두 시험 원본 실행 `2 passed, 1 warning in 12.92s`. CI 실패 자체는 재현하지 못했다.
- lifecycle: `parked`는 모든 `approval.requested` 발행 뒤 설정된다. CI에는 `seen`과 개별 task의 상태·예외가 없어 빠진 kind와 실패 task를 판별할 수 없다. 3초 제한을 늘리기 전에 timeout에서 누락 kind, task 상태·예외·stack을 기록하고 남은 task를 정리하는 시험 진단이 필요하다. 제품 결함 여부 미확인.
- descendant: 요청 생성 시 6초 deadline이 시작되며 runner readiness나 자손 생성 완료를 기다리지 않는다. 별도 scratch harness에서 runner process 시작을 8초 늦추고 대기를 12초로 줄이자 PID 파일 0개, request `cancelled`, `cancel_reason=request_deadline`, receipt `termination=no_running_tasks`, 동일한 자손 대기 timeout을 관찰했다. 이는 가능한 경쟁의 재현이지 CI 원인 확정이 아니다. CI 로그는 PID 단계와 request 상태를 남기지 않는다.
- 수정 claim 제안: `tests/test_request_lifecycle.py`에 위 task 진단; `tests/test_request_deadline_descendants_1232.py`에 단계별 PID·request 상태·gateway/runner 로그를 실패 시 기록하고, runner 준비와 deadline 시작 순서를 결정적으로 조정하는 방안 검증. 필요 시 `tests/fixtures/fake_deadline_cli.py`와 gateway/helper 추가 claim. timeout만 늘리지 않는다. CI 재실행·제품 변경은 하지 않았다.

## rev32 — 시험 보강

- lifecycle timeout에 누락 kind와 task 상태·예외·stack을 기록하고 task를 정리한다.
- descendant 시험은 자손 생성 후 시험용 deadline을 시작한다. runner 시작을 8초 지연해 기존 경쟁을 검증하고 종료 receipt와 무관 프로세스 생존을 확인한다.
- Windows Python 3.12 대상 시험 `2 passed, 1 warning in 21.65s`. 주입 실패로 lifecycle 진단과 descendant 실패의 PID·request·로그 출력을 확인했다.
- 실제 CI 실패 원인과 제품 결함 여부는 미확인이다.
