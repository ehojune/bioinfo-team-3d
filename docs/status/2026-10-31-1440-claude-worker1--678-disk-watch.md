# #678 task 도중 디스크 감시

- 결론: runner가 task를 실제로 돌리는 동안(`working` 이후) `runner.disk_watch_s`(기본 30 s, 0이면 끔)마다 작업 폴더·runner 상태 폴더·`runner.bulk_data_root`의 드라이브 여유를 읽고, 하나라도 `runner.disk_fail_free_gb`(2 GiB) 미만이면 그 task를 한 번만 halt한다. 세션은 보존되고 gateway의 기존 `멈춤 · 재개 가능` 흐름이 PI에게 묻는다. 재개 turn은 새 task 시작이라 `_disk_gate`를 다시 거친다.
- 재사용: 멈추기는 `Controls.apply(halt)` → `adapters/base.py::halt_watch`(모든 엔진 공통), gateway는 `agent.breaker`의 `enforcement: "halt"` 기록으로 기존 경로를 탄다(`episode` 없음: 폭주 차단기 해제 흐름과 섞이지 않음). 폭주 차단기의 `breaker_hard_stop`과는 독립이며 `runner.disk_watch_halt: false`면 경고 기록만 남긴다.
- 바꾼 것: `labhq/runner/daemon.py`(`_start_disk_watch`·`_disk_watch`, `_run_guarded`에서 정리), `labhq/settings.py`, `labhq/runner/breaker.py`(`stage_text`의 disk 문구), 예시 설정 두 사본, `tests/test_disk_watch_678.py`(8건), `config/ci/windows-boundary-v1.json`.
- 시험: 한 번만 halt·이벤트 셋·`episode` 없음, 여유 충분하면 무반응, 여러 드라이브 중 낮은 쪽 이름, 읽기 실패 건너뜀, `disk_watch_halt=false`는 경고만, halt 실패 보고, task마다 감시자 하나·종료 시 정리·꺼짐 조건·facilities_fix 제외, `bulk_data_root` 포함. 디스크·breaker·`_disk_gate`·예시 설정 관련 기존 1,933건 통과.
- 미검증: 실제 엔진에서 halt가 `halted` 결과와 세션 보존까지 가는지(기존 halt 시험이 덮음), 실제 디스크가 차는 상황, 기본 간격 30 s와 2 GiB 완충이 빠른 다운로드에 충분한지, 웹 UI가 `signal: "disk"` 기록을 어떻게 그리는지, Windows CI.
- manual 한·영은 이 PR에 없다(claim 순서 대기).
