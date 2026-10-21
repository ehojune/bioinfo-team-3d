# scheduler:none 경계 — #829

`none`은 HPC 비활성 설정이며 `mock`의 완료·빈 큐 응답과 다르다. Scheduler의 submit/submit_args·queue·status·cancel은 명시적으로 실패한다. `_run`도 SSH 구성 전에 같은 경계를 적용한다.

Runner는 broker/연결을 유지하고 watcher를 예약하지 않는다. watcher와 직접 `_poll_jobs` 진입도 즉시 반환한다. 저장 job·missing count·notified·요청 연결을 보존하며 실제 완료나 요청 0으로 바꾸지 않는다. 시작 로그는 polling 비활성을 알린다.

검증: 새 pure fake 모듈에서 기존 코드 13건 실패를 확인한 뒤 관련 mock 37건 통과. SGE/PBS/Slurm의 제출·취소·큐/상태 해석, mock 응답, none의 subprocess/SSH 호출 0과 기록 불변을 확인했다. event loop·thread·native process·HPC·운영 state는 사용하지 않았다. 실제 로컬 분석·운영 준비·CI 결과는 미검증이다.

변경 범위는 Scheduler/Runner의 HPC 진입점이다. 직원 shell의 네트워크 접근, 외부 trigger, CLI 인증 cache, 운영 인스턴스 복구를 격리한다는 뜻이 아니다. 병합 전 변경이며 배포·연구 실행 승인을 포함하지 않는다.
