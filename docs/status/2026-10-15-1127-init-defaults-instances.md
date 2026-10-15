## 결론

Refs #613 (PI 결정 C). 새 `labhq init` 설정이 PI가 시험한 동작으로 시작하고, bioinfo-agent 위치를 찾거나 묻고, `doctor`와 `up`이 설정이 다른 인스턴스를 쉬운 한국어로 경고합니다. 기존 설정은 바꾸지 않습니다.

## 바뀐 것

**init 기본값** (`config/labhq.example.yaml`과 패키지 사본, `labhq/init_defaults.py`)

| 설정 | 새 설정의 값 | 전에는 |
|---|---|---|
| `research.enabled` · `research.evidence_checkpoint` | true · true | false · false |
| `research.active_packs` | `bulk_tumor_normal@3`, `single_cell_de@3` | 빈 목록 |
| `semantics` | `{mode: ab, actions: confirm}` | `off` |
| `plan.declare_output_types` | true | false |
| `policy.data_zones` | `runner.workspace_root`(인스턴스는 자기 폴더)를 `internal` 구역으로 추가 | 없음 |

- 마지막 줄은 PI 설정에 들어 있는 항목입니다. 의미 모델은 `public`·`internal` 구역 안의 산출물만 hash를 재므로(manual) 없으면 `ab`의 재사용 후보가 비게 됩니다.
- 기준 목록 `REFERENCE_BEHAVIOUR`은 설정 경로와 값 6개뿐이고 토큰·경로·이름이 없습니다. 예시 설정 두 사본과 `init` 결과가 어긋나면 `tests/test_init_defaults.py`가 실패합니다.
- 일부러 켜지 않은 것: `policy.bioinfo_agent.pipeline_pr`(동의를 묻는 값), `dev_log.repo`, `BIOINFO_AGENT_DIR`(PC마다 다름), `hpc.scheduler`(init이 감지).

**bioinfo-agent 연결** (`labhq/bioinfo_link.py`, `init_wizard.py`)
- 저장하는 경로는 상대로 받아도 절대 경로입니다(직원 task는 자기 작업 폴더 기준으로 상대 경로를 읽습니다). `doctor`는 상대 경로로 적힌 설정을 warn합니다.
- 찾는 순서: `--bioinfo-agent PATH` → 기존 `BIOINFO_AGENT_DIR` → `D:/bioinfo-agent`·`C:/bioinfo-agent`·`~/bioinfo-agent`·`~/Desktop/bioinfo-agent` → WSL의 `/mnt/d/bioinfo-agent`·`/mnt/c/bioinfo-agent`(#612).
- 찾으면 `--yes`는 바로 연결하고 대화형은 확인합니다. 못 찾으면 대화형은 경로를 묻고 `--yes`는 묻지 않습니다.
- 끝내 없으면 설정에 넣지 않고 "bioinfo-agent 연결 안 됨 — …"과 고치는 법을 출력합니다. `doctor`의 `bioinfo-agent` 행이 같은 말로 warn을 냅니다(폴더에 plugin이 없어도 warn).
- `--bioinfo-agent`로 준 폴더에 plugin이 없으면 init이 멈추고 아무것도 쓰지 않습니다. 경로는 출력하지 않습니다.

**다른 인스턴스 경고** (`labhq/instance_guard.py`, `cli.py` up, `gateway/server.py` health, `doctor.py` `instance` 행)

실행 중인 gateway와 runner는 시작할 때 **정체**를 적습니다: 시작한 설정 파일과 `gateway.state_dir`·`runner.state_dir`·`runner.id`·포트의 지문(해시, 비밀값 없음). 기록은 gateway state 폴더의 `labhq-gateway-owner.json`, runner state 폴더의 `labhq-runner-owner.json`이고, gateway는 `/api/health`의 `identity`(해시만, 경로·token 없음)로도 돌려줍니다. 검사는 모두 "state·runner state·포트를 쥔 쪽의 정체"를 지금 설정과 비교하는 하나의 규칙입니다.

| 쥔 쪽 | 정체가 다르면 `up` | `doctor` |
|---|---|---|
| gateway state 폴더(owner 기록, 옛 pid 기록) | 멈춤. 그 설정의 `open`·`down`을 보임. 같은 설정 파일인데 시작한 뒤 바뀐 설정이면 `down` 뒤 `up` | warn |
| runner state 폴더(`runner-<id>.sqlite3`·control 폴더·입력 hash cache, `runner.id`가 달라도) | 멈춤 | warn |
| 포트: 다른 설정의 gateway 기록(그 설정을 가리킬 때만), 또는 답한 gateway의 `identity` | 멈춤. 아는 설정이면 `labhq -c <그 설정> open`, 모르면 찾는 법 | warn(기록만) |
| 정체를 안 알려 주는 이전 판 gateway | 이 설정의 살아 있는 기록이 보증할 때만 받아들임. 아니면 경고하고 기록 안 함 | - |
| 마지막으로 그 state 폴더를 쓴 설정이 다름(`labhq-last-config.json`) | 경고 | warn |
| 이 PC의 주 설정과 다름(`labhq-primary-config.json`) | 경고. 바꾸려면 `up --make-primary` | warn |

- `up`은 gateway가 답할 때마다(시작 전, 방금 시작한 뒤) 정체를 확인하고, 확인된 gateway만 last·주 설정에 적습니다. 새 gateway가 포트를 못 잡아 다른 gateway가 답해도 방금 시작한 것으로 치지 않습니다.
- `labhq runner`·`labhq gateway`를 직접 띄워도 시작할 때 같은 검사를 하고(`claim`) 막히면 `Runner`를 만들기 전에 끝납니다.
- `doctor`는 읽기만 하고 HTTP를 하지 않습니다. 포트에서 답하는 모르는 gateway는 `up`만 압니다.
- `--instance`로 만든 설정은 일부러 따로 쓰는 것이므로 주 설정이 되지 않고 비교하지도 않습니다. 설정 파일이 지워진 기록은 무시합니다. 러너만 있는 PC는 포트 비교를 건너뜁니다.
- 경고문은 doctor에서 홈 폴더를 `~`로 가리고, 고치는 명령에는 실제 경로를 씁니다.

**dev log** — `dev_log.repo`는 기본값 없이 init이 owner/name으로 한 번 묻습니다(Enter면 비움, 형식이 틀리면 비움, `--force`는 기존 값 유지). 비어 있으면 `doctor`의 `dev log` 행이 설정하는 법을 알려 줍니다.

**문서** — README 설치 절, `docs/manual.md`(init, doctor, 겹침 경고, bioinfo-agent 연결).

**격리 설치 검사** — `scripts/clean_install_check.py`가 `init`에 Enter 세 번만 줘서, 질문이 하나 늘자 마지막 동의 질문에서 EOFError가 났습니다(임시 HOME에서 재현). 입력을 Enter 여덟 번으로 늘렸습니다.

## 확인

- 새 test 69건: `tests/test_init_defaults.py` 36건, `tests/test_instance_guard.py` 33건(봇 지적 4건 대응 16건 포함, 대응 전 소스에서는 16건 모두 실패). 임시 폴더와 가짜 process 기록만 쓰고 실제 프로세스·포트는 쓰지 않습니다. 옛 예시 설정으로 되돌리면 새 test 6건과 고친 semantics test 1건이 실패합니다.
- 기존 test 수정 3곳(약화 없음): `test_init_wizard.py`의 Windows `--yes` 구역 기대값(빈 목록 → 작업 폴더 internal 구역 하나)과 대화형 답 순서(dev log 질문 한 줄 추가), `test_semantics_shadow_settings.py`의 "예시 설정이 off" 확인(코드 기본 off 확인은 유지하고, 예시는 ab·confirm으로 확인). 이 test는 action layer만 지우는 검사(`test_semantics_shadow_remove`)에서도 돌아서 `actions` 키를 뺀 설정으로 mode를 확인하고, actions 쪽 확인은 그 layer가 소유한 `test_semantics_actions.py`에 뒀습니다. `tests/conftest.py`는 흔한 위치 탐지를 테스트마다 비웁니다(이 PC의 `D:/bioinfo-agent`에 test가 기대지 않게).
- 첫 CI에서 위 semantics test가 3개 환경 모두 실패했고(`ShadowConfig(actions=True)`가 action layer 제거본에 없음), 고친 뒤 `test_semantics_shadow_remove.py` 6건을 로컬에서 통과시켰습니다.
- 로컬 Windows pytest 전체: 82%까지 전부 통과, 나머지에서 59건 실패. 59건은 main에서도 똑같이 실패합니다(`tests/test_web_3d.py` 58건은 격리 HOME 아래에서 `node.exe`를 못 찾음, `test_staff_control_steps.py` 1건은 `tasklist` 출력이 None). node test 58개 파일은 `node --test`로 모두 통과.
- `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.

- 봇 지적 4건(PR 인라인): runner state 폴더 겹침(P1), 같은 token으로 답하는 미등록 설정의 gateway 채택(P1), 후보 설정의 프로세스 기록 소유자(P2), 상대 bioinfo-agent 경로(P2). 앞의 셋은 같은 부류(정체)라 사례별 패치가 아니라 위의 정체 기록·비교 한 가지로 닫았습니다.

## 남은 것

- 정체를 안 알려 주는 이전 판 gateway는 그 설정 자신의 live 기록이 없으면 확인할 수 없어, 경고만 하고 받아들이되 기록하지 않습니다. 다시 시작하면 정체가 붙습니다.
- 같은 설정 파일을 고친 뒤 다시 시작하지 않은 프로세스가 돌고 있으면(정체의 지문이 바뀜) `up`이 `down` 뒤 `up`을 안내하며 막습니다. 포트만 바꾼 경우처럼 새 포트가 비어 있어도 같은 state 폴더를 옛 프로세스가 쥐고 있으면 막힙니다.
- `orchestrator.solo_agent: solo`는 PI 설정에 있지만 저장소 roster에 `solo` 직원이 없어 init 기본값에 넣지 않았습니다.
- `plan.declare_output_types`는 이슈가 켜라고 했지만 PI 시험 설정에는 없습니다(시험 근거 없음). 예시 설정의 `projects:` 자리표시자(`your-account/my-project`)도 PI 설정(`projects: []`)과 다르지만 범위 밖이라 두었습니다.
