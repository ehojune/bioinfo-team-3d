## #842 — 강제 정리 회귀 위치 조정 (source 3dd6180)

- peer P2: 새 실Chrome 회귀가 focus layout 파일의 30초 상한 여유를 줄였다. 그 파일은 #839 진단과 #834·#835·main 로그에서 Windows 실측 6.6~12초다.
- 같은 회귀를 `web_visual_tooling.cjs`로 옮겼다. 이 파일은 driver 자체 계약 파일이고 실Chrome 검사 1개가 이미 있다. Windows 실측은 3.3~5.6초, pytest 상한은 170초다. focus 파일은 `95346f0` 상태(두 demo·기능 단언·시작 trace)와 같다.
- 5초 cleanup, 3초 graceful, 30/170초 상한, `test_web_3d.py`·config, Windows 선택(`test_office_in_node` 보호 node)은 바뀌지 않았다.
- 검사: bundled Node의 parser 전용 `--check`로 변경 CJS 3개를 통과했다(NODE_OPTIONS·NODE_PATH·NODE_COMPILE_CACHE 제거, 프로그램은 실행하지 않음). 정적 CI 계약 199 passed, public·diff 검사 통과, guard blocked 0.
- 미실행: 실제 Node·Chrome·Windows 실행 결과. #842는 열어 둔다.
