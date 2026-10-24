# 직원이 자기 outputs/에 쓰지 못함 (#946)

- 11차(`req_5aaf34eab7`)의 연구 단계 c1·r1이 `missing outputs`로 실패했다. 직원 settings(`permissions.deny`)에 작업 root(`runner.workspace_root`) 전체의 `Edit(…/**)`·`Write(…/**)`가 있었고, 거부 규칙은 허용 규칙(`Edit(//작업폴더/**)`)보다 우선하므로 자기 `outputs/`도 막혔다.
- 원인: 선행 단계 산출을 읽기 전용 `inputs/`로 받는 경로(#791, `artifact_contract`, `upstream_artifacts`가 dict면 빈 dict여도 해당)가 `private.paths`에 작업 root 전체를 추가한다(`runner/daemon.py`). `claude_deny_private`는 "열린 읽기 폴더를 품은 개인 경로"를 모두 직원 Claude 설정 폴더처럼 다뤄 전체 경로에 Edit/Write 거부를 붙였다. 설정 폴더에는 맞지만 작업 root에는 작업 폴더가 들어 있어 틀리다. `.labhq-system-ca.pem`이 root에 있고 `.credentials.json` 등 이름이 Read 거부에 붙은 것도 같은 경로로 설명된다. 처음 이슈의 "root를 Claude 설정 폴더로 오인"이라는 추정은 맞지 않고, root가 일부러 개인 경로가 된 경우의 규칙 모양 문제다.
- 수정: `claude_deny_private`에 `write_roots`를 받아, 작업의 쓰기 폴더를 품은 holder는 Edit/Write 거부를 전체 경로가 아니라 작업 폴더로 가는 길에 없는 최상위 항목(`closed_entries`)에만 붙인다. 쓰기 폴더가 없는 holder(직원 Claude 설정 폴더)는 그대로 전체 경로 거부다. 러너는 `write_roots=[작업 폴더]`를 넘긴다. 같은 날짜 폴더 안의 다른 단계 폴더는 이전처럼 거부 규칙이 아니라 승인 게이트가 막는다.
- 테스트 `tests/test_workspace_write_946.py` 4건(수정 전 4건 중 3건 실패): 정책 단위 3건과, `upstream_artifacts={}`인 step을 러너로 실행해 생성된 settings에서 `outputs/`·`.tmp/`에 거부가 없음을 확인하는 1건. 관련 626건 통과.
- 미확인: 실제 Claude CLI가 이 규칙으로 쓰기를 허용하는지(실제 요청), 전체 suite, Windows CI. 12차에서 확인된다.
