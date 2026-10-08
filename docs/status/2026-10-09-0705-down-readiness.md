## 2026-10-09 · labhq down이 이어 묻기와 채용도 확인한다 (#500 첫 항목)

- 결론: `labhq down`은 진행 중 요청 목록만 봐서, 끝난 요청에서 돌고 있는 이어 묻기(follow-up)나 `labhq recruit` 채용 중에는 `--force` 없이 runner를 껐다(#500의 P1, 결과를 잃을 수 있는 유일한 항목). 이제 둘 다 있으면 거부하고 무엇이 돌고 있는지 한 줄씩 보인다.
- 바뀐 것: gateway `GET /api/shutdown-readiness`(client token 필요): 진행 중 요청, `followups[].status == running`인 이어 묻기, runner가 돌리는 채용 수, `ready`. 채용 수는 `/api/recruit`에서 올리고 `recruit.done`·`recruit.failed`나 새 runner 프로세스(incarnation 변경)에서 내린다. CLI `_down`이 요청 목록과 함께 이 API를 본다. API가 없는 이전 gateway면 지금처럼 요청 목록만 본다. `docs/manual.md` down 문장.
- 실행한 것: 새 test 2건(`tests/test_cli_launch.py` down 거부·force·이전 gateway, `tests/test_request_lifecycle.py` readiness·recruit.done)이 main 코드에서 실패하고 이 branch에서 통과. 전체 `pytest -q`(Windows), `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: #500의 나머지 두 항목(같은 instance에서 동시에 `up` 두 번, POSIX 종료 대기)은 그대로다. PI는 Windows 터미널 하나에서 `up`을 한 번 쓰므로 드물다. gateway를 다시 시작하면 채용 수를 잊는다(runner는 채용을 계속함). 이 branch는 문서 PR #513 위에 쌓았다(같은 manual 문장).
- 근거: `labhq/gateway/server.py` `shutdown_readiness`·`recruits_running`, `labhq/cli.py` `_shutdown_blockers`·`_down`.
