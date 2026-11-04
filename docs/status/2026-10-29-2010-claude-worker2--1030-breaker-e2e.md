# 폭주 차단기 실제 세션 E2E (#1030)

disposable instance(포트 18787, 연구 요청 아님), Claude Code 2.1.310, 모델 haiku, 직원 1명(`prober`, 도구 `Bash(echo *)`·`Bash(sleep *)`·읽기). `breaker_beat_s: 0.5`, `breaker_hard_stop: true`.

## 결과 요약

| 구간 | 결과 |
|---|---|
| 같은 호출 8번 → warn | **확인**: 8번째에서 `agent.breaker`(shadow)와 피드 경고가 한 번 남음 |
| warn → constrain | **확인**: 11번째에서 `stage: constrain, enforcement: applied`, `agent.control constrained: true, mode: hook` |
| PI 해제(API) → runner ACK | **확인**: `POST /api/tasks/{id}/breaker/release` → `sent: true` → runner가 `agent.control constrained: false`와 `agent.breaker{enforcement: released, command_id, episode}`로 ACK. 해제 요청에서 ACK까지 약 10초(작업이 `sleep 40` 중) |
| 해제 뒤 재승격 | **확인**: 같은 작업에서 다시 warn(8번)→constrain(11번, 새 episode `:2`)→stop(14번) |
| stop → halt(hardStop on) | 이벤트는 **확인**: `stage: stop, enforcement: halt`, `agent.control halt: true`. **그러나 세션은 멈추지 않음**(아래 결함) |
| constrain 중 CSO 판정(hook→broker→gateway) | **미확인**: `restricted.requested`가 한 번도 안 나옴. 제한 중 `echo after-constrain`·`sleep 40`·`echo loop`가 그대로 실행됨 |
| halt→resume | **미확인**: 세션이 멈추지 않아 resume 대상이 없음 |

## 결함: Windows에서 Claude hook 명령이 실행되지 않음 (가장 유력)

- 증거 1: 제한(constrain)·halt가 `agent.control`로 적용됐는데도 Claude의 호출이 거절되지 않았고, 세 번의 실제 세션 모두 `restricted.requested`·도구 거절(`agent.tool_error`)이 0건이었다.
- 증거 2: hook 명령은 `subprocess.list2cmdline([sys.executable, "-m", "labhq.hooks.control", "pre"])`로 만든다(`labhq/hooks/control.py` `hook_commands`, `labhq/hooks/tool_use.py` `add_claude_hook`). Windows에서는 공백이 없는 경로를 따옴표 없이 백슬래시 그대로 낸다. 같은 문자열을 Git Bash로 돌리면 `bash: D:.labhqdev…python.exe: command not found`(rc=127)로 실패한다(백슬래시가 escape로 먹힘).
- 직접 확인하지 못한 것: Claude Code가 이 hook 명령을 실제로 Git Bash로 돌리는지는 모델 호출을 더 써서 확인하지 않았다. 위 두 증거와 일치하지만 단정은 아니다.
- 영향 범위: 같은 방식으로 만든 hook이 전부 영향을 받는다. 즉 #59 ①의 steer·pause·halt, #1028의 constrain CSO 판정, write 도구 영수증 hook이 Windows에서 조용히 동작하지 않았을 수 있다. 단위 시험은 hook 함수를 직접 부르므로 이 경로를 잡지 못한다.
- 제안 수정: hook 명령을 bash와 cmd 양쪽에서 통하게 만든다(`Path(sys.executable).as_posix()`를 따옴표로 감싸기). 시험은 만든 명령 문자열을 실제 `bash -c`로 돌려 보는 것을 넣는다. 파일: `labhq/hooks/control.py`·`labhq/hooks/tool_use.py`·`tests/test_staff_control.py`. 수정은 이 claim 범위 밖이라 재claim이 필요하다.

## 환경 함정 (제품 결함은 아님, 하지만 기록)

- hook은 `sys.executable -m labhq.hooks.control`로 돈다. 이 PC의 시스템 Python에는 오래된 `labhq`(메인 checkout, `ask_restricted` 없음)가 설치돼 있어, 저장소 작업 폴더가 아닌 곳에서 hook이 돌면 옛 코드가 실행된다. 이 시험은 작업 폴더를 가리키는 venv(`e2e-venv`)로 다시 돌렸고 같은 결과였다.

## 작은 점: 자동 해제 기록의 `enforcement`

경고가 신호가 잦아들어 저절로 풀릴 때(첫 실행, `echo after-constrain`이 오자 warn→normal)의 `agent.breaker`가 `stage: normal`인데 `enforcement: applied`다. `_breaker_stage`가 모든 단계에 같은 기본값 `applied`를 쓰기 때문이다. 동작에는 문제가 없고 문구만 어색하다(`released`로 맞추는 것이 자연스럽다). 같은 이유로 해제 뒤 다시 감지된 warn 기록도 `applied`다.

## 관측 로그 (temp, 저장소 밖)

| 항목 | 경로 |
|---|---|
| 설정·직원 정의·감시 스크립트 | `D:\.labhq\dev\tmp\claude-worker2\e2e1030\` (`labhq.yaml`, `agents\core\prober.yaml`, `watch.py`, `promptA.txt`, `promptA2.txt`, `promptC.txt`) |
| 실행 1(beat 3초: constrain 못 감) | `…\e2e1030\runs\2026-10-29\task_11e41c7918_prober\events.jsonl` |
| 실행 2(beat 0.5: warn→constrain→해제 ACK) | `…\task_4a79bddfc5_prober\events.jsonl` |
| 실행 3(해제 뒤 warn→constrain→stop/halt) | `…\task_e8a2b7f49c_prober\events.jsonl` |
| hook 명령 bash 재현 | 이 문서의 위 명령(`bash -c`) |

## 비용

세 세션의 모델 호출은 응답 단위로 세어 약 14~15회(토큰 수로는 정확한 요청 수를 알 수 없음), 비용은 합계 약 $0.011(haiku). 상한 15회 안이다.

## 다음

hook 명령 수정을 재claim해 고친 뒤, 같은 instance로 CSO 판정과 halt→resume 구간만 다시 시험한다(예상 모델 호출 5~6회).
