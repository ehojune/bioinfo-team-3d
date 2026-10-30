# #1055 읽기 전용 Codex 직원의 셸 네트워크

- 결론: 5차 블라인드 리뷰어(`sci_reviewer`)의 셸이 Europe PMC·RCSB·ChEMBL을 직접 호출했고 조회 집계에 들어가지 않았다(집계 DB에서 그 task는 0건). 원인은 Windows elevated sandbox가 프로필·`network_access` 설정과 무관하게 셸 연결을 허용하는 것이다.
- 시험: Codex 0.165.0-alpha.1.2에서 `codex exec` 6가지 설정 중 elevated는 하나도 막지 못했고 unelevated만 막았다(#382 보호를 잃어 쓰지 않음). 표는 `docs/reference/codex-shell-network.md`.
- 바꾼 것: `labhq/adapters/codex.py`에 `SHELL_NO_PROXY_ENV`를 추가해 read-only이고 웹 도구가 없는 Codex 직원의 셸에만 닫힌 프록시를 준다. Codex 프로세스 자신의 환경은 건드리지 않는다.
- 시험(단위): `tests/test_codex_deny_read.py`에 4건. 전체 15건 통과.
- 한계: 프록시를 무시하는 코드는 막지 못한다. 경계가 아니라 실수 방지다. 근본 대책은 #666.
- 미확인: 이 설정에서 셸이 정상 실행되는지. 시험 중 elevated sandbox 설정 도우미가 UAC(오류 1223)를 띄워 셸이 실행되지 않았고 통제군을 돌리지 않았다. 그래서 PR은 draft·hold이며 통제군 확인 전에는 병합하지 않는다.
- 근거: `docs/reference/codex-shell-network.md`, #1055 댓글.
