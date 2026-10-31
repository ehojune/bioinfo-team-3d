# #1076 에이전트 PC에서 node 시험이 못 도는 문제

- 결론: PATH 맨 앞의 `node.cmd` shim(Codex 앱 번들)이 시험 격리로 바뀐 `LOCALAPPDATA`를 보고 node를 못 찾았다. 시험 73건이 main에서도 `node.exe not found`로 실패했다. 제품 코드는 바꾸지 않았다.
- 바꾼 것: `tests/support/node_binary.py`의 `node_binary()`가 실제 `node.exe`를 먼저 쓴다. shim뿐이면 shim을 한 번만, 운영자의 `LOCALAPPDATA`(프로필의 실제 위치, 없으면 `LABHQ_TEST_REAL_HOME` 기준)로 돌려 `process.execPath`만 알아낸다. 시험은 그 실제 `node.exe`를 격리된 환경 그대로 직접 실행하므로 시험의 자식 프로세스는 운영자 폴더를 보지 않는다. `tests/test_web_3d.py`(두 곳)와 `tests/test_runner_wait_snapshot.py`가 `node_binary()`를 쓴다. `tests/test_ci_windows10m.py`의 `node_wrapper` 가짜 이름공간에는 `node_binary` stub 하나만 더했다. `config/ci/windows-boundary-v1.json`에서는 `tests/test_web_3d.py`의 sha256 한 줄만 바꿨다.
- 실행한 것: 두 파일 98 passed, 1 skipped(기존 73 failed). PATH를 `~/bin`의 shim만 남기고 비운 경우에도 브라우저·데모 6건을 뺀 92 passed이고 `node_binary()`가 `…\runtimes\cua_node\…\bin\node.exe`를 돌려준다. CI 계약 시험 6개 파일 포함 485 passed.
- 미확인: 브라우저·데모 6건은 shim-only PATH로 돌리지 않았다. 환경 변수로 `LOCALAPPDATA`를 직접 바꾼 PC는 `SHGetFolderPathW`가 그 값을 반영하지 않아 지원하지 못한다.
- 근거: #1076 댓글 6527238637.
