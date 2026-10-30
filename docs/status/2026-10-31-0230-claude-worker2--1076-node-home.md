# #1076 에이전트 PC에서 node 시험이 못 도는 문제

- 결론: PATH 맨 앞의 `node.cmd` shim(Codex 앱 번들)이 시험 격리로 바뀐 `LOCALAPPDATA`를 보고 node를 못 찾았다. 시험 73건이 main에서도 `node.exe not found`로 실패했다. 제품 코드는 바꾸지 않았다.
- 바꾼 것: `tests/support/node_binary.py`의 `node_binary()`가 실제 `node.exe`를 먼저 쓰고, shim뿐이면 `node_env()`가 그 하위 프로세스에만 운영자의 `LOCALAPPDATA`(`LABHQ_TEST_REAL_HOME` 기준)를 돌려준다. `tests/test_web_3d.py`(두 곳)와 `tests/test_runner_wait_snapshot.py`가 이를 쓴다. 시험 격리는 그대로다. `config/ci/windows-boundary-v1.json`에서는 `tests/test_web_3d.py`의 sha256 한 줄만 바꿨다.
- 실행한 것: 두 파일 98 passed, 1 skipped(기존 73 failed). PATH를 `~/bin`의 shim만 남기고 비운 경우에도 브라우저·데모 6건을 뺀 92 passed. CI 계약 시험 4개 파일 통과.
- 미확인: Windows CI job의 결과는 PR에서 본다. 이 PC에서 브라우저·데모 6건은 shim-only PATH로는 돌리지 않았다.
- 근거: #1076 댓글 6527238637.
