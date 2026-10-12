## 결론

오늘 19:00 PI 시운전 전에 CLI 결함 3건을 고쳤습니다. `labhq down`의 한글 Windows 추적 출력(점검 1·15, 같은 결함), 기록 없는 gateway를 두고 성공으로 끝나던 `down`(2), `send`·`watch <id>`가 요청 묶음을 출력하지 않던 것(12)입니다. 3건 모두 수정 전 코드에서 실패하는 test를 붙였습니다.

## 바뀐 것

- `down`의 `taskkill`을 `encoding="oem", errors="replace"`로 읽습니다. `PYTHONUTF8=1`인 한글 Windows에서 UnicodeDecodeError 추적이 두 번 나오던 것이 사라지고, `taskkill`이 실패해도 stderr·stdout이 None이라 메시지 조립이 깨지지 않습니다.
- `down`은 기록된 프로세스를 멈춘 뒤 gateway가 아직 답하는지 다시 봅니다(종료 직후 최대 3초 기다림). `up` 밖에서 시작해 PID 기록이 없는 gateway·runner가 답하면 "`up`이 아닌 방법으로 시작한 gateway/runner가 아직 실행 중"이라고 알리고 exit 2로 끝냅니다. 전에는 `종료: 기록된 프로세스 없음`과 exit 0이어서 다음 `up`이 그 프로세스를 그대로 썼습니다. gateway가 다른 PC에 있는 runner 전용 PC에서는 이 설정의 runner id가 아직 연결돼 있을 때만 실패로 봅니다.
- `send`와 `watch <id>`는 끝났다는 이벤트 뒤에 오는 `request.bundle`(또는 요청 기록의 묶음 필드)을 최대 60초 기다려 `요청 묶음:`과 `재현 등급:`을 출력하고 끝납니다. gateway는 끝났다는 기록을 먼저 저장하고 묶음을 나중에 만들기 때문입니다. 요청 기록에 `bundle_pending`이 없으면(단계를 돌리지 않은 요청) 기다리지 않고, 60초가 지나면 `labhq status`로 보라고 알립니다.
- `docs/manual.md`의 `down`과 `send`·`watch` 설명을 고쳤습니다.

## 확인

- 새 test 15건: `tests/test_cli_launch.py` 9건(taskkill 2건, Windows 전용 디코딩 1건, `down` 6건), `tests/test_cli_bundle_wait.py` 6건(실제 `Hub.schedule_terminal`이 커밋하는 이벤트 순서로 `send`·`watch`를 구동). 수정 전 `labhq/cli.py`로 돌리면 11건이 실패합니다. 동작 때문에 실패하는 것은 4건(taskkill 인자·None 처리 1, 기록 없는 gateway 1, 묶음 대기 `send`·`watch` 2)이고 나머지 7건은 새 상수가 없어 먼저 실패합니다.
- 전체 pytest(Windows, `--basetemp`, xdist 없음): 5466 passed, 66 skipped, 8분 50초.
- 기존 `test_down_refuses_active_work_and_force_terminates_only_recorded_trees`가 이 PC의 실제 8787 gateway에 닿아 401로 실패해서 `_health`를 막았습니다.
- `scripts/check_public.sh`, `git diff --check`: 통과.

## 미해결

- 없음. 오늘 밤 8787 instance가 `instances.ps1`로 떠 있으면 PID 기록이 없어 `down`이 이제 exit 2로 알리기만 하고 멈추지는 못합니다. `up`으로 시작한 instance에서만 `down`이 멈춥니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
