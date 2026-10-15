## 결론

`labhq up`이 역할(gateway, runner)마다 감독 프로세스를 하나씩 띄웁니다. 감독이 gateway·runner를 자기 자식으로 시작해 끝까지 기다리므로, 두 프로세스의 부모 PID는 사는 동안 재사용될 수 없습니다. 감독은 역할이 끝난 시각과 종료 코드도 남깁니다. #589에서 얻지 못하던 단서입니다. 시운전이 끝난 뒤 배포합니다. Refs #589.

## 남은 경로

#597은 labhq 자신의 종료 경로만 고쳤습니다. `up`이 끝나면 launcher는 죽은 `up`을 부모로 기록한 채 남습니다. labhq 밖의 코드(test의 트리 종료, 다른 도구)가 부모 PID만 보고 트리를 끄면, 그 번호를 물려받은 프로세스를 따라 gateway·runner가 함께 죽습니다. 10-15 10:38~10:48에 #597을 배포한 뒤에도 같은 모양으로 꺼졌습니다. 밖의 코드는 고칠 수 없으니, 그 PID가 다른 프로세스에 배정되지 않게 합니다.

## 선택한 설계

(a) 감독 프로세스를 택했습니다. 부모가 살아 있으면 PID는 재사용될 수 없고, 종료 코드도 같은 자리에서 남습니다. (b) `PROC_THREAD_ATTRIBUTE_PARENT_PROCESS`는 부모를 다른 오래 사는 프로세스로 지정하는데, 그 프로세스가 죽으면 같은 문제가 돌아옵니다. STARTUPINFOEX를 ctypes로 직접 만들어야 하고 권한이 없을 때의 경로도 갈라져, 단순하지 않아서 뺐습니다.

(a)만으로는 한 곳이 비어 있어 하나를 더했습니다. 감독 자신의 부모는 죽은 `up`입니다. 그 PID가 재사용돼 트리 종료가 거기서 시작하면 감독이 자식으로 잡히고, gateway·runner가 따라 죽습니다. 그래서 감독은 시작할 때 조상 프로세스 4개의 handle을 열어 둡니다(`proc_tree.pin_ancestors`). 열린 handle이 있는 동안은 끝난 프로세스의 PID도 반환되지 않습니다. #597의 종료 helper가 이미 쓰는 성질입니다. `up`은 감독이 이 일을 하고 역할을 기록할 때까지(최대 20초) 끝나지 않습니다. 조상 중 하나인 `up`이 살아 있어야 그 PID를 붙들 수 있습니다.

감독은 역할마다 하나입니다(gateway용, runner용). 한쪽만 다시 띄우는 `up`과, runner만 끄고 gateway는 두는 웹 종료(`down --keep-gateway`)가 있습니다. 감독 하나가 둘을 맡으면 시작·종료 명령을 감독에게 보내는 통로가 따로 필요합니다. 역할마다 하나면 `up`·`down`이 지금처럼 프로세스를 띄우고 끄는 것으로 끝납니다.

## 바뀐 것

- 새 `labhq/supervisor.py`: 조상 handle 고정, 역할 시작과 `labhq-<role>.pid` 기록(이전과 같은 형식), 대기, `child_exit` 기록(역할 PID·종료 코드·Windows 상태 hex·POSIX signal·가동 시간·종료 요청 여부)을 `<role>-supervisor-diagnostics.jsonl`에 남김. 역할이 시작되지 못하면 `child_start_failed`.
- `supervisor.restart`(설정, **기본 꺼짐**): 켜면 스스로 끝난(종료 코드 0이 아닌) 역할을 다시 시작합니다. 5초에서 시작해 두 배씩 최대 120초 기다리고, 600초 안에 5번을 넘기면 그만둡니다(`restart_giving_up`). `down`·웹 종료가 먼저 남기는 표시 파일(`labhq-<role>.stopping`)이 있으면 다시 시작하지 않습니다.
- `labhq/cli.py`: `up`이 감독을 띄우고 `labhq-<role>-supervisor.pid`로 기록한 뒤 역할 기록을 기다립니다. 감독이 먼저 끝났거나 20초 안에 기록하지 않으면 감독을 정리하고 exit 2. 감독이 다시 시작하는 중인 역할에는 감독을 하나 더 띄우지 않고 응답을 기다립니다. `down`은 감독을 먼저 끝낸 뒤 역할에 남은 것을 끝내고(그 사이 끝나 PID가 재사용됐을 수 있어 기록을 다시 읽음), 감독 신원을 확인할 수 없으면 아무것도 끄기 전에 exit 2입니다. `status`·`doctor`는 감독 PID와 역할의 마지막 종료(시각·코드·PID·가동 시간·`down` 요청 여부)를 보여 줍니다. 감독 기록은 있는데 감독이 없고 역할만 살아 있으면 warn입니다. 이 변경 전에 시작한 역할은 "감독 없음(감독 도입 전 방식으로 시작됨)"으로 알립니다.
- `labhq/gateway/shutdown.py`: 웹 종료가 gateway를 끄기 전에 종료 표시를 남깁니다.
- `labhq/settings.py`, `config/labhq.example.yaml`(패키지 사본 포함), `docs/manual.md`: `supervisor` 설정과 설명.
- `labhq/proc_tree.py`: `pin_ancestors`.
- `tests/test_no_tree_kill.py`(가드): tests/와 labhq/의 모든 텍스트 파일(주석·docstring 포함)에서 `taskkill` 뒤 같은 문장의 `/T`·`-T` 스위치를 찾습니다. 여러 줄 인자 목록과 `/F/T`도 잡습니다. 점검 결과 실제 `/T` 호출은 tests/에도 labhq/에도 없었고, 설명 주석·docstring 8곳만 걸려 말을 바꿨습니다. 부모 PID만 보고 트리를 끄는 코드도 없었습니다(`adapters/base.py`의 `_idle_process_tree`는 읽기 전용).

## 확인

- 새 unit test 71건(`test_supervisor.py` 23, `test_supervisor_cli.py` 24, `test_pin_ancestors.py` 7, `test_no_tree_kill.py` 17). 가짜 프로세스·가짜 시계와 실제 PID와 겹치지 않는 값만 쓰고, 실제 프로세스 primitive에 닿으면 실패하게 막아 두었습니다. main에서는 모두 실패합니다(가드는 8곳이 걸리고, 나머지는 `labhq.supervisor`·`pin_ancestors`·`latest_child_exit`가 없어 실패).
- 기존 test 2건을 새 동작에 맞췄습니다: `test_cli_launch.py`의 up·down 기록 test(이제 감독 기록을 확인), `test_process_diagnostics.py`의 로그·버퍼링 test(감독의 스트림을 확인, 역할의 스트림은 `test_supervisor.py`).
- 실제 프로세스 test 6건은 CI 전용입니다(`tests/test_supervisor_process.py`): 실패한 역할의 종료 코드 기록, 재시작과 상한, 밖에서 죽인 역할은 재시작하고 표시된 정지는 재시작하지 않음, POSIX signal 전달, **Windows: 감독이 끝난 부모의 PID를 쥐고 있어 그 PID가 열린 채 남음**, 진짜 gateway가 감독의 자식으로 돌고 `down`이 둘 다 끝냄. 기존 `test_web_shutdown_process.py`도 이제 감독 아래의 진짜 gateway를 씁니다. Windows `clean-install` job이 진짜 `up`→`down`을 돕니다.
- 로컬에서는 프로세스를 띄우거나 끄는 test를 돌리지 않았습니다(PI 시운전 중). 그 밖의 121개 test 파일 3600건 통과, `scripts/check_public.sh`·`scripts/patch_notes.py check` 통과. 읽기만 하는 확인으로 `pin_ancestors()`를 이 PC에서 한 번 불러 조상 4개를 쥐었다 놓았습니다.

## 위험과 남은 것

- 배포 뒤 `down`→`up`을 한 번 해야 감독 아래로 들어갑니다. 그 전에 시작한 gateway·runner는 이전 방식 그대로입니다.
- 감독 자체가 밖에서 강제 종료되면 역할은 다시 죽은 번호를 부모로 가진 채 남습니다. `status`·`doctor`가 warn으로 알리지만 막지는 못합니다. 감독이 죽을 때 역할을 함께 끝내는 job 묶음은 두지 않았습니다.
- 조상 handle을 못 열면(권한, 이미 끝난 부모) 역할은 그래도 시작하고 이유를 `parents_pinned` 행에 남깁니다. 그때는 보호가 없습니다.
- Windows `down`은 트리를 말단부터 끄므로 감독이 `child_exit`를 쓰기 전에 끝날 수 있습니다. POSIX는 기록합니다.
- 프로세스가 역할마다 늘어납니다(감독 launcher와 interpreter). 명령줄로 gateway·runner를 찾는 도구에는 `labhq.supervisor`도 보입니다.
- 10-15의 정지가 PID 재사용 때문이라는 증거는 아직 가설입니다. 다음에 꺼지면 감독의 `child_exit`가 종료 코드를 남깁니다.

Refs #589

🤖 Generated with [Claude Code](https://claude.com/claude-code)
