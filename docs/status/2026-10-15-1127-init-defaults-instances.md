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
- 찾는 순서: `--bioinfo-agent PATH` → 기존 `BIOINFO_AGENT_DIR` → `D:/bioinfo-agent`·`C:/bioinfo-agent`·`~/bioinfo-agent`·`~/Desktop/bioinfo-agent` → WSL의 `/mnt/d/bioinfo-agent`·`/mnt/c/bioinfo-agent`(#612).
- 찾으면 `--yes`는 바로 연결하고 대화형은 확인합니다. 못 찾으면 대화형은 경로를 묻고 `--yes`는 묻지 않습니다.
- 끝내 없으면 설정에 넣지 않고 "bioinfo-agent 연결 안 됨 — …"과 고치는 법을 출력합니다. `doctor`의 `bioinfo-agent` 행이 같은 말로 warn을 냅니다(폴더에 plugin이 없어도 warn).
- `--bioinfo-agent`로 준 폴더에 plugin이 없으면 init이 멈추고 아무것도 쓰지 않습니다. 경로는 출력하지 않습니다.

**다른 인스턴스 경고** (`labhq/instance_guard.py`, `cli.py` up, `doctor.py` `instance` 행)

| 경우 | `up` | `doctor` |
|---|---|---|
| (a) 다른 설정의 살아 있는 프로세스 기록이 같은 state 폴더 | 멈춤. 그 설정의 `open`·`down` 명령을 보임 | warn |
| (a) 살아 있진 않지만 마지막으로 그 폴더를 쓴 설정이 다름(`up` 성공 때 적는 `labhq-last-config.json`) | 경고 | warn |
| (b) 다른 설정의 gateway가 이 포트에서 돎(기록 확인) | 멈춤. 어느 설정인지와 `labhq -c <그 설정> open` | warn |
| (b) 다른 token의 gateway가 답하는데 어느 설정인지 기록이 없음 | 기존 한 줄에 이어 찾는 법을 보임 | - |
| (c) 이 PC의 주 설정과 다름(처음 `up`이 성공한 설정, 기본 state 폴더의 `labhq-primary-config.json`) | 경고. 바꾸려면 `up --make-primary` | warn |

- 읽기만 하고 네트워크를 쓰지 않습니다(`doctor`는 HTTP를 하지 않아 포트의 모르는 프로세스는 못 봅니다).
- `--instance`로 만든 설정은 일부러 따로 쓰는 것이므로 주 설정이 되지 않고 비교하지도 않습니다. 설정 파일이 지워진 기록은 무시합니다. 러너만 있는 PC(gateway가 다른 PC)는 포트 비교를 건너뜁니다.
- 경고문은 doctor에서 홈 폴더를 `~`로 가리고, 고치는 명령에는 실제 경로를 씁니다.

**dev log** — `dev_log.repo`는 기본값 없이 init이 owner/name으로 한 번 묻습니다(Enter면 비움, 형식이 틀리면 비움, `--force`는 기존 값 유지). 비어 있으면 `doctor`의 `dev log` 행이 설정하는 법을 알려 줍니다.

**문서** — README 설치 절, `docs/manual.md`(init, doctor, 겹침 경고, bioinfo-agent 연결).

## 확인

- 새 test 54건: `tests/test_init_defaults.py` 34건, `tests/test_instance_guard.py` 20건. 임시 폴더와 가짜 process 기록만 쓰고 실제 프로세스·포트는 쓰지 않습니다. 옛 예시 설정으로 되돌리면 새 test 6건과 고친 semantics test 1건이 실패합니다.
- 기존 test 수정 3곳(약화 없음): `test_init_wizard.py`의 Windows `--yes` 구역 기대값(빈 목록 → 작업 폴더 internal 구역 하나)과 대화형 답 순서(dev log 질문 한 줄 추가), `test_semantics_shadow_settings.py`의 "예시 설정이 off" 확인(코드 기본 off 확인은 유지하고, 예시는 ab·confirm으로 확인). `tests/conftest.py`는 흔한 위치 탐지를 테스트마다 비웁니다(이 PC의 `D:/bioinfo-agent`에 test가 기대지 않게).
- 로컬 Windows pytest 전체: 82%까지 전부 통과, 나머지에서 59건 실패. 59건은 main에서도 똑같이 실패합니다(`tests/test_web_3d.py` 58건은 격리 HOME 아래에서 `node.exe`를 못 찾음, `test_staff_control_steps.py` 1건은 `tasklist` 출력이 None). node test 58개 파일은 `node --test`로 모두 통과.
- `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.

## 남은 것

- `doctor`는 HTTP를 하지 않아 기록에 없는 프로세스가 포트를 쓰는 경우는 `up`만 압니다(401로 확인).
- `orchestrator.solo_agent: solo`는 PI 설정에 있지만 저장소 roster에 `solo` 직원이 없어 init 기본값에 넣지 않았습니다.
- `plan.declare_output_types`는 이슈가 켜라고 했지만 PI 시험 설정에는 없습니다(시험 근거 없음). 예시 설정의 `projects:` 자리표시자(`your-account/my-project`)도 PI 설정(`projects: []`)과 다르지만 범위 밖이라 두었습니다.
