# PI 인스턴스가 기록 없이 꺼진 일 (#589): 10-24 이후 감사 (2026-11-07)

lookup-only. 읽기 전용으로 PI 인스턴스의 로그를 읽었다. 인스턴스는 재시작하거나 건드리지 않았다. 대상은 `~/.labhq/state/logs`와 `labhq-v0.2/logs`, Windows System 로그다.

## 결론

| 질문 | 답 |
|---|---|
| 10-24 이후 기록 없이 꺼진 적이 있는가 | **판단할 자료가 없다.** PI 인스턴스는 10-20 06:29 이후 한 번도 시작되지 않았다. 10-24부터 지금까지 로그가 한 줄도 늘지 않았다 |
| 그 전에는 | 10-20 09:59~10:09 KST에 gateway·runner·두 supervisor가 **함께 종료 기록 없이** 사라졌다. supervisor 구조가 들어온 뒤의 첫 사례다 |
| #589를 닫아도 되는가 | **아니다.** 재발이 없어서가 아니라 관측이 없어서 닫을 근거가 없다. 열어 두고 재개 조건을 아래에 적는다 |

## 근거

### 10-24 이후 PI 인스턴스는 돌지 않았다
- `~/.labhq/state/logs/*`의 마지막 수정은 모두 10-20이다(gateway·runner diagnostics 09:59, runner.log 10:04, supervisor diagnostics 09:29).
- `~/.labhq/state/labhq-gateway.pid`·`labhq-runner.pid`는 10-20 06:29에 쓰였고 지금도 남아 있다. 정상 `labhq down`이 지운 흔적이 없다.
- `gateway.sqlite3-wal`의 마지막 쓰기는 10-20 06:29이다. `labhq-v0.2/logs/pi-up.log`의 마지막 `up`도 10-20 06:29이다.
- 8787·8788 포트에 듣는 프로세스가 없다.
- 이 PC는 11-02 13:29에 다시 부팅했다. 10-21 18:10 총괄 확인(기록된 신원 네 개 모두 stale)과 같은 상태가 이어졌다.

### 10-20에 사라진 것
`*-diagnostics.jsonl`을 pid별로 묶은 시간표(2026-10-17 09:28 이후. 그 전에는 supervisor가 기록 없이 끝난 세션이 여럿이라 비교에서 뺐다).

| | 세션 수 | 종료 기록(`exit`)이 있는 것 | 없는 것 |
|---|---|---|---|
| gateway supervisor | 23 | 22 | **1** (pid 38860, 10-20 06:29 시작) |
| runner supervisor | 23 | 20 | 3 |

- runner supervisor의 3건 중 둘(pid 51712, 4972)은 `child_exit`가 마지막이고 몇 초 안에 다음 세션이 시작됐다. 정상 재시작에서 supervisor의 `exit` 줄만 빠진 것으로 보이고 사라진 것이 아니다. 나머지 하나(pid 22996)가 10-20 06:29 시작 세션이다.
- 10-20 06:29 세션의 네 프로세스의 마지막 기록: gateway(pid 17588) heartbeat 09:59:25, runner(pid 58616) heartbeat 09:59:29, 두 supervisor의 시간당 heartbeat 09:29(다음은 10:29였을 것인데 없다). `child_exit`·`exit`·`uvicorn_end`·fault 기록은 없다.
- `runner.log`의 마지막 줄은 10-20 10:04:52 `gateway connection lost/failed: no close frame received or sent (retry in 1s)`이다. 재시도 줄이 더 없다. gateway가 10:04:52 이전에 사라졌고 runner는 그 뒤 몇 초 안에 사라졌다.
- supervisor까지 `child_exit`·`exit` 없이 사라졌으므로 자식 하나가 죽은 것이 아니다. 네 프로세스가 한꺼번에 하드하게 끝났다.

### 같은 시각 다른 곳
- System 로그 10-20 09:40~10:20: 종료·로그오프·절전·서비스 중지 이벤트는 없다. 있는 것은 09:40 AhnLab V3의 파일 시스템 필터 드라이버가 내려갔다 다시 올라온 것(09:40:15~09:40:37, 사라진 시각보다 20분 앞), 09:47과 10:04의 `VMICTimeProvider`(Hyper-V 시간 동기) 이벤트다. 후자는 10-19~20 이틀 동안 10건 나온 평소 이벤트이고, 10:04:08이 runner의 연결 끊김(10:04:52)과 가깝지만 이틀간 10건 중 하나라 원인으로 보지 않는다.
- Security 로그(프로세스 생성·종료 4688/4689)는 관리자 권한이 필요해 읽을 수 없었다. 로그온 세션 로그(`TerminalServices-LocalSessionManager` 등)는 조회되지 않았다.
- 개발 lane의 활동: 09:58~10:10 사이에 `D:\.labhq\dev`와 `labhq-v0.2`, `~/.labhq` 아래에서 바뀐 파일은 PI 인스턴스 자신의 로그 세 개뿐이다. 10-20은 개발 lane이 생기기 전이라(`D:\.labhq\dev\lanes`는 10-23 이후) 이전 가설(lane의 test가 PI 프로세스를 끈다)을 지지하는 흔적도 반증도 없다.

## 원인 후보 (10-20 사건)

| 후보 | 근거 | 반증·미확인 |
|---|---|---|
| ① 사용자 세션 종료(로그오프, 원격 연결 정리)로 세션의 모든 프로세스가 같이 끝남 | supervisor까지 포함해 네 프로세스가 동시에 기록 없이 끝남. detached로 띄웠어도 세션 종료는 막지 못함 | System 로그에 로그오프 이벤트가 없다. 세션 로그는 이 PC에 없어 확정 못 함 |
| ② 외부 프로그램(백신 등)이 프로세스를 종료 | 09:40에 백신 드라이버가 한 번 재적재됨. 하드 종료와 모양이 맞음 | 사라진 시각과 20분 떨어짐. 종료 주체를 가를 Security 로그를 못 읽음 |
| ③ Job 객체 종료(KILL_ON_CLOSE) | 같은 job에 있던 네 프로세스가 함께 끝남 | `up`이 분리된 supervisor로 띄우는 구조지만 job 소속은 로그에 없음 |
| ④ lane의 test가 프로세스를 끔 (10-14·15 가설) | 10-14~15 정황 | 10-20 시각에 lane 활동 없음 |
| ⑤ VM(Hyper-V) 단위의 일시 정지·복구 | System 로그가 Hyper-V 게스트임을 보여 줌 | 게스트 안의 시계·이벤트에는 정지 흔적이 없음 |

원인은 확정하지 못했다.

## 고칠 범위 (제안, 이번에는 코드 수정 없음)

1. **관측 간격 좁히기(같은 PR에서 함)**: supervisor 시간당 heartbeat를 gateway·runner와 같은 10분으로 줄였다(`labhq/supervisor.py`의 `HEARTBEAT_S = 600`, 전에는 `interval=3600`). 로그 증가는 supervisor 둘 합쳐 하루 48줄(약 7 KiB)에서 288줄(약 42 KiB)로, 줄당 약 150바이트다. 이번에는 사라진 시각을 10분 창(09:59~10:09)까지만 좁혔고 supervisor 쪽은 한 시간 창이라 쓸모가 적었다.
2. **PI가 직접(보안 설정이라 labhq가 하지 않는다)**: `auditpol /set /subcategory:"Process Creation" /success:enable`로 프로세스 생성 기록을 켠다(#589 총괄 댓글 10-14에 명령이 있다). 종료 기록 4689가 필요하면 "Process Termination"도 켠다. 다음에 사라질 때 누가 끝냈는지 Security 로그에 남는다.
3. **재개 조건**: PI가 인스턴스를 현재 main으로 다시 띄운다. 그 뒤 며칠(예: 3일) 돌려 `*-diagnostics.jsonl`의 `exit`·`child_exit` 없는 끝을 확인한다. 이 문서의 pid별 시간표 스크립트가 그대로 쓰인다(임시 폴더에 있고 저장소에는 없다).

## 한계
- 로그는 이 계정의 것만 읽었다. 같은 PC의 다른 계정 프로세스와 로그는 보지 않았다.
- System 로그의 보존 시작은 09-29다. 10-20 창은 들어 있다.
- 10-24 이후 인스턴스가 돌지 않았으므로 "재발 없음"은 결론이 아니다.
