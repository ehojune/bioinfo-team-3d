# #1232 — 요청 기한 종료가 후손까지 끝내는지 저장소 시험

| 항목 | 내용 |
|---|---|
| 결론 | #877 rev6 드라이버를 `tests/test_request_deadline_descendants_1232.py`로 옮겼다 |
| 바뀐 것 | 새 시험, 가짜 CLI `tests/fixtures/fake_deadline_cli.py`, `windows-boundary-v1.json` 등록, 패치노트. 제품 코드는 그대로 |
| 실행한 것 | 로컬 Windows에서 1건 통과(8.5초). Linux는 CI에서 확인 |
| 미해결 | `request_wall_clock_s`의 60초 하한과 15초 점검 간격은 gateway 프로세스 안에서만 줄였다. 실제 CLI·MCP 자식은 #739 gate |
| 근거 | https://github.com/ehojune/bioinfo-team-3d/issues/1232 |
