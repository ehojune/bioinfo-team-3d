[← 매뉴얼 목차](../manual.md)

## 설치와 첫 설정


**실험 중인 연결(#666):** 프로세스 경계는 기본 off이며 embedding Python API에서만 요청합니다. 요청하면 아직 unsupported로 실행을 거부하고, offline observer 결과는 합성 근거에 머뭅니다. 실제 보호·인증 cleanup은 미검증입니다. [연결 범위](../reference/sandbox-666-phase2.md).

VM 없는 transport 초안도 기본 off이며 일반 CLI와 연결되지 않습니다. 가짜 peer로 요청 인증·재전송 거절·env/FD 허용 목록을 시험하는 단계로, 실제 OS 인증이나 보호 실행을 제공하지 않습니다. [초안의 범위와 한계](../reference/sandbox-666-transport.md).

**VM 준비 초안(#666, PR #1267 검토 중·draft/hold):** [Hyper-V 설치·정리 안내](../reference/sandbox-666-vm-install.md)에 따라 사용자가 관리자 권한 Windows PowerShell 5.1에서 `Inspect` → `EnableFeature` → 필요한 수동 재부팅 → `Provision`을 직접 실행합니다. 자동 UAC·재부팅·VM 시작은 없으며, 준비된 VM은 전원 OFF·NIC 0 상태입니다.

`RemoveVM`은 VM 설정·checkpoint를 삭제하므로 사전 export/백업과 정확한 VmId·`-ConfirmConfigurationRemoval`이 필요합니다(VHD·ISO·state는 남음). 실제 설치·guest·권한 분리·OS 보호·종료/회수는 **UNVERIFIED**, 같은 관리자 계정 격리는 **UNSUPPORTED**입니다. 기본 off·일반 CLI 미연결·#666 OPEN을 유지합니다.

전제: Python ≥ 3.10, Git, 설정한 직원 CLI의 설치·로그인. npm 설치 CLI는 Node.js도 필요합니다.

```bash
.\labhq init
.\labhq -c config\labhq.yaml doctor
```

PowerShell:

POSIX에서는 `./labhq.sh init`, `./labhq.sh -c config/labhq.yaml doctor`를 씁니다.

`labhq init`은 예시 설정을 복사해 gateway token을 무작위로 만들고 HPC·bioinfo-agent 경로를 묻습니다.
새 설정은 PI가 시험한 동작으로 시작합니다(#613): `research.enabled`·`research.evidence_checkpoint`, 분야 팩 `bulk_tumor_normal@3`·`single_cell_de@3`, `plan.declare_output_types: true`, 그리고 의미 모델이 산출물 hash를 재도록 `runner.workspace_root`를 `internal` 구역으로 둡니다. v1.0에서는 온톨로지(`semantics: {mode: "off", actions: confirm}`, `research.semantic_report: false`)와 Paper2Agent 채용(`recruit.enabled: false`)을 꺼 둡니다(#1336). 켜려면 `semantics.mode`를 `ab`로, 나머지를 `true`로 바꿉니다. 끄려면 설정에서 각 값을 바꾸세요. 이 목록은 `labhq/init_defaults.py`의 `REFERENCE_BEHAVIOUR`이고, `tests/test_init_defaults.py`가 예시 설정·`init` 결과와 어긋나면 실패합니다. 토큰·경로·사람 이름 같은 개인 값은 목록에 없습니다. `pipeline_pr` 동의, `dev_log.repo`, `BIOINFO_AGENT_DIR`은 사람이나 PC마다 달라 기본값으로 켜지 않고 묻거나 찾습니다. 이미 있는 설정은 옮기지 않습니다.
bioinfo-agent 폴더는 `--bioinfo-agent <폴더>`, 이미 있는 `BIOINFO_AGENT_DIR`, `D:/bioinfo-agent`·`~/bioinfo-agent` 같은 흔한 위치, WSL의 `/mnt/d/bioinfo-agent` 순으로 찾습니다. 못 찾으면 묻고(`--yes`면 묻지 않음) 끝내 모르면 "bioinfo-agent 연결 안 됨 — …"을 알리고 연결 없이 설정을 씁니다. 찾거나 받은 경로는 상대 경로여도 절대 경로로 바꿔 저장합니다(직원 task는 자기 작업 폴더 기준으로 상대 경로를 읽어 연결이 깨지기 때문입니다). `doctor`의 `bioinfo-agent` 행이 연결 여부를 warn으로 알리고, 상대 경로로 적힌 설정도 warn합니다.
`dev_log.repo`(개발 기록을 올릴 비공개 저장소)는 `init`이 owner/name으로 한 번 묻고 Enter면 비워 둡니다. 비어 있으면 기록은 이 PC에만 남고 `doctor`의 `dev log` 행이 설정하는 법을 알려 줍니다.
새 설정에서는 pipeline 코드의 공개 PR 기여 동의도 묻습니다. `y`(대소문자 무관)만 동의이며 Enter·`--yes`·그 밖의 답은 끔입니다.
AlphaGenome API 키가 아직 없으면 마지막에 한 번 묻습니다. Enter로 건너뛸 수 있고, 키 보관은 [공개 자원 조회 도구](09-staff-tools.md#공개-자원-조회-도구)에 있습니다.

GitHub 토큰도 한 번 묻습니다(선택, #687). 요청 결과를 GitHub 저장소로 정리하고 이슈로 알릴 때 씁니다. 묻기 전에 필요한 권한을 보입니다. fine-grained 토큰은 Repository creation(또는 Administration 쓰기)·Contents 쓰기·Issues 쓰기가 필요하고 Repository access는 All repositories여야 합니다. classic 토큰은 `repo` 범위면 됩니다. Enter로 건너뛰면 결과는 로컬에만 저장하고, 나중에 `labhq init`을 다시 돌리거나 `GITHUB_TOKEN` 환경 변수를 설정하면 됩니다. 토큰이 저장돼 있으면 init은 새 토큰을 받을지 묻습니다. 만료·취소됐거나 권한을 바꾼 토큰은 여기서 바꾸고, Enter면 그대로 둡니다. 붙여 넣은 토큰은 AlphaGenome 키처럼 이 계정만 읽는 `github.token_file`(기본 `~/.labhq/secrets-github/github_token`)에만 두고, 설정 파일·화면·로그에는 남기지 않습니다. 환경 변수가 있으면 그쪽이 먼저입니다. 파일의 토큰은 gateway 프로세스만 시작할 때 읽고, 직원·runner·MCP 프로세스에는 넘기지 않습니다. 직원에게는 이 파일이 개인 경로라 읽기가 막힙니다. 직원 CLI는 개발용 GitHub App 봇 신원을 쓰지 않으며, 이 PC에 저장된 사용자 GitHub 로그인 권한은 이와 별개입니다. 바로 이어지는 doctor가 권한을 확인합니다(아래 [결과 저장 위치](12-github-reports.md#결과-저장-위치) 참고).
기관 문헌 접근도 묻습니다(선택, #826). 소속 기관, 도서관이 안내하는 공식 HTTPS 프록시 접두(`https://…login?url=` 형태), 쓰는 문헌 서비스, Unpaywall 연락 email을 묻고, 모르거나 나중에 하려면 Enter로 넘어갑니다. 로그인한 뒤 주소창의 주소는 붙이지 마세요. 새 설정에는 프록시(`literature.institution_proxy`)와 email(`annot.unpaywall_email`)만 남기고 기관 세션은 꺼 둡니다. 기관·출판사 이용 조건을 확인한 뒤 직접 켜세요. 기존 설정 파일은 바꾸지 않고 채울 설정 이름만 알려 줍니다. 비밀번호·cookie·API 키는 묻지 않고 접속도 하지 않습니다. Web of Science Research Assistant·Nexus Connect 전용 자동 연결은 아직 없습니다. 받는 주소 형식과 확인 범위: [참고](../reference/institution-literature-826.md).
`python scripts/clean_install_check.py`는 임시 HOME의 새 clone·venv에서 init → doctor → mock 요청 → 요청 묶음 → verify → down을 검사한 뒤 기본 호출에서는 임시 폴더를 지웁니다. 설치판·분리 stdout/stderr 로그를 남기려면 `--work-dir <새-빈-폴더>`를, PASS/FAIL/SKIP JSON 경로를 지정하려면 `--receipt <로컬-receipt.json>`을 붙이세요. 설치판·로그·receipt는 개인 경로가 담긴 로컬 전용 증거이므로 공개 저장소에 올리지 않습니다([상세](../reference/clean-install-922.md)). 실제 직원 CLI 로그인이나 모델 호출은 하지 않습니다. 점검은 실행한 셸과 격리돼서, Git Bash가 내보내는 셸 변수(`MSYSTEM`, `MSYS*`, `MINGW*`, `EXEPATH`, `ORIGINAL_*`)는 점검 환경에 넘기지 않습니다. 이 변수가 남아 있으면 `mingw64`의 `git.exe`가 새 clone 단계에서 죽기 때문입니다. 새 clone 단계까지만 Git Bash에서 확인했고, 설치와 mock 요청까지 전체를 여러 셸에서 돌려 비교하지는 않았습니다.

**mock 엔진으로 연구 모드 끝까지 돌려 보기.** `labhq init`이 쓰는 기본 설정(research 켜짐)에서 `runner.force_engine: mock`이면 CSO가 research 계약을 통과하는 계획을 내고, 계획 → CP1 → 단계 → CP2 → 리뷰 → 보고서까지 `done`으로 끝납니다. 요청 문안에 `[multistep]`을 넣으면 s2가 s1에 의존하는 2단계 계획, `[branched]`를 넣으면 두 갈래(b1·b2)와 둘을 읽는 종합 단계 s3를 가진 분기 계획을 냅니다. 토큰이 없으면 1단계입니다. `[step-fail]`은 다단계 계획의 s2를, `[branch-fail]`은 분기 계획의 둘째 갈래 s2를 실패시킵니다. 실패한 쪽은 보고서 한계와 CP2 경고에 남고, 다른 갈래와 종합 단계는 그대로 돕니다. `[all-branch-fail]`은 분기 계획의 두 갈래를 모두 실패시켜, 종합 단계를 건너뛰고 CP2 없이 요청이 `failed`(outcome `research_failed`)로 끝나는지 봅니다. 연구 모드가 아닌 팀 계획(`work_kind: simple`)에서는 `[step-fail]`이 세 번째 단계(bioinfo-agent)를 실패시키며, 뒤 단계는 건너뛰고 한 번 다시 계획한 뒤 요청 전체가 `failed`로 끝납니다. 단계마다 작업자 한 명이 산출 파일 하나(`outputs/result<번호>.tsv`)를 쓰며, 분야 pack이 적용되는 주제와 갈래 중 실패는 흉내 내지 않습니다. 시험은 `tests/test_e2e_mock.py`입니다.
한 PC에서 여러 연구소를 띄우려면 `labhq init --instance <이름>`을 실행한 뒤 각 명령에 같은
`--instance <이름>`을 붙이세요. 설정·state·runs·talent·포트·token이 인스턴스별로 나뉩니다.
엔진 로그인과 사용량 한도는 모든 인스턴스가 공유합니다.
스케줄러는 `qsub`/`qstat`(SGE·PBS)과 `sbatch`/`sinfo`(Slurm)로 찾고, 종류를 하나로 정할 수 없으면 묻습니다(`--yes`면 멈춤).
스케줄러 도구가 있으면 HPC 상담을 합니다(#119). 읽기 전용 조회(SGE `qconf -sql`·`-spl`·`-sp`·`-sc`, PBS `qstat -Q`·`qstat -B -f`·`pbsnodes -a`, Slurm `sinfo`)로 큐·PE·메모리 리소스·PBS 종류를 모아 `hpc:` 초안을 설정에 넣고 보여 줍니다.
설정을 저장한 뒤 `sleep 1` 시험 잡(1코어, 5분)의 제출 명령을 보여 주고, PI가 `y`라고 답할 때만 한 번 제출해 끝날 때까지 추적합니다. `--yes`·`--dry-run`에서는 묻지 않고, 통제 구역 안에서는 제출하지 않습니다.
클러스터가 없으면 `scheduler: none`이고, 무거운 단계는 로컬 CLI로만 돌리며 그럴 수 없는 단계는 CSO가 계획에서 밝힙니다. `ssh_host`·데이터 계정 전환(`submit_prefix`)은 상담이 정하지 않습니다([설정 포인트](15-config.md#설정-포인트)).
HPC에 ssh로 접속하면 접속마다 기관 전산팀에 로그인 기록이 남을 수 있어, 스케줄러를 SGE·PBS·Slurm으로 고르면 init이 **요청당·하루** HPC 로그인 상한을 묻습니다. 기관마다 값이 다르므로 기본값이 없고, 비워 두면 제한이 없으며(빈 답도 최종), 사람이 터미널에 있을 때만 묻습니다(`--yes`·파이프에서는 묻지 않고 정하지 않은 채로 둡니다). 상한에 닿으면 새 접속을 하지 않고 그 요청의 잡 상태 조회를 멈춥니다. 자세한 동작은 [설정 포인터](15-config.md)의 "HPC 로그인 상한과 카운터"를 보세요. 이미 있는 설정이 미정이면(#1295 이전 설정이거나 `--yes`·파이프로 만든 설정) 터미널에서 다시 실행할 때 한 번 묻고, 그 세 키만 저장합니다(다른 줄과 주석은 그대로).

**HPC 사이트 프로필**(#1300): HPC에 닿는 방법은 기관마다 다릅니다(직접 ssh, 게이트웨이 터널, VPN, 파일 전송 창). 그래서 SGE·PBS·Slurm을 고르면 init이 "이 클러스터의 접속 방법을 사이트 프로필로 적어 둘까요?"를 묻고, `y`이면 프로필 이름과 아래 항목을 차례로 묻습니다: 접속 방식(`ssh`·`gateway_tunnel`·`vpn`·`other`), 접속 명령(공백으로 나눈 단어), 게이트웨이의 로컬 포트를 찾는 방법, 원격 명령 실행 가능 여부, 파일 전송 방법, 유휴 잠김 시간(분), 클러스터 안에서 agent를 써도 되는지, scheduler·큐·작업 경로, 로그인 상한. 모르는 항목은 Enter로 비워 두면 "모름"으로 남고 추측하지 않습니다. 저장할 때 아직 모르는 항목 목록을 보여 주고, 새 설정을 만들며 같은 이름을 고르면 그 항목을 다시 물을 수 있습니다. 나중에 자동으로 다시 묻는 흐름은 아직 없습니다. 접속 명령에 알려진 비밀 패턴(`password=`, `--token`, `sshpass` 등)이 있으면 받지 않습니다. 모든 비밀을 알아보는 검사는 아니므로 비밀은 명령에 쓰지 말고 ssh agent나 키 파일로 인증하세요. 프로필은 `~/.labhq/sites/<이름>.yaml`에 저장합니다. `LABHQ_SITES_DIR`을 설정하면 그 폴더를 씁니다. 저장소 안이나 `.git`이 있는 폴더는 거부하고, POSIX에서는 파일 권한을 사용자만 읽게(0600) 둡니다. 설정 파일에는 프로필 이름만 `hpc.site_profile`로 남습니다. 이미 같은 이름의 프로필이 있으면 비어 있는 상태로 덮어쓰지 않고 기존 값을 기본값으로 보여 줍니다(Enter는 기존 값을 유지합니다. 예·아니오 질문도 `y`/`n`으로 보여 주고 `true`/`false`도 받습니다). 그래도 바꾼 답은 그 파일에 저장되므로, 다른 설정이 같은 프로필을 쓰고 있으면 이름을 달리하세요. 읽을 수 없는 파일이면 저장하지 않습니다. **프로필을 저장소에 넣지 마세요.** 호스트·포트·경로가 들어 있습니다. 공개 전 검사(`scripts/check_public.sh`)는 프로필의 첫 키(`labhq_site_profile`, 따옴표나 들여쓰기가 있어도)가 든 추적 파일과 `.labhq/sites/` 경로가 있으면 실패합니다. 한계: 새 설정을 만들 때만 묻습니다. 이미 만든 설정에서는 묻지 않으며, 그때는 프로필 파일을 직접 만들고 `hpc.site_profile`에 이름을 적으세요(`--yes`·파이프·`--dry-run`에서도 묻거나 저장하지 않습니다). 공개 검사는 flow 표기(`{…}`)나 anchor로 쓴 프로필 복사본까지는 찾지 못합니다. 아직 연결되지 않았습니다. 세션 관리자와 `labhq_hpc` 도구는 지금 이 프로필을 읽지 않으므로 `hpc.site_profile`을 적어도 HPC 동작은 바뀌지 않습니다. 연결(#1295 C단계)이 병합되면 09장과 15장에 적습니다.
`--yes`는 기본값을 수락하고 `--dry-run`은 파일 생성 없이 변경과 doctor 점검을 보여 줍니다. 기존 설정은 보존하며 `--force`일 때만 교체합니다.
Windows에서는 restricted 구역을 빼고, 개인 Codex 지침이 있으면 직원 전용 `CODEX_HOME`을, `~/.claude`가 있으면 Claude 직원 전용 `CLAUDE_CONFIG_DIR`(`~/.labhq/claude-staff`)을 넣고 사람이 실행할 로그인 명령을 안내합니다.
작업 폴더는 새 설정을 만들 때 `runner.workspace_root`로 묻습니다. Windows 시스템 드라이브가 128 GiB 미만이거나 여유 공간이 경고 기준보다 작으면 여유 있는 고정 데이터 드라이브 후보를 안내합니다. 원하는 드라이브를 포함한 경로를 직접 고르세요. `--yes`는 기존 기본 경로를 유지하며 자동 이동·삭제는 하지 않습니다.

Codex `bin`이 비어 있거나 `auto`이면 Windows 앱에서 판본이 가장 높은 폴더를 고르고, 판본을 비교할 수 없을 때만 mtime을 씁니다. doctor는 고른 경로를 표시합니다. 앱이 없으면 PATH를 사용합니다.

**다른 설정이 겹칠 때(#613).** 한 PC에서 설정 파일이 다른 labhq가 함께 돌면 서로의 기록·승인을 섞거나 엉뚱한 사무실을 열게 됩니다. 실행 중인 gateway와 runner는 시작할 때 자기 *정체*를 적어 둡니다: 시작한 설정 파일, 그리고 `gateway.state_dir`·`runner.state_dir`·`runner.id`·포트의 지문(둘 다 해시이고 비밀값은 없음). 파일은 gateway state 폴더의 `labhq-gateway-owner.json`과 runner state 폴더의 `labhq-runner-owner.json`이고, gateway는 `/api/health`의 `identity`로도 같은 해시를 돌려줍니다(경로·token 없음). `doctor`(`instance` 행)와 `up`은 state 폴더·runner state 폴더·포트를 쥔 쪽의 정체를 지금 설정과 비교해 쉬운 한국어와 고치는 명령으로 알립니다. 읽기만 하며 기본 `doctor`는 네트워크를 쓰지 않습니다. `--runtime`은 인증된 gateway 상태를 확인합니다.
- 같은 state 폴더: 다른 정체의 프로세스가 살아 있으면 `up`은 시작하지 않고 `labhq -c <그 설정> open`·`down`을 안내합니다. 이 설정 파일이 시작한 뒤 바뀐 설정으로 아직 돌고 있으면 `down` 뒤 `up`을 안내합니다. 살아 있지는 않지만 마지막으로 그 폴더를 쓴 설정이 다르면 경고만 합니다.
- 같은 runner state 폴더: `runner-<id>.sqlite3`, control 폴더, 입력 hash cache를 두 runner가 함께 쓰면 안 되므로 `runner.id`가 달라도 막습니다. `labhq runner`·`labhq gateway`를 직접 띄워도 시작할 때 같은 검사를 하고 막힌 채로 시작하지 않습니다.
- 같은 포트: 다른 설정의 gateway가 이 포트에서 돌면(그 설정의 프로세스 기록이 그 설정을 가리킬 때만) `up`이 멈추고 어느 설정인지와 `labhq -c <그 설정> open`을 알립니다. 같은 token으로 답하는 gateway도 `identity`가 이 설정과 다르면 쓰지 않고, 정체를 알려 주지 않는 이전 판 gateway는 이 설정의 프로세스 기록이 보증할 때만 받아들입니다. 받아들이지 않은 gateway는 `labhq-last-config.json`·주 설정에 적지 않습니다.
- 주 설정: 처음 `up`이 성공한 설정 경로를 기본 state 폴더(`~/.labhq/state`)의 `labhq-primary-config.json`에 한 번 적습니다. 다른 설정으로 `doctor`·`up`하면 경고하고, 바꾸려면 `labhq -c <새 설정> up --make-primary`를 씁니다. `--instance`로 만든 인스턴스는 일부러 따로 쓰므로 주 설정이 되지 않고 비교하지도 않습니다.
`up`이 자기 gateway임을 확인한 뒤에만 그 state 폴더에 `labhq-last-config.json`을 남깁니다. 설정 파일이 지워지면 그 기록은 무시합니다.

실행 전 `labhq doctor`로 설정·엔진·직원·계산 도구를 점검하세요. `labhq doctor --json`은 러너 상태 디렉터리에 `capabilities.json`을 쓰고, `--network`를 붙일 때만 공개 데이터 사이트에 접속합니다. 이 manifest의 `runner_capabilities`는 러너가 보고하는 기능과 같은 설정·roster에서 산출한 사실입니다. `research lane` 행은 연구 lane이 끝까지 도는지 알려 줍니다. `research.enabled`만 켜면 CP1 뒤 멈춘다는 warn이 뜨고, `evidence_checkpoint`까지 켜야 CP2·리뷰·보고서로 갑니다. `research` 아래 오타 키는 시작할 때 오류입니다.
doctor는 **설정 준비**와 **운영 준비**를 나눠 표시합니다. 배포 전에는 `doctor --runtime`을 사용하세요. daemon의 stale·unknown PID 정체는 실패하며 pristine·정상 down은 별도로 표시합니다. remote·foreground 역할은 로컬 PID를 요구하지 않고 인증된 gateway와 runner 연결로 확인합니다(켜진 WSL runner도 포함). PID 기록이 모두 없는 과거 up/down 이력은 이 인증·연결이 확인된 경우만 해소하며, 인증 없는 missing과 이력 조회 오류는 계속 실패합니다.

Windows에서 검토한 독립 owner Job을 쓰는 인스턴스는 state 밖의 운영자 계약 pin, `contained-runtime.json`, fresh owner challenge로 네 daemon 세대를 확인하면 `contained_verified`로 표시합니다. 실제 검사 코드의 배포 경로·hash도 대조합니다. 기본 detached 검사는 유지하며 인증·runner 연결 실패를 지우지 않습니다. owner receipt의 수명 상한은 `runner.contained_max_s`(기본 5400초, 하한 60·상한 86400)이고, 15시간처럼 긴 연구 실행은 발급하는 owner도 같은 값으로 맞춰야 합니다(#938). 직원 자식·요청 종료·연구 GO를 확인한 값은 아닙니다. [receipt·RPC 계약과 한계](../reference/contained-runtime.md).
doctor의 `disk` 행은 작업·임시·Codex/Claude 직원 홈의 드라이브 여유를 읽습니다. gateway 상태 폴더(`gateway.state_dir`, SQLite·이벤트)가 이들과 다른 드라이브에 있으면 그 드라이브도 따로 읽고, 같은 드라이브면 행을 반복하지 않습니다. 폴더가 아직 없으면 가장 가까운 기존 부모를 검사합니다. 기본 경고는 10 GiB 미만 또는 5% 미만이며 `runner.disk_warning_free_gb`·`runner.disk_warning_free_percent`로 바꿉니다(각각 0이면 해당 기준 해제). 작업 폴더와 gateway 상태 폴더의 드라이브는 여유가 `runner.disk_fail_free_gb`(기본 2 GiB, runner가 task 시작 전 PI에게 묻는 기준과 같음) 미만이면 fail입니다(0이면 fail 없음). 임시·직원 홈 드라이브는 warn까지입니다. 조회 실패도 warn입니다. 단계가 앞 단계 산출을 `inputs/<단계>`로 복사할 때는 runner가 먼저 파일 크기 합을 작업 드라이브 여유와 비교해, 크기 합이 여유보다 크면 파일 내용을 복사하기 전에 멈추고 그 입력을 쓸 수 없다고 이유(필요·여유 크기)와 함께 활동 기록과 직원 안내에 남깁니다. 압축 볼륨처럼 실제 사용량이 파일 크기와 다른 드라이브에서는 이 계산이 맞는다고 보장하지 않습니다. 직원은 CSO에게 묻고, 원본 산출은 그대로입니다. 여유를 읽지 못하면 이전처럼 상한 안에서 복사하고, 복사 중 디스크가 차면 `disk full during copy`로 알립니다. 직원이 셸로 받는 다운로드는 크기를 미리 알 수 없어 이 입력 복사 검사는 막지 않지만, 아래의 셸 명령 확인이 따로 봅니다.

**셸 다운로드·압축 해제 확인**(#678): Claude 직원이 Bash로 `curl`·`wget`·`unzip`·`tar`·`7z x`·`git clone`·`fasterq-dump`·`prefetch`·`aws`·`gsutil`·`rsync` 같은 명령을 부르면, 정책이 허용한 명령도 대상 드라이브 여유를 먼저 봅니다. 대상 폴더는 `-o`·`-O`·`-P`·`--output-dir`·`-d`·`-C`·`git clone`의 폴더 인자가 있으면 그것이고, 없으면 작업 폴더입니다. 한 명령에 적힌 다른 경로(출력이 여러 개인 `curl`, 앞선 `cd`가 옮긴 위치, 리다이렉션 `> 파일`·`>> 파일`·`&> 파일`의 대상)도 같이 읽어 하나라도 기준 아래 드라이브면 묻습니다. 명령은 bash처럼 읽습니다. 따옴표 안의 `;`·`#`은 글자이고, `#`은 단어 시작에서만 주석이며(`url#frag`은 한 단어), `2>&1`처럼 출력을 복제하는 표기는 파일로 보지 않습니다. `curl -dfoo -o 파일`의 값과 출력 옵션을 구분하고 `curl -Lo 파일`처럼 묶은 옵션의 값과 `unzip 파일 -x 패턴 -d 폴더`처럼 패턴 뒤에 오는 `-d`도 읽습니다. 여유가 `runner.disk_fail_free_gb`(기본 2 GiB) 미만이면 크기와 상관없이, 알려진 크기를 풀면 그 아래로 내려가도 승인 카드로 PI에게 묻습니다. 크기는 `unzip`이 zip 목록의 비압축 합, `tar`·`7z x`가 압축 파일 크기의 4배(추정)로 잡고, 다운로드와 그 밖의 명령은 알 수 없습니다. 크기를 모르면 막지 않고, 여유가 경고 기준(`runner.disk_warning_free_gb`·`disk_warning_free_percent`) 아래일 때 작업판에 경고만 남깁니다. 막기만 하는 경우는 없고 PI가 승인하면 그대로 진행합니다. 파이프·변수·치환처럼 해석하지 못한 명령과 읽을 수 없는 드라이브는 건드리지 않습니다. `--allowedTools`에 미리 허용된 `Bash(unzip *)`·`Bash(git clone *)` 같은 규칙은 승인 gate가 있는 직원에게서 빼서 이 확인을 거치게 합니다. runner가 직접 시작하는 로컬 job의 셸 명령에도 같은 확인을 합니다(#1167). 정책이 허용한 명령이 여유 없는 드라이브를 향하면 job을 시작하기 전에 같은 승인 카드로 묻습니다. 여유가 `runner.disk_fail_free_gb` 미만이면 크기를 몰라도 묻고, 크기를 모르고 여유가 경고 기준 아래일 뿐이면 경고만 남깁니다. 확인 중 오류가 나도 정책이 허용한 명령은 막지 않습니다. PowerShell 도구도 같은 확인을 받습니다(#1167): `Invoke-WebRequest`·`Invoke-RestMethod`(`iwr`·`irm`·`curl`·`wget`)의 `-OutFile`, `Expand-Archive`(`-DestinationPath`와 zip 목록의 비압축 합), `Start-BitsTransfer`의 `-Destination`을 같은 기준으로 봅니다. PowerShell 문법으로 읽어 역슬래시는 경로 구분자이고 작은따옴표 안은 글자 그대로입니다. `$r = iwr ...`처럼 값에 담거나 `[void](iwr ...)`처럼 감싸 이름은 보이는데 위치를 읽지 못한 다운로드·압축 해제도 그냥 지나가지 않고, 현재 폴더의 드라이브와 명령에 적힌 드라이브(`D:a.zip`처럼 드라이브만 붙은 상대 경로 포함)의 여유를 모두 봅니다. 하나라도 기준 아래면 묻고, 경고 기준 아래일 뿐이면 경고만 남기며, 아니면 허용합니다. deny는 없습니다. 따옴표 안의 글자나 주석에 든 이름도 같은 확인을 받을 수 있습니다. cmdlet 이름 안의 backtick(`iw`r`)은 PowerShell처럼 무시하고 셉니다. 네트워크 경로(`\\서버\공유`)는 쓰는 쪽 값(`-OutFile`, `-DestinationPath`, `-Destination`)에 있을 때만 봅니다. 이 PC가 여유를 읽을 수 있으면 다른 드라이브와 같은 기준으로 묻고, 읽을 수 없으면 경고만 남기고 막지 않습니다. 읽기만 하는 원본 경로는 보지 않습니다. 명령으로 읽힌 `iwr`가 파일로 가지 않거나 `$` 변수가 든 경로는 건드리지 않습니다(읽지 못한 `$r = iwr ...`는 `-OutFile`이 없어도 위 확인을 받아 여유가 없으면 카드가 뜰 수 있습니다).  `PowerShell(Invoke-WebRequest *)` 같은 사전 승인 규칙도 gate로 돌립니다. 적용하지 못하는 곳: Codex·Gemini·cli 직원(엔진 sandbox가 명령을 직접 실행), PowerShell의 `Copy-Item`·`robocopy`·`Invoke-Expression`, 명령 도중 디스크가 차는 경우입니다. 접두 규칙(`Bash(ls *)`)이 체인 전체를 미리 허용하는 경우는 목록에서 뺐습니다. Claude Code가 체인을 명령마다 나눠 검사하기 때문입니다(#1163). task를 시작할 때(#678)는 runner가 작업 폴더 드라이브의 여유를 다시 읽습니다. `runner.disk_fail_free_gb`(기본 2 GiB, 0이면 끔) 아래이면 task를 시작하지 않고 **디스크 여유 부족** 카드로 PI에게 묻습니다. 승인하면 시작하고, 거절하거나 24시간 안에 답이 없으면(runner가 건 시간 제한) 시작하지 않고 이유를 남깁니다. 웹은 이 카드가 열려 있는 동안 요청을 PI 결정 대기로 보여 줍니다. 판단은 실제로 이번 실행이 쓰는 작업 폴더(재개·재시도로 다른 드라이브를 쓰는 경우 포함)의 드라이브 여유를 기준으로 하며, 작업 폴더를 만들거나 실행 슬롯을 잡기 전에 합니다. 경고 기준(위 10 GiB·5%) 아래이면 작업판 로그에 경고만 남기고 진행합니다. 드라이브를 읽지 못하면 막지 않고, 환경 자동 수정(`facilities_fix`)은 이 검사를 받지 않습니다. 큰 원자료용 폴더는 선택 설정 `runner.bulk_data_root`로 정합니다. 값이 있으면 직원 브리핑에 "큰 원자료(수 GB 이상)는 이 폴더 아래에 받으세요"가 여유 공간과 함께 한 줄 들어갑니다. 이 폴더는 작업 폴더 밖이라 쓰기에 새 허가를 주지 않으며 평소처럼 승인 판정을 거칩니다. 통제 구역에 걸치면 브리핑에 쓰지 않고 경고를 남깁니다. 값이 없으면 지금과 같습니다. 설정한 작업 폴더는 이름과 무관하게 게시 전 개인 경로 검사에 포함됩니다. [검증 근거](../reference/disk-preflight-752.md).

**작업 도중 디스크 감시**(#678): 사전 확인으로 막지 못하는 경우(크기를 모르는 다운로드, Codex·Gemini·cli 직원이 sandbox 안에서 직접 실행하는 명령, 명령 도중에 차는 경우)를 위해, runner가 task를 돌리는 동안 `runner.disk_watch_s`초(기본 30, 0이면 끔)마다 작업 폴더·runner 상태 폴더·`runner.bulk_data_root`·직원이 쓸 수 있는 project 폴더의 드라이브 여유를 읽습니다. 하나라도 `runner.disk_fail_free_gb`(기본 2 GiB) 미만이면 그 task를 한 번 멈추고, 세션은 그대로 둡니다. 멈춘 task는 기존 `멈춤 · 재개 가능` 카드로 PI에게 올라가며 작업 기록에 "디스크 여유가 … 기준 아래라 멈췄어요"가 남습니다. 여유를 만든 뒤 재개하면 새 turn이 시작 때 디스크를 다시 확인합니다(기준 아래면 PI에게 시작 여부를 묻습니다). 멈추지 않고 경고만 남기려면 `runner.disk_watch_halt: false`로 둡니다. 멈추기에 실패하면 다음 회차에 다시 시도하고, 읽을 수 없는 드라이브는 건너뜁니다. 시설 수리 작업은 가득 찬 작업 폴더를 비우는 일이라 감시하지 않습니다. 한계: 간격 사이에 남은 여유보다 많이 쓰면 이미 찬 뒤이며(2 GiB 기준이 완충입니다), gateway가 다른 PC에 있으면 그 DB 드라이브는 runner가 볼 수 없습니다(doctor의 gateway 드라이브 행이 봅니다).

doctor는 roster가 쓰는 엔진만 검사하고, 생략한 네트워크·dry-run 로그인 검사는 한 줄로 묶습니다. Claude `login` 행은 `claude auth status`가 통과해도 직원 폴더의 `.credentials.json`에 적힌 날짜를 읽어, 갱신할 수 없게 만료된 로그인(갱신 토큰 만료, 또는 갱신 토큰 없이 접근 토큰 만료)이면 fail로 바꾸고 다시 로그인하라고 안내합니다(#1276). 토큰 값은 쓰지 않고 아무것도 호출하지 않습니다. status가 API 키·토큰 같은 다른 로그인 방식을 쓴다고 하면 옛 파일은 보지 않습니다. 서버에서 취소된 토큰과 OS 키체인에 저장된 로그인은 이 방법으로 알 수 없어 첫 직원 turn에서야 드러납니다. `runner.contract_dir`를 지정하면 기본 `agents/contract` 대신 그 폴더를 읽고, 계약 유효성·중복 ID 우선순위도 runner와 맞춥니다. 만료 계약의 파일 이동·수정은 하지 않습니다. fail이 하나라도 있거나, 활성 직원이 없거나, roster 엔진 실행 파일이 하나라도 없으면 끝에 `실행 준비: 아니오`와 이유를 표시합니다. 다른 warn은 준비 여부를 바꾸지 않습니다.

`runner.facilities_support`는 기본 `unspecified`(항상 WARN)입니다. `required`는 부재·실패·미확인·dry-run·mock이면 FAIL이며, `optional`은 ready만 OK입니다. init은 기존 명시 값을 보존합니다(`--force` 포함). [판정표와 준비 증거의 한계](../reference/facilities-support-983.md)를 확인하세요.

| doctor 항목 | 판정 | 고치는 명령 |
|---|---|---|
| Paper2Agent | `recruit.enabled: true`일 때 recruiter·skill이 없거나 설정한 commit과 설치본이 다르면 fail. 꺼져 있으면(v1.0 기본)·mock·데모는 skip | `labhq doctor --fix` |

`--fix`는 설정한 `recruit.skill_commit`에 맞춰 Paper2Agent를 설치한 뒤 다시 검사합니다. 다운로드에 실패하면 네트워크·source/ref 확인 안내와 fail을 남깁니다. 별도 설정 파일을 쓴다면 같은 `-c <config>`를 붙이세요. `init`은 설치 검사를 미루며 skill을 내려받지 않습니다. 채용을 켠 인스턴스는 실제 직원 실행 전에 `doctor --fix`를 실행하세요.

직원 모델도 함께 봅니다. Claude는 `haiku`·`sonnet`·`opus` 별칭과 `claude-<family>-<version>` 형식을 검사합니다. Codex는 직원 전용 `CODEX_HOME/models_cache.json`이 있을 때만 그 안의 slug와 대조하고, 파일이 없으면 skip합니다. 최소 CLI는 아래와 같습니다. 더 낮으면 adapter의 격리·결과 계약을 보장할 수 없어 fail, 판본을 읽지 못하면 warn입니다.

| 엔진 | 최소 판본 | 이 판본에서 확인한 의존 기능 |
|---|---:|---|
| Claude Code | 2.1.282 | stream-json result·usage, `--resume`, `--permission-prompt-tool`, hook/settings JSON 격리 |
| Codex | 0.160.1 | `exec --json` result·usage, `exec resume`, hooks·plugins·apps 차단 feature 이름 |

`labhq doctor --canary <agent_id|all>`은 명시할 때만 실제 모델을 부릅니다. 직원마다 짧은 고정 답변, 같은 session의 resume, 시작 직후 cancel까지 세 호출을 하므로 비용이 듭니다. result, 비용·token 보고(엔진이 비용을 안 주면 unknown), session id, resume, cancel을 각각 pass/fail로 표시하며 `--json` manifest에도 넣습니다. CI와 일반 `doctor`는 canary 경로를 부르지 않습니다.
cancel은 Windows Job 종료 계약을 확인한 경우에만 pass입니다. cleanup 근거가 없거나 확인되지 않은 환경에서는 fail이며 프로세스가 남았다고 단정하지 않습니다.
canary는 Windows Job 격리와 종료 확인이 가능할 때만 실행합니다. Linux 등 지원하지 않는 환경에서는 모델 호출 전에 실패합니다. 일반 `doctor` 검사는 그대로 사용할 수 있습니다. [경계와 남은 과제](../reference/engine-canary-35.md)

npm의 `.cmd`/`.bat` shim은 여러 줄 prompt를 손상시킬 수 있어 labhq가 표준 npm shim만 Node.js로 풀어 실행합니다.
풀 수 없는 shim은 거부합니다. Windows에서 직접 지정하려면 `engines.<engine>.bin`과 `prefix_args`를 쓰세요(설치된 package 경로 확인).

Codex 직원 로그인 격리는 [알려진 한계](17-known-limits.md#알려진-한계)의 전용 `CODEX_HOME` 안내를 따릅니다. `labhq doctor`가 구성을 점검합니다.

### 이 PC의 WSL에서 분석하기

Windows 설정을 선택해 `labhq -c <설정> setup-wsl --check`로 도구·plugin·runner 연결을 확인합니다. 분석 준비에는 bioinfo manifest·bioinfo-analyze skill·Java·Nextflow·WSL Claude CLI와 직원 계정 로그인이 모두 필요합니다. 연결·roster 갱신 때 준비가 확인된 WSL만 우선 배정하며, 미준비 상태면 native를 씁니다.

WSL을 설정하지 않은 설치에서 `doctor`는 선택 기능이 꺼졌다는 한 줄만 냅니다. `setup-wsl --check`를 직접 실행하거나 WSL을 활성화한 뒤에는 개별 준비 상태와 실제 WSL 직원 로그인 경로를 안내합니다.

1. `labhq -c <설정> up`으로 gateway를 켭니다.
2. `labhq -c <설정> setup-wsl`로 없는 Nextflow(24.10.5)·editable labhq venv·runner 설정을 준비합니다. 분석 준비와 연결을 모두 확인한 뒤 활성화를 저장합니다. Claude CLI 설치·로그인과 관리자 명령은 안내만 합니다.
3. 이후 `up`·`down`이 WSL runner도 시작·종료합니다. 따로 제어하려면 같은 설정으로 `wsl-runner start` 또는 `wsl-runner stop`을 씁니다. 기존 supervisor와 프로세스 신원 검사를 재사용합니다.

**WSL이 준비되지 않았을 때(#612).** `bioinfo-agent`에게 맡긴 일(직접 요청이나 이미 계획된 단계)이 갈 곳이 없고 설정에 `runner.wsl`이 있으면, 요청마다 한 번 "WSL 준비가 덜 됐어요. 셋업할까요?" 카드가 뜹니다. 승인하면 gateway가 `setup-wsl`과 같은 준비를 thread에서 돌립니다. 15분은 기다림의 상한이라 이미 시작한 준비는 멈추지 못하고, 끝날 때까지 다른 셋업은 새로 시작하지 않습니다. 다른 native runner가 같은 직원을 맡고 있으면(잠시 끊긴 경우) 카드 없이 기존 대기 경로를 씁니다. 관리자 권한이 필요한 명령이 남으면 카드와 오류에 그 명령을 보여 주고, 요청은 runner를 기다리지 않고 바로 멈춥니다(비용 $0). 거절하거나 시간이 끝나도 같은 요청 안에서는 다시 묻지 않습니다. gateway가 WSL이 있는 PC가 아니면 실행하지 않고 `labhq setup-wsl`을 안내만 합니다. 카드와 피드에는 설치 출력이 아니라 항목 이름과 고정 안내만 남습니다.

**이 PC의 WSL에서 실행하기(#612).** 요청을 보낼 때 WSL을 직접 고를 수 있습니다: `labhq send --wsl`, 웹 요청 칸의 "WSL에서" 체크, API의 `run_where: "wsl"`(기본 `auto`). 고르면 계획이 직원 목록을 읽기 전에 위 카드를 요청 단위로 한 번 열고(다른 native runner가 같은 직원을 호스트해도 묻습니다), 승인하면 준비 뒤 WSL runner가 직원을 맡을 때까지 기다린 다음 그 요청의 `bioinfo-agent` 작업을 WSL runner에 고정합니다(`session_runner`). 고정된 작업은 다른 요청의 `bioinfo-agent` 작업이 native runner에서 아직 끝나지 않았어도 WSL runner로만 보내고, 다시 보내거나 gateway 재시작 뒤 이어받을 때도 같은 runner로만 갑니다. WSL runner가 잠시 끊기면 native로 돌리지 않고 그 runner가 돌아오기를 기다립니다. WSL runner를 기다리는 시간은 최대 5분이고, 설정(`runner.wsl`)이 없거나 점검·셋업이 실패하거나 거절·시간 초과가 나면 기다리지 않고 요청의 `run_where_note`에 이유를 남긴 채 WSL 없이 진행합니다. 계획에는 WSL을 실제로 얻은 요청에만 "PI가 이 PC의 WSL에서 실행하라고 골랐다"가 한 줄 전달됩니다. 골랐지만 WSL 없이 진행하는 요청은 대신 `run_where_note`의 이유와 "WSL 없이 실행한다"가 전달되어 계획이 WSL을 가정하지 않습니다. CP1 수정 요청과 후속 요청도 같은 선택을 이어받습니다. 이 선택은 `bioinfo-agent`에만 해당해서, 다른 직원을 직접 고른 요청에 붙이면 API(400)·CLI·웹이 거부합니다. 요청 전체 HPC/local 선택은 아래 절을 보세요.

| `runner.wsl` 설정 | 기본값 / 용도 |
|---|---|
| `distro`, `user` | `Ubuntu-22.04`, 배포판 기본 사용자 |
| `id` | `lab-wsl` — 활성화 때 Windows runner와 같으면 설정·시작 거부 |
| `bioinfo_agent` | `/mnt/d/bioinfo-agent` |
| `root` | Linux 홈의 `.local/share/labhq/wsl-<설정 해시>`; venv·state·로그·직원 설정을 분리 |
| `gateway_url` | 기존 주소 → localhost 실패 시 Windows NAT 주소. 기본 포트 ws 80·wss 443 |
| `enabled` | setup의 연결 확인 성공 시 true |

토큰은 stdin으로 전달하고 Linux의 700 폴더 안 600 설정 파일에 둡니다. Windows mount에는 저장하지 않습니다. NAT에서 연결이 안 되면 gateway를 WSL에서 닿는 주소에 bind하고 방화벽을 확인하세요. setup이 bind나 방화벽을 바꾸지는 않습니다.

작업은 Windows `runner.workspace_root`와 같은 `/mnt/<drive>/…` 폴더를 씁니다. gateway 위치와 무관하게 대상 runner의 `runtime_env`로 작업·결과·이벤트 경로를 변환해 project·reference·`inputs/<step>`·manifest·bundle을 잇습니다. `/home` 작업 폴더·UNC 경로는 거부합니다. setup은 Windows 승인·예산 정책과 reference root(프로젝트 포함)를 복사하고 개인·통제 구역 경로도 변환합니다. 변환 실패는 설정 거부이며, 정책이나 reference root를 바꿨다면 `wsl-runner stop` 후 setup을 다시 실행하세요.

직원마다 담당 runner는 하나입니다. 실행 중이거나 전송 결과가 불확실한 task가 있으면 담당을 유지하고 새 task도 그 runner에 보냅니다. 모두 끝나면 bioinfo-agent는 준비된 WSL을 우선하며, WSL이 끊겼으면 연결된 Windows 직원으로 돌아갑니다. `up`에서 WSL 시작만 실패하면 경고 한 줄을 출력하고 native 시작은 성공으로 마칩니다. CSO는 연결된 runner의 Nextflow·Docker 확인을 계획에 반영합니다. WSL Claude CLI·직원 로그인(`root/claude-staff`)은 별도 준비하며 setup은 로그인하지 않습니다.

### 요청 전체를 HPC 또는 이 PC에서 실행하기

온라인 runner에 내장 scheduler·HPC 도구와 요청 전체를 맡을 worker가 있으면 브리핑 뒤 CP1 전에 실행 위치를 묻습니다(#578). direct 요청은 지정 직원만 확인합니다. 지원 worker가 없으면 질문 없이 local로 정하고, 질문에 답하지 않으면 처리 단계를 시작하지 않습니다. CLI 답변은 `labhq approve <id> --note "1: a"`(HPC) 또는 `"1: b"`(이 PC)입니다.

| 선택 | 실행·데이터 위치 | 시간·비용 |
|---|---|---|
| HPC | 다운로드·준비·QC·분석·그림/보고서 파일 생성을 모두 HPC 잡으로 실행 | 큐 대기·서버 사용료 가능 |
| 이 PC | 모든 처리 단계가 local이며 HPC 잡 제출은 차단 | 큐 대기 없이 PC 자원 사용 |

- 선택은 요청 기록·CP1 카드·보고서 부록에 남고 재시작 뒤에도 유지됩니다. HPC/local 혼합이나 선택한 runner 밖의 HPC 계획은 거부합니다. WSL 선택은 위의 요청별 runner 고정을 따릅니다.
- HPC 원본은 `hpc/<request>/steps/<step>/`에 나눠 둡니다. 잡은 해당 단계의 `outputs/`에서 실행하며 앞 단계 입력은 hash를 확인한 `data/<producer>/<source-task>/` 복사본만 읽습니다. 요청 묶음은 별도로 만듭니다.
- 직원은 스크립트를 준비·제출하고 허용된 요약만 읽습니다. HPC 단계 완료에는 제출 기록이 필요합니다. 공유 계정(`submit_prefix`)의 상위 폴더 접근 권한은 운영자가 준비하며 입력 링크 차단은 유지합니다.
- 기존 project·경로 참고 입력과 runner 환경 binding은 scheduler용 snapshot이 없어 HPC에서 거부합니다. 저장된 precedent 본문은 CSO 계획과 local 단계만 읽고, HPC 단계에는 붙이지 않습니다. 그래서 precedent 본문이 있다고 HPC 요청을 거부하지는 않습니다(#1336). configured project 입력이 있는 직원·외부 HPC MCP만 쓰는 직원은 요청 전체 HPC 선택 대상이 아닙니다.

계획·제출 검사이며 CLI 전체를 격리하는 sandbox는 아닙니다. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED입니다. [구조](08-structure.md) · [검증 근거](../reference/hpc-step-workspaces-578.md).
