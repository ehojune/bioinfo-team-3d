[← 매뉴얼 목차](../manual.md)

## 띄우기와 접속

**HPC 전체 실행의 산출 경로:** 잡은 이미 단계의 `outputs/`에서 시작합니다. 잡 스크립트에는 `outputs/result.tsv` 대신 `result.tsv`를 씁니다. 제출 전 보조 검사는 단순 리터럴 `>`, `>>`, `&>`와 `cp`, `mv`, `install`의 마지막 목적지, `mkdir`, `touch`, `tee`, `cd outputs`를 살핍니다. 입력 읽기는 거절하지 않습니다. 인자를 받는 옵션·변수·호출 스크립트·here-document·작업 폴더 변경 이후 경로 등은 검사하지 못하므로 통과가 안전 보증은 아닙니다.

경로로 지정한 기존 입력은 선택 runner의 사전 검사와 PI 승인을 거쳐 snapshot으로 전달합니다. 변경·누락·전송 검증 실패 시 제출하지 않습니다. [입력과 노드 선택](04-install.md#요청-전체를-hpc-또는-이-pc에서-실행하기).

필수 산출이 없고 같은 경로가 한 단계 더 중첩된 곳에 안전하게 확인되면 같은 직원에게 한 번 수정을 요청합니다. 산출을 옮기지 않고 기존 검증을 유지합니다. 재시작으로 이 수정이 중단되면 중복 실행을 막기 위해 추가 수정 기회를 주지 않을 수 있습니다.

같은 단계·revision에서 성공한 HPC 잡이 같은 파일을 만들면 나중에 제출한 잡의 산출을 사용하고 앞선 사본은 `jobs/replaced/`에 보관합니다. 동시에 제출한 잡의 충돌, 실행 중인 잡, 마지막 성공 뒤의 실패, 파일과 폴더의 경로 충돌은 거부합니다.

수정 재계획에서는 완료 단계의 원격 산출을 선택 runner가 다시 확인한 뒤 재사용 후보로 표시합니다. 성공 run의 증빙과 파일 hash가 일치하지 않거나 확인할 수 없으면 재사용하지 않습니다. 승인 뒤에도 재검사하며 변경되면 새 승인이 필요합니다.

```bash
.\labhq -c config\labhq.yaml up
.\labhq -c config\labhq.yaml open
```

Windows wrapper는 `.venv\Scripts\python.exe`를 직접 실행하므로 venv 활성화나 PowerShell ExecutionPolicy 변경이 필요 없습니다. `up`은 gateway와 runner를 분리된 background process로 띄우고(역할마다 감독 프로세스를 거칩니다. 아래 **감독 프로세스**) state 폴더에 로그와 생성 시각을 포함한 프로세스 기록을 남깁니다. 같은 instance의 `up`·`down`은 state 폴더 lock으로 직렬화되며, 다른 명령이 진행 중이면 exit 2입니다. POSIX에서는 `./labhq.sh`를 씁니다.

**감독 프로세스(#589):** `up`은 역할(gateway, runner)마다 작은 감독 프로세스를 하나씩 띄우고(`python -m labhq.supervisor`), gateway·runner는 그 감독의 자식으로 시작됩니다. `up`이 끝나면 `up`이 직접 띄운 프로세스는 죽은 `up`의 PID를 부모로 계속 기록합니다. Windows는 그 번호를 다른 프로세스에 다시 줄 수 있고, 부모 PID만 보고 트리를 끄는 도구(테스트 코드의 `/T` 종료 등)가 그 프로세스를 끄면 gateway·runner가 함께 죽습니다. labhq 자신의 종료 경로는 생성 시각을 확인하지만(#597) 밖의 코드는 고칠 수 없으므로, 부모 PID가 재사용되지 않게 합니다. 감독은 gateway·runner가 사는 동안 살아 있어 그 부모 PID는 재사용되지 않고, 감독 자신의 부모(`up`)는 시작할 때 조상 프로세스 4개의 handle을 열어 둬서 재사용되지 않습니다(열린 handle이 있으면 끝난 프로세스의 PID도 반환되지 않습니다). 감독은 gateway·runner가 끝난 시각과 종료 코드도 `<role>-supervisor-diagnostics.jsonl`에 `child_exit`로 남깁니다. 강제 종료는 프로세스 자신의 `exit` 기록을 남기지 못하므로 이것이 유일한 단서입니다. 조상 handle을 열지 못해도(권한·이미 끝난 부모) 역할은 시작하고, 이유는 같은 파일의 `parents_pinned` 행에 남깁니다. `up`은 감독이 조상을 고정하고 역할을 기록할 때까지(최대 20초) 끝나지 않습니다.

감독이 역할 기록을 남기지 못해 시작에 실패하면 `up`이 감독을 정리합니다. 이 정리도 실패하면 PID 기록을 보존하고 종료 오류와 PID를 알리며 exit 2로 끝납니다. 다음 `down`·`up`은 같은 감독 기록을 다시 확인합니다(#627).

`supervisor.restart: true`로 켜면 스스로 끝난(종료 코드 0이 아닌) 역할을 다시 시작합니다. 종료 코드 2는 labhq가 시작을 거부한 경우(설정 거부, 다른 설정이 같은 state 폴더를 쓰고 있다는 #613의 소유 기록)라 같은 거부가 되풀이되므로 다시 시작하지 않고 `restart_skipped`만 남깁니다. 처음엔 `backoff_s`(5초) 뒤에, 이어서 두 배씩 `backoff_cap_s`(120초)까지 기다리고, `restart_window_s`(600초) 안에 `max_restarts`(5)번을 넘기면 그대로 둡니다. **기본은 꺼짐**이라 비정상 종료가 가려지지 않습니다. `down`과 웹 종료로 멈춘 역할은 다시 시작하지 않습니다(`labhq-<role>.stopping` 표시 파일을 먼저 남기고, `up`이 지웁니다). 감독이 다시 시작하는 중에 `up`을 실행하면 감독을 하나 더 띄우지 않고 역할이 응답하길 기다립니다. 이 변경 전에 시작한 gateway·runner는 감독이 없고, `status`가 "감독 없음(감독 도입 전 방식)"이라고 알립니다. `down` 뒤 `up`을 하면 감독 아래로 들어갑니다. 감독 프로세스 기록이 있는데 프로세스가 없고 역할만 살아 있으면 `status`·`doctor`가 경고합니다(보호받지 못하는 상태).

2.5D·3D 웹을 열어 두면 gateway 연결 끊김과 runner 미연결을 별도로 알립니다. 재연결 중에도 경고를 유지하고 연결이 돌아오면 지웁니다. 연결 끊김만으로 종료와 네트워크 문제를 구별하지는 않으며, 웹을 닫았을 때 외부 채널로 알림을 보내지는 않습니다.

Windows `up`은 실제 Python interpreter로 Job breakaway를 먼저 요청합니다(venv 선택 유지). 확인된 거부·잔류에만 direct 자식을 소유 handle로 정리한 뒤 임시 local WMI bootstrap을 한 번 시도합니다. nonce·생성 시각·실행 파일·현재 사용자·모든 Job 비소속 확인 뒤 ACK하며 bootstrap은 같은 PID로 supervisor에 진입합니다. receipt·ACK 파일을 다른 handle이 잡고 있을 때의 공유 위반(winerror 32)과, Python 파일 열기가 winerror 없이 남기는 EACCES는 기존 대기 상한 안에서 다시 읽고 그 횟수를 실패 기록에 남깁니다. 다른 winerror·errno는 바로 중단합니다. unknown·정리 실패는 중단하고 flag를 빼지 않습니다. 서비스·Task Scheduler·승격은 없습니다. `logs/detached-launch.jsonl`에는 시도·거부·검증 단계와 Job 잔류/조회 오류를 남기며, 반환 전 로그 쓰기가 실패해도 소유 자식의 정리를 시도합니다. 운영 준비는 role·supervisor의 현재 PID와 생성 시각을 고정한 조회 전용 handle로 모든 Job 비소속을 확인합니다. 과거 로그나 HTTP 인증만으로 대신하지 않으며 확인 불가하면 검증된 경로로 재시작해야 합니다. 배포는 `doctor --runtime` exit 0과 `runtime_readiness.ready=true`를 모두 확인해야 합니다. 기록 없는 소멸만으로 Job 강제 종료를 확정하지 않습니다. [근거·검증 경계](../reference/windows-runtime-795.md). WMI 기동이 지연되면 고정 단계와 경과 시간을 확인합니다. provider 대기 상한은 15초이고(요청 만료 20초에서 bootstrap 시작 여유 5초를 뺀 값, 측정한 상한이 아니라 여유를 둔 값) timeout은 계속 실패로 처리합니다([진단 한계](../reference/windows-provider-793.md)).

**예고 없이 꺼졌을 때:** `status`는 연결을 시도하기 전에, `doctor`는 네트워크 없이 로컬 PID 기록과 마지막 heartbeat·exit를 보여 줍니다. 조회는 PID 기록을 지우지 않습니다. 프로세스가 없고 exit도 없으면 `비정상 종료 의심`입니다. 아래 파일은 설정의 `gateway.state_dir/logs/`에 gateway·runner별로 쌓입니다(직접 실행한 경우도 동일).

| 다음 종료 때 볼 파일 | 내용 |
|---|---|
| `<role>-diagnostics.jsonl` | UTC 시각·PID·부모 PID, 시작, 10분 간격 heartbeat(RSS bytes·Windows handle 수), 예외 종류, signal, uvicorn 종료, exit reason/code |
| `<role>-fault.log` | faulthandler의 native fault traceback, 잡히지 않은 Python 예외의 호출 스택 |
| `<role>.log` | `up` 자식의 버퍼링을 끈 stdout/stderr, uvicorn·runner 일반 로그 |
| `<role>-supervisor-diagnostics.jsonl` | 감독 자신의 시작·exit와 `parents_pinned`(고정한 조상 PID), `child_start`, `child_exit`(역할의 PID·종료 코드·가동 시간·`down` 요청 여부), `restart_scheduled`, `restart_giving_up`, 10분마다 heartbeat(gateway·runner와 같은 간격이라 감독까지 기록 없이 사라져도 사라진 시각을 10분 창으로 좁힙니다). 한 줄이 약 150바이트이고 감독 둘이 합쳐 하루 약 42 KiB입니다(전에는 한 시간 간격, 하루 약 7 KiB). `status`와 `doctor`가 보여 주는 마지막 `child_exit`는 이 로그를 끝에서부터 거슬러 올라가 찾으므로(최대 16 MiB) heartbeat가 쌓여도 밀려나지 않습니다 |
| `<role>-supervisor.log` | 감독의 stdout/stderr(감독이 시작하지 못했을 때의 오류가 여기 남습니다) |

`status`의 종료 요청 표시는 요청 당시 종료됐다는 뜻이며, 종료 원인을 확정하지 않습니다. Windows 강제 종료도 code 1을 남길 수 있습니다.

지원하지 않는 자원 값은 `null`입니다. 예외 메시지·설정은 진단 JSON에 넣지 않습니다. heartbeat는 프로세스 생존 기록이며 gateway 응답 여부를 보장하지 않습니다. 강제 종료·`os._exit()`에서는 exit hook이 실행되지 않으므로 마지막 heartbeat와 같은 시각의 Windows Application/System 이벤트를 함께 봅니다([Python atexit](https://docs.python.org/3/library/atexit.html)). native fault 기록도 모든 외부 종료를 잡지는 못합니다([faulthandler](https://docs.python.org/3/library/faulthandler.html)). 현재 실행 중인 인스턴스에는 다음 시작부터 적용됩니다.

`down`은 진행 중이거나 대기 중인 요청(PI 결정 대기 포함), 끝난 요청에서 돌고 있는 이어 묻기, 진행 중인 채용(`labhq recruit`), 아직 기록을 쓰는 끝난 요청(취소 보고서를 쓰기 전이거나 요청 묶음을 복사하는 중, `마무리 중인 요청`)이 있거나 인증된 상태를 확인할 수 없으면 exit 2로 거부합니다(gateway `/api/shutdown-readiness`, #500·#521). 거부 문구는 단계(취소 기록·보고서 묶음 저장·결과 저장·PI 해석)를 적고, 보고서 묶음 단계는 경과 시간(`n분째`)도 적습니다(나머지 단계는 시작 시각을 기록하지 않습니다). 요청 묶음을 만드는 작업이 10분(`FINISHING_STUCK_S`)을 넘기면 `멈춘 마무리`로 알리고 종료를 막지 않으며, 그 요청은 `bundle_pending`이 남아 다음 시작 때 묶음을 다시 만듭니다(#658). 채용 수는 러너 프로세스 기준이라, 러너를 다시 시작하면 이전 프로세스의 채용은 빠집니다(`runner.hello`의 `process`). 중단을 감수할 때만 `down --force`를 쓰며, 신원이 맞는 runner 프로세스 트리와 gateway만 종료합니다. 역할마다 감독을 먼저 끝내고(다시 시작하지 않도록 표시를 먼저 남깁니다) 그 뒤 역할에 남은 것을 끝냅니다. 감독의 신원을 확인할 수 없으면 아무것도 끝내기 전에 exit 2로 멈춥니다. POSIX는 SIGTERM 뒤 최대 10초 기다리고, 남아 있으면 SIGKILL한 뒤 종료를 확인하고 기록을 지웁니다. 기록된 프로세스를 멈춘 뒤에는 gateway가 아직 답하는지 한 번 더 확인합니다(종료 직후 최대 3초 기다림). 스크립트나 다른 창처럼 `up` 밖에서 시작해 PID 기록이 없는 gateway·runner가 계속 답하면(gateway가 다른 PC에 있으면 이 설정의 runner id가 아직 연결돼 있을 때만) `종료`로 끝내지 않고 "`up`이 아닌 방법으로 시작한 gateway/runner가 아직 실행 중"이라고 알리며 exit 2로 끝납니다. 그 프로세스는 `down`이 멈추지 못하니 직접 끈 뒤 `up`을 다시 실행하세요. 멈출 것이 아무것도 없고 gateway도 답하지 않을 때만 `종료: 기록된 프로세스 없음`을 출력하고 exit 0입니다. Windows에서는 `taskkill /T`를 쓰지 않습니다. 부모 PID가 같아도 부모보다 먼저 만들어진 프로세스는 자식으로 보지 않고, 후보의 handle을 먼저 열어 PID를 고정한 뒤 두 번째 조회로 부모 PID를 다시 확인해 그 handle로 종료하므로, `up`이 끝난 뒤 다른 프로세스가 물려받은 PID 때문에 gateway·runner가 함께 종료되는 일이 없습니다(#589; adapter 취소·시간 초과와 runner의 버전 조사 시간 초과도 같은 방식). `down`은 supervisor·role과 그 자식이 실제로 끝날 때까지 프로세스마다 최대 10초 기다립니다. 하나라도 끝나지 않으면 역할과 PID를 알리고 exit 2로 끝내며, PID 기록을 유지해 다시 시도할 수 있습니다.

**PC를 다시 켰을 때**

gateway의 로컬 여부는 URL hostname과 이 PC 주소로 판단합니다. `0.0.0.0` bind와 TLS proxy를 함께 써도 확인하며, 주소를 확인하지 못하면 로컬 가능성으로 봅니다.

```powershell
cd <labhq 저장소>
.\labhq -c config\labhq.yaml up
.\labhq -c config\labhq.yaml open
```

설정 경로가 없거나 gateway client token이 `change-me` 기본값이면 시작하지 않습니다. `open`과 다른 API 명령은 gateway가 꺼졌거나 HTTP 오류가 나면 traceback 대신 한 줄과 exit 2를 돌려줍니다.
오프라인 데모를 폰으로 보려면 PC와 폰이 같은 Wi-Fi에 있어야 합니다([웹 사무실](06-web-office.md#웹-사무실)).
게이트웨이는 요청·승인·이벤트를 로컬에 저장하므로 상태 디렉터리를 유지할 수 있는 VM이나 집 PC에서 돌립니다
(러너는 재접속 루프로 대기). 러너가 하나도 연결되지 않았거나 필요한 직원의 러너가 꺼져 있으면, 새 요청과 진행 중 단계는 실패하지 않고 `waiting_for_runner`로 기다렸다가 러너가 붙으면 이어 갑니다(PI 점검 R15). 아무것도 보내지 않은 상태라 비용도 없습니다. `gateway.runner_wait_s`(기본 86400초)를 넘기면 "labhq up(또는 labhq runner)으로 러너를 켠 뒤 다시 보내세요"라는 한국어 이유로 끝납니다. 어느 러너도 맡은 적 없는 직원은 기다리지 않고 바로 실패합니다. 러너가 붙거나 명단을 바꿀 때마다 기다리던 단계가 다시 판정해, 붙은 러너가 그 직원을 맡지 않고(꺼져 있는 동안 계약이 끝났거나 yaml을 지운 경우, 처음 켠 gateway에서 다른 직원만 맡은 러너가 먼저 붙은 경우) 기다릴 다른 러너도 없으면 기다림을 바로 끝내고 "연결된 러너 중 그 직원을 맡은 곳이 없다"고 알립니다(#521). `GET /api/health`의 `runner_online`, `labhq status`의 러너 줄, 웹 상단의 **러너 꺼짐** 띠가 연결 여부를 보여 줍니다. gateway가 다시 시작되면 이전 프로세스가 연결돼 있다고 본 러너마다 `runner.offline`(`reason: gateway_restarted`)을 남기므로, 새로고침 없이 다시 붙은 페이지도 러너가 돌아올 때까지 이 띠를 보입니다. 가장 간단한 구성은 게이트웨이 머신과 폰에 Tailscale을 켜고 tailnet 주소로 접속하는 것.
공개 인터넷에 열어야 한다면 TLS 프록시(Caddy 등) 뒤에 두고 토큰을 반드시 교체하세요.

### 웹에서 종료하기

2.5D·3D 상단 **종료하기**에서 진행 요청의 제목·건수, PI 결정 대기, 이어 묻기·채용·마무리 상태를 확인합니다.

| 선택 | 동작 |
|---|---|
| **labhq 끄기** | 일이 모두 끝났을 때 runner와 gateway를 끕니다. 진행 중이면 막습니다. |
| **그래도 끄기** | 중단 경고를 읽고 **진행 중인 일을 중단하고 labhq 끄기**를 한 번 더 눌러야 합니다. 다시 켜면 재개 카드에서 이어 갑니다. 저장 중이던 보고서·묶음은 유실될 수 있습니다. |
| **탭만 닫기** | labhq는 계속 돌아갑니다. 다시 열면 이어서 봅니다. 브라우저가 탭 닫기를 막으면 직접 닫으세요. |

확인 창에는 접속한 서버 주소를 표시합니다. 다른 PC에서 눌러도 **서버 PC의 인스턴스**를 끕니다. Enter로는 종료하지 않습니다.
PI 토큰으로 `POST /api/shutdown`을 호출하며 `down`의 상태 재확인·잠금·PID 신원 검사를 그대로 씁니다. 이 gateway와 runner의 `up` PID 기록이 없거나 맞지 않으면 거부합니다.
순서는 runner를 먼저 끄고, gateway는 응답을 보낸 뒤 **스스로 정상 종료**합니다(종료 hook을 실행하고 열린 연결을 닫음). gateway가 자기 종료를 기다리면 서로 기다리다 강제 종료로 끝나므로, 분리된 감시 프로세스가 30초 안에 끝나지 않을 때만 강제로 끕니다(`state/logs/gateway-exit-watch.log`).
응답의 `stopping`은 접수 표시입니다. 화면은 종료 후 서버 연결이 끊기면 **끝났습니다. 이 탭을 닫아도 됩니다.**를 표시합니다. 네트워크 단절과 프로세스 종료를 브라우저만으로 구별하지는 못합니다. 서버가 종료 실패를 응답하거나 확인 시간이 지나면 실패 안내를 남깁니다.
