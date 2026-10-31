# #606 단계 시작의 파일 시스템 작업을 event loop 밖으로

- 결론: `Runner._run_task`가 단계를 시작할 때 loop thread에서 하던 파일 시스템 작업을 `asyncio.to_thread`로 옮겼다. 대상은 `TaskWorkspace`(작업 폴더 만들기)·`os.path.lexists`·`install_required_skills`(기본 skill 복사)·`resolve_private_paths` 3곳(`stage_inputs` 인자로 미리 평가되던 1곳 포함)·`_system_ca_env`(OS 인증서 저장소 읽기)와 채용 `Recruitment.run`의 build 폴더 만들기다. 호출 자리의 동작·순서는 그대로다.
- 측정(mock lab 요청 1건, D:·`synchronous=FULL`, 3회): #1179 병합 직후 → 이번
  - p99 loop 늦음 33–34 ms → 17–21 ms, 50 ms 넘은 횟수 13–15 → 5–8, 그 정지의 합 1.09–1.68 s → 0.38–0.98 s.
  - 최대 정지 148–158 ms(이상치 417 ms) → 132 · 187 ms(이상치 417 ms). **최대 100 ms 밑 목표는 이번에도 못 맞췄다.** `install_skill`·`private_paths`·`mkdir`는 상위 정지 목록에서 사라졌다.
- 남은 큰 정지: `server.py dispatch`의 `store.put`(#674 보장 때문에 의도적으로 그대로), `knowledge/recall.py`의 `_write`·`store.delete`(이 claim 밖), `rounds._git_commit`(자식 프로세스 대기, create_request).
- 시험: `test_gateway_loop_lag_606.py`에 러너 단계 시작 시험 1개를 더했다(세 함수를 0.2 s씩 늦추고 loop 늦음 0.1 s 밑을 확인, 수정 전 0.632 s로 실패). 정적 시험 `test_recruit_inherited_budget_static`이 `build.mkdir(` 모양을 찾아서 `to_thread(lambda: build.mkdir(...))` 모양으로 유지했다. 관련 계열 1119 passed, 38 skipped.
- 전체 시험: 13,065 passed, 3 failed. 그중 `test_annot`·`test_annot_regulatory` 2개는 main에서도 같은 실패(이 PR 무관), 나머지 1개(위 정적 시험)는 고쳤다.
- manual: 한·영의 "result 수신의 나머지 저장·dispatch…sync 경계는 남습니다"를 실제에 맞췄다(#1179 P2).
- 미검증: 실제 느린 디스크, runner·gateway loop를 나눈 측정, 이 PC D:가 빠른 디스크라는 점.
