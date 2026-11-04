# #1154 RESULT.md 별칭 fsync 생략

- 결론: `RESULT.md`(최신 별칭)만 fsync를 생략, `RESULT_<task>.md`는 유지. 결과 본문은 outbox(`task.result`)에도 있어 정전 시 별칭만 옛 것·빈 파일·없음일 수 있고 `RESULT_<task>.md`·outbox에서 복구한다.
- 바뀐 것: `labhq/adapters/owned.py`(`sync` 플래그), `labhq/runner/daemon.py`(별칭만 `False`), `tests/test_durable_writer.py`(3개).
- 실측(D:, lab 1요청): 파일 `os.fsync` 70→58, 파일 쓰기 3.1–3.9 s→2.7–3.0 s.
- 실행: 관련 test 2,988 passed·49 skipped. 새 test 3개는 main 코드에서 실패.
- 미검증: 정전 실험, CI 전체.
