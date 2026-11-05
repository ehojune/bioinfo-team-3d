# #678 디스크 감시·halt 안내 P2 두 건

- 결론: (1) #1169의 P2 — 읽기 전용 task도 `runner.bulk_data_root`를 감시해, 쓸 수 없는 드라이브가 기준 아래면 불필요하게 halt될 수 있었다. `_start_disk_watch(..., read_only=)`가 읽기 전용이면 bulk 폴더를 빼고(작업 폴더·state 폴더는 계속 본다) 호출부가 `read_only`를 넘긴다. (2) #1175의 P2 — `halt_watch`가 "프로세스 트리를 끝냈습니다" 안내를 먼저 쓴 뒤 결과 확인을 다시 해서, 완료가 이기면 안내와 최종 상태가 어긋났다. 안내를 `await` 앞이 아니라 kill 뒤로 옮기고, 확인과 표시(`halted`)와 kill 사이에 `await`가 없게 했다. 완료가 먼저면 halt하지도 안내하지도 않는다.
- 부수 효과: #1174 H1의 경합 창(안내 emit)이 구조적으로 사라졌다. 그 경합 시험(안내 emit 중 결과 도착)은 상황 자체가 없어져 "결과 이벤트를 처리하는 중이거나 이미 처리한 뒤의 halt는 결과를 유지하고 kill을 주장하지 않는다" 시험(Claude·Gemini)으로 바꿨다.
- 바꾼 것: `labhq/runner/daemon.py`, `labhq/adapters/base.py`, `tests/test_disk_watch_678.py`(+1: 읽기 전용은 bulk 제외), `tests/test_halt_watch_1174.py`(경합 시험 교체, 결과 뒤 halt 시험 2건·halt가 실행 중이면 여전히 멈춤).
- 시험: 감시·halt·어댑터 fake CLI·halt 재개 e2e 27건 통과.
- 미검증: Windows CI, 실제 엔진.
