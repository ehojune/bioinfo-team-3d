# #1124 semantics lab 시험의 commit fsync 대기 줄이기

- 결론: lab 한 번의 실행은 SQLite commit을 약 840번(이벤트당 약 6번) 하고 매번 `synchronous=FULL`로 fsync를 기다린다. fsync가 느린 디스크에서는 이것이 시험 시간의 대부분이다. lab이 이 실행의 상태 저장소만 WAL `synchronous=NORMAL`로 열게 했다. 제품 코드와 #606의 durable 보장은 건드리지 않았다.
- 바꾼 것: `tests/semantics_shadow_lab.py`의 `run_lab`이 `_unsynced_commits()` 안에서 돈다. `StateStore._write_connection`이 새 쓰기 연결을 열 때 `PRAGMA synchronous=NORMAL`을 더하고, 실행이 끝나면(예외 포함) 원래 함수로 되돌린다. durable 순서·완결을 보는 시험은 이 lab을 쓰지 않는다.
- 전후(이 PC, pytest 임시 폴더를 D:에 둔 경우, 시험 한 건은 off·on 두 번 실행):

  | 시험 | 전 | 후 |
  |---|---|---|
  | `test_the_lab_records_the_same_with_actions_on[shadow]` | 58.8 s | 21.2 s |
  | `…[confirm]` | 59.5 s | 20.2 s |
  | `test_shadow_writes_one_line_per_finished_request` | 48.2 s | 14.8 s |

  기본 임시 폴더(C:)에서는 9.0/8.5/6.3 s에서 8.6/8.2/5.9 s로 거의 같다(fsync가 원래 빠르다). lab 1회 단계 측정: commit fsync 합 31.5 s → 0.4 s, 요청 처리 25 s → 7.3 s.
- 실행한 것: lab을 쓰는 9개 시험 파일 226 passed, 1 skipped(전과 같음). 패치가 실행 뒤 원래대로 복원되는 것을 확인했다.
- 남은 지연: 요청 처리의 나머지 큰 부분은 `owned.py`의 파일 쓰기가 부르는 `os.fsync` 86회(약 3.8 s/회 실행)다. 제품 동작이라 이번에 건드리지 않았다.
- 미확인: CI Windows 러너에서의 전후 시간은 PR의 CI로 본다(전: #1111 run 41107759746, pool 단계 780초 초과).
- 근거: #1124 댓글 6531184747.
