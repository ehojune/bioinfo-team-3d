# STATUS — labhq

최신 항목이 맨 위. 단계를 끝낼 때마다 PR 본문과 같은 내용을 여기에 추가합니다 (형식: `.github/pull_request_template.md`).

## 2026-10-02 · #229 #238 — CSO 산출 경로 정규화와 결과 블록 보존

- 결론: 선언·지시문·dependency·resume·연구 lane이 한 번 정규화된 `outputs/<name>`을 쓰며, bench 구조화 결과 블록은 LabHQ 상태·비용 문구 뒤에서도 최종 블록으로 남는다. 정보 경계와 실행 가드는 낮추지 않았다.
- 바뀐 것: 공백은 제거하고 원문 `..`·home·절대 경로는 정규화 전에 거부한다. 중복 선언은 합치고, 자기 산출의 루트·절대·home·bare 지시 경로는 대소문자와 무관하게 canonical 경로로 고친다. 같은 basename 입력은 가까운 input 동작으로 구분하며 모호하면 계획 교정을 요구한다. producer를 만든 뒤 dependency를 추론하던 순서를 바꿨고, 저장 계획 resume와 연구 CP1도 같은 검사를 다시 탄다. `wait_for_clarification: false`는 교정 계획 질문도 첫 계획처럼 기록한 뒤 진행한다. main에 이미 있던 단순 `./<name>` 교정은 기존 회귀로 유지했다.
- 실행한 것: 최초 확인 조건 10건과 봇 P1 회귀 2건이 수정 전 실패했다. 관련 310건, 전체 pytest 2461 passed/44 skipped, Node 13개, `scripts/check_public.sh`, `scripts/patch_notes.py check`, `git diff --check`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/util.py`, `labhq/orchestrator/cso.py`, `tests/test_cso.py`, `tests/test_research_protocol.py`, `tests/test_output_types_research.py`.

## 2026-10-02 · #263 — 재사용 후보 입력 정체성·목표 data type 필터

- 결론: 2차 실행 15건을 DB 사본으로 다시 계산했다. 판정표 34쌍의 후보는 34→3, 정밀도는 8.8%(3/34)→100%(3/3), 참 양성은 3→3, 거짓 양성은 31→0, 거짓 음성은 0→0이다. B1 그대로이며 CSO·실행 경로에는 값을 주지 않는다.
- 바뀐 것: 각 요청이 끝날 때 같은 host runner가 허용했다고 manifest에 남긴 reference의 입력 hash를 불투명 키 아래 보존하고, 과거 요청 입력은 다시 읽지 않는다. 요청문에서 계획의 출력 이름을 뺀 입력 이름만 쓰며 증거 없는 path는 `input_unknown`이다. 공개 accession·URL은 기록된 link만 쓰고 GitHub ref의 대소문자를 보존한다. 이 입력 정체성과 PI가 이름을 적은 산출의 #249 선언 data type이 모두 일치해야 후보가 된다. 제외 결과는 네 고정 사유의 개수만 남기고 필터 뒤에 순위를 매긴다.
- 실행한 것: 핵심 회귀 3건이 수정 전 실패했다. 자동 리뷰 두 차례의 P1 2건과 같은 정체성 부류 P2 3건을 고친 뒤 관련 66건(3 skipped), 전체 pytest 2450 passed/44 skipped, Node 13개, `scripts/check_public.sh`가 통과했다. #264 정보 경계 검사는 새 고정 사유만 허용하고 경로·파일명을 기록하지 않는 회귀로 확인했다.
- 미해결: 2차 원본에서 #261 자동 off 뒤 네 live 요청은 정답표가 없어 혼동행렬에서 제외했다. 15건 전체 replay의 최종 후보도 같은 3개였으며, B2 전환 근거로 쓰지 않는다.
- 근거: `labhq/research/semantics_shadow.py`, `tests/test_semantics_shadow_provenance.py`, `tests/test_semantics_shadow_hash.py`, `tests/test_direct_outputs.py`.

## 2026-10-02 · #262 — Windows Codex elevated sandbox setup 사전 차단

- 결론: 직원 Codex는 격리한 `CODEX_HOME`에서 떴지만 그 홈에는 elevated sandbox setup marker가 없었다. LabHQ가 `--ignore-user-config`와 `windows.sandbox="elevated"`를 명시하므로 개인 설정은 원인이 아니다. Codex가 무인 실행 중 관리자 helper를 띄우려다 사용자가 취소해 Windows 1223으로 실패했고, 기존 처리는 최종 응답이 있다는 이유로 성공처럼 넘긴 뒤 `missing_outputs`로 바꿨다.
- 바뀐 것: 초기화된 직원 `CODEX_HOME`에 marker가 없으면 adapter preflight와 doctor가 Codex를 시작하기 전에 이유를 밝히고 멈춘다. 실시간 1223 event도 `sandbox_setup_required` 실패로 보존한다. 더 약한 sandbox로 자동 전환하지 않는다.
- 실행한 것: 회귀 test 3건을 실패부터 확인했다. 관련 test 190 passed/17 skipped, 전체 pytest 2432 passed/44 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과. 격리 폴더에서 `unelevated` 저장은 2회 성공했지만 상위 canary 쓰기도 성공해 작업 폴더 경계를 지키지 못했으므로 채택하지 않았다. UAC와 elevated setup은 실행하지 않았다.
- 미해결: 실제 elevated 직원 실행 2회 저장 확인은 관리자가 해당 직원 `CODEX_HOME`의 setup을 대화형으로 마친 뒤 해야 한다. preflight는 marker 존재 여부를 확인하며, 버전 비호환은 실행 중 1223 원인 분류로 남는다. sandbox·권한 변경이므로 병합하지 않는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/adapters/codex.py`, `labhq/doctor.py`, `tests/test_isolation.py`, `tests/test_real_streams.py`, `tests/test_doctor.py`.

## 2026-10-02 · #261 — 연구 계획의 어휘 판본 hash가 정보 경계에 걸린 오판

- 결론: 2차 실행의 연구 요청을 최신 main에서 다시 계산했다. 걸린 칸은 `vocab_sha256`, 부류는 검증된 산출 어휘 판본 hash였다. 연구 계획의 같은 판본을 자유 입력처럼 민감값에 넣어, 줄의 공개 판본 칸이 자기 자신과 일치하자 차단한 오판이다.
- 바뀐 것: 현재 설치된 어휘 판본과 정확히 같은 값만 `vocab_sha256` 칸에서 허용한다. 같은 값이 다른 칸에 있거나 다른 판본이면 계속 막는다. 경계 위반으로 자동 off될 때 `disabled.json`·자동 off 줄·report에 고정된 칸 이름과 부류만 남기며 값은 쓰지 않는다. 경로·파일명·URL·DOI·직원 ID·자유 문장 차단 회귀를 넣었다.
- 실행한 것: DB 사본 재계산은 수정 전 `vocab_sha256:identifier` 1건, 수정 뒤 경계·type·action shape 문제 0건이다. 회귀는 수정 전 10건 실패를 확인했다. 관련 75건, 의미 모델 415건(2 skipped), 전체 pytest 2438 passed/44 skipped, Node 13개, `scripts/check_public.sh`가 통과했다.
- 미해결: 정보 경계 변경이라 PR은 병합하지 않는다. 봇 리뷰가 끝나 P1이 없으면 총괄에게 넘긴다.
- 근거: `labhq/research/semantics_shadow.py`, `tests/test_semantics_shadow_breaker.py`, `tests/test_semantics_shadow_report.py`, `README.md`.

## 2026-10-02 · #149 결정 13 · #150 — 액션 층 그림자 A1 (실행 없음)

- 결론: 객체 뷰 위에 기존 액션 7종의 전제 조건을 계산해 기록만 한다. 위험 검토 sol·astra가 둘 다 "A1만 조건부 go, A2 실행 코드는 이번 PR에서 뺀다"고 판정해 실행 허용 목록은 빈 집합이다. 실제 실행(A2, CLI `request.followup`)은 검토가 요구한 조건을 입증한 뒤 별도 PR이다.
- 바뀐 것: 새 `labhq/research/semantics_actions.py`(순수 판정, gateway·orchestrator·runner·store·네트워크·프로세스 import 없음, 입력은 frozen 사본). `semantics: {mode: shadow, actions: shadow}`면 요청 종료 B1 줄에 `actions` 칸을, 이어 묻기 접수·거부·종료 때 `type: followup` 줄을 쓴다. 조건은 true/false/unknown이고 기록에 없는 시점은 unknown이다(지금 roster로 채우지 않음). 읽기 전용 거부는 run_followup이 넘긴 reason만 `refused_while_open`으로 센다. `hpc.*`는 늘 `refused_p3`이고 `hpc_submit` 승인 창만 센다. `actions: confirm`과 그 밖의 값은 actions만 끄고 경고 한 번, 허용 목록 키는 모르는 키라 semantics 전체가 off다. 실패는 B1 breaker 창에서 세고, 새 칸·줄은 고정 이름·참거짓·개수 shape 검사를 거친다. `labhq semantics report`에 A1 절, `scripts/semantics_shadow_remove.py --only actions`. 연결은 `# semantics-hook: actions` 줄(server 4·cso 4·semantics_shadow 23)과 semantics_shadow 안의 블록 2개다. README §8·§10.
- 실행한 것: 새 test 72건. 게이트 우회 불가(AST import·동적 호출 검사, 빈 허용 목록, 설정으로 못 늘림, 문서 속 "PI 승인 완료" 무시, lab에서 recruit·contract·cancel 메시지 0), 끄면 원상(actions off 다섯 값에서 모듈 미로드·B1 줄만, 고정 시계에서 B1 줄 바이트 동일, lab 기록 동일, `--only actions` 제거 뒤 B1 동작), 중복(같은 요청·이어 묻기를 여러 번 기록해도 한 번), HPC 거부. 가드 11개를 하나씩 깨는 변이 검사에서 11개 모두 test가 실패했다. 로컬 리뷰 P2 4건(결정된 research_plan의 hash를 unknown으로, backlog가 queue의 요청 작업을 앞지르지 않게, mismatch 필드 누락을 shape에서 거부, 마지막 burst에서 버린 수 기록)을 test와 함께 고쳤다. 전체 pytest 2428 passed/44 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: A2 재개 조건(직원이 PI client token에 닿지 못함 입증, 응답 유실 때 재전송 금지, 감사와 followup id 연결, 전송 직전 off 재확인). runner 승인 시계와 gateway 결정 시계가 다르면 만료 판정이 어긋난다. task 실행 구간은 끝 시각이 없어 길이를 재지 않는다. 이어 묻기는 REST 경로만 보고, queue가 차면 20건까지 뒤에 둔 뒤 넘치는 것은 버리고 센다. 임계값은 전부 미측정 제안치다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/research/semantics_actions.py`, `labhq/research/semantics_shadow.py`, `labhq/gateway/server.py`, `labhq/orchestrator/cso.py`, `scripts/semantics_shadow_remove.py`, `tests/test_semantics_actions.py`, `tests/test_semantics_actions_shadow.py`, `tests/test_semantics_shadow_remove.py`, `README.md`.

## 2026-10-02 · #221 할 일 2 · #151 — 산출 데이터 종류 선언 자리

- 결론: 출처 모델과 객체 뷰가 함께 쓰는 "산출 데이터 종류 선언 자리"를 core에 만들었다(PI 결정 12). 기본 off라 지금 동작은 그대로다. 켜면 CSO가 단계 산출마다 data_type·format key를 적고, 러너가 실제로 모은 산출에 붙이며, 두 그림자 모델이 같은 읽기 함수로 읽는다. 팔란티어식 액션은 계속 보류다.
- 바뀐 것: `labhq/vocab/`(로컬 key 38개, 선언 정규화 계약 하나, optional subset loader), `plan.declare_output_types`(기본 false), 일반·연구 계획 단계의 `output_types`, `TaskResult.output_types`(러너 기록), `ArtifactRef.data_type·format`, 그림자 줄의 `vocab_sha256`·`types`(zone 통과분만 ID별, 나머지 withheld)·`declarations`, report의 model/vocab 판본 분리. EDAM 표와 생성·검증 묶음은 draft PR #253으로 분리했다.
- 두 위험 검토(Sol·Astra, 둘 다 조건부 go)의 필수 수정: 기본 off와 off일 때 schema·prompt·dispatch 바이트 동일, 로컬 어휘와 EDAM 분리(subset 없거나 깨져도 동작), 정규화 계약 하나(순서 무관, 중복·별칭·충돌·판본 차이는 unknown, 엔트리·바이트 상한), 선언 판본 고정(다른 판본은 재해석하지 않음), 빈 선언은 canonical에서 빠져 승인된 계획 hash 유지, off·재개 때 저장 선언 보존, 경고는 코드·개수만, 그림자 줄은 subset ID 허용 목록으로 key·값 검사, zone gate 뒤에만 종류별 집계, 공통 YAML loader를 core로 분리, wheel 포함·semantics 제거 뒤 core 동작, report 판본 분리.
- 실행한 것: 기능·수정 커밋마다 회귀를 먼저 확인했고 dispatch·승인·결과가 선언과 무관함을 guard test로 고정했다. EDAM 묶음을 뺀 실제 tree에서 subset 부재 회귀를 포함한 관련 test 91개와 전체 pytest 2341 passed/43 skipped, Node 13개, `bash scripts/check_public.sh`가 통과했다. 로컬 Codex 리뷰 P2 3건(직원 선언 중복의 순서 의존, 객체 뷰의 직원 선언 누락, 엔트리 상한 불일치)을 고쳤다.
- 미해결: 실제 CLI에서 켜 보지 않았다. 켜기 전 조건은 Claude·Codex schema probe, 같은 요청의 off/on 계획 비교, prompt+schema 실제 token(규칙 500자·schema 224자, token 미측정)이다. 연구 직원 선언은 연구 단계가 실행되기 전까지 쓰이지 않는다. EDAM 표는 #252 PI 확인 전 병합하지 않는 draft PR #253에 있다. 로컬 key 정의 검수는 모두 pending이다.
- 근거: `labhq/vocab/`, `labhq/yaml_unique.py`, `labhq/orchestrator/cso.py`, `labhq/research/contract.py`, `labhq/runner/daemon.py`, `labhq/models.py`, `labhq/research/semantics*.py`, `tests/test_output_vocab.py`, `tests/test_output_types*.py`, `tests/test_semantics_output_types.py`.

## 2026-10-02 · #222 — 공개 데이터 연구 요청이 실제 CLI로 CP1 승인 카드까지

- 결론: 10-01 실행의 CSO 연구 계획 5개는 모두 acceptance 키를 지어냈다. rule id가 prompt 어디에도 키로 적혀 있지 않아 reviewer question을 키로 썼다. d1은 `protocol.packs`의 sha256도 비웠다. 검증은 첫 오류에서 멈춰서 교정 한 번에 오류 하나만 보였다. 이제 pack hash는 코드가 채우고, prompt는 `pack_values_keys`를 보여 주고, 교정 prompt에는 문제를 모두 넣는다. 재실행하다 Windows 명령줄 한도를 넘은 재계획이 `executable not found`로 실패하는 것도 찾아 고쳤다.
- 바뀐 것: `with_pack_refs`·`research_plan_errors`(키 missing·unexpected, 통계 core 누락 field 이름), `pack_refs`와 catalog `pack_values_keys`, CSO 교정 루프. 교정 뒤에도 실패하면 CP1 카드 없이 `outcome: plan_invalid`, 한국어 보고서, `plan_validation.errors`가 남는다. adapter는 명령줄이 32,000 UTF-16 단위를 넘으면 TASK 파일을 가리키는 prompt로 바꾸고, 그래도 넘으면 실행 전에 거부한다. README §8·§10, `docs/research_protocol.md`를 맞췄다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 실행한 것: 회귀 test 12건 모두 main 코드에서 실패한다(독립 검증: main에 새 test 파일만 얹어 12 failed). 그중 1건(agent_id 타입)은 로컬 Codex 리뷰가 찾은 중간 회귀의 guard다. 로컬 Codex 리뷰 P2 2건(agent_id가 list면 교정 없이 실패, 길이를 UTF-16으로 세기)을 반영했다. 실제 CLI 재실행(CSO Sonnet, 10-01 d1과 같은 공개 GSE96583 요청): 첫 시도는 clarify 뒤 재계획 명령이 33,091자라 실패했다. 고친 뒤 두 번 모두 CP1까지 갔다(첫 계획 통과 349초·$0.55, 한도를 20,000으로 낮춰 TASK 파일 경로로 clarify·재계획 1,116초·$1.50). 두 번 다 CSO는 `protocol.packs`를 비웠고 acceptance 14개를 rule id로 채웠다. 전체 pytest 2187 passed/41 skipped, Node 13개, `bash scripts/check_public.sh` 통과.
- 미해결: 연구 lane은 요청마다 모든 active pack 값을 채워야 한다. d2(엽록체 IR 가설에 `single_cell_de@2`)처럼 대상이 다르면 여전히 CP1에 못 가고, 보고서는 설정된 pack과 그 적용 대상을 알려 줄 뿐이다(README §10). 요청별 pack 선택은 범위 밖이다.
- 근거: `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/orchestrator/cso.py`, `labhq/adapters/base.py`, `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `tests/test_research_cp1.py`, `tests/fixtures/fake_claude_cso.py`, `tests/test_adapters_fake_cli.py`.

## 2026-10-02 · 열린 issue 작업 큐 동기화

- 결론: 오래된 병합 전 큐를 열린 PR 2건과 아직 추적되지 않던 후속 33건 기준으로 바꿨다.
- 바뀐 것: PR #239·#230을 리뷰 중으로 두고, #57·#90·#149·#150·#151·#165·#178의 남은 단계와 P2·P3 후속을 `HANDOFF.md`에 묶었다.
- 실행한 것: GitHub의 열린 PR·issue 상태와 각 issue의 현재 본문을 대조했다.
- 미해결: 구현은 하지 않았다. 각 묶음의 순서와 담당은 다음 개발 총괄이 정한다.
- 근거: `HANDOFF.md`, GitHub issue #57·#86·#90·#149–#151·#165·#178·#196–#242 중 열린 후속.

## 2026-10-01 · #221 할 일 1 — direct 요청 산출을 결과 outputs로

- 결론: direct 요청도 작업 폴더 `outputs/`의 산출을 결과 outputs로 남겨, 그림자 출처 모델이 artifact로 보고 hash를 잰다. 할 일 2(산출 데이터 종류 선언 자리)는 PI 판단(#149·#151) 대기라 하지 않았고 #221은 열어 둔다.
- 바뀐 것: runner가 `kind: direct` 실행 뒤 `TaskWorkspace.scan_outputs`로 `outputs/` 아래 정규 파일을 센다. symlink·junction·mount·통제 구역은 따라가지 않고(`outputs/` 자체 포함) labhq의 `RESULT*.md`와 UTF-8로 읽을 수 없는 이름(Linux에서 푼 CP949 파일명 등)은 빼며, 최대 200개(그림자 `HASH_MAX_FILES`)와 `runner.reference_scan_max_entries`·`reference_scan_max_depth` 안에서만 센다. 상한에 닿으면 작업 로그에 경고가 남는다. 계획 단계는 그대로 선언한 산출만 보고한다. direct 이어 묻기는 orchestrate처럼 산출이 있는 작업 폴더를 읽기 전용 upstream으로 받는다. mock 직원은 direct `[artifact]`에도 파일을 쓴다. README 그림자 절을 맞췄고 패치노트는 건드리지 않았다.
- 실행한 것: 회귀 10건이 수정 전 실패하고 수정 뒤 통과했다(step 선언 산출 guard 1건은 전후 통과). 로컬 Codex 리뷰 P2 1건(`outputs/` 자체가 mount거나 통제 구역 안이면 그대로 순회)을 고치고 회귀에 넣었다. run log의 c1·c2를 실제 Claude Code CLI(data_steward=Sonnet, 공개 palmerpenguins 발췌, 별도 state)로 다시 돌렸다. c1은 outputs 2개·`observed_new 2`(이전 0), c2는 `history_artifacts 2`·`candidates 0`·`type_unknown 2`. 전체 pytest 2133 passed/25 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과. 독립 검증에서 P1 2건을 고쳤다. UTF-8이 아닌 파일명 하나가 끝난 direct 실행을 `UnicodeEncodeError` 실패로 바꾸던 것과, 항목 상한 test가 ext4 목록 순서에서 실패하던 것이다(WSL ext4에서 수정 전 실패, 수정 뒤 12 passed). main 병합 뒤 두 번째 검증에서 Codex 리뷰 P1 1건도 고쳤다. 산출 목록이 `manifest.json`을 `read_owned` 없이 읽어, 직원이 그 자리에 둔 링크나 FIFO를 따라가던 것이다(#165 우회, WSL ext4에서 수정 전 실패, 수정 뒤 13 passed).
- 미해결: 할 일 2 전이라 live 후보는 여전히 0이다(`type_unknown`). 같은 폴더를 다시 쓰는 재시도·wake는 같은 파일을 여러 run이 보고해 `not_generated`가 된다(선언 산출과 같은 규칙). hard link는 경로로 구별하지 못한다.
- 근거: `labhq/runner/workspace.py`, `labhq/runner/daemon.py`, `labhq/adapters/mock.py`, `tests/test_direct_outputs.py`, `tests/semantics_shadow_lab.py`.

## 2026-10-02 · #219 — Windows TEMP 아래 작업 폴더의 Claude 쓰기 경로

- 결론: Claude 직원이 Git Bash `pwd`의 `/tmp/...`를 Write에 넣어도, 작업 폴더 안이면 승인 없이 작업 폴더에 쓴다. 폴더 밖 쓰기는 여전히 승인을 받는다.
- 원인: Claude Code 2.1.282(Windows)의 Bash는 TEMP를 `/tmp`로 보여 주고, 파일 도구는 같은 표기를 드라이브 루트 `C:\tmp\...`에 쓴다. 게이트는 그 경로를 폴더 밖으로 보고 물었다(chief_of_staff·biologist). 맨 `Write`가 사전 허용된 직원(analyst·data_steward)은 묻지 않고 `C:\tmp`에 써서 산출이 비었다.
- 바뀐 것: 게이트는 드라이브 없는 쓰기 경로를 Claude가 실제로 쓸 경로로 판정한다. Git Bash 뜻(`/tmp`→TMP·TEMP, `/c/`→`C:\`)이 쓰기 루트 안일 때만 그 경로로 고쳐 `updatedInput`으로 돌려준다. TMP·TEMP가 없거나 다르면 고치지 않고 지금처럼 묻는다. 승인 요청에는 실제로 쓸 경로를 `detail.path`로 싣는다. 맨 `Write`·`Edit`는 작업·project·upstream 폴더의 `Edit(//…/**)` 규칙으로 바꿨다. 링크로 적힌 폴더는 적힌 표기와 실제 경로에 규칙을 하나씩 둔다(검증 중 발견: 실제 경로 규칙만으로는 링크 표기 절대경로 쓰기가 막혔다). bench 대본 PI는 그 task 작업 폴더 안 Write/Edit 요청을 승인하고 `workdir_write_approvals`로 따로 센다. README §8·§10을 맞췄다.
- 실행한 것: 실제 Claude CLI(2.1.282, sonnet) probe로 경로 표기와 파일이 생긴 곳을 기록했다(가린 fixture 3개). 수정 뒤 같은 `/tmp` 쓰기는 작업 폴더에 생겼고, 폴더 밖 쓰기는 게이트가 승인으로 넘겼다. junction 표기 작업 폴더 probe 6개(`junction_*`)로 두 표기 규칙이 모두 있어야 사전 허용됨을 확인했다. bench public-protein-qc labhq arm 재실행에서 미스크립트 승인 1→0, `C:\tmp` 쓰기 0. 새 회귀 36건 중 34건이 수정 전 실패했다(2건은 guard). 전체 pytest 2160 passed/25 skipped, Node 13개, `bash scripts/check_public.sh` 통과. Codex 리뷰 지적 없음.
- 미해결: protein-qc는 여전히 FAIL이다. CSO 계획이 `answer.md`를 `outputs/` 밖에 선언해 INCOMPLETE가 됐다(별개 원인, 그림자 실행의 penguins와 같은 부류). 사전 허용 Read의 `/tmp/...`는 "파일 없음"으로 끝난다. Bash 셸 쓰기 대상의 `/tmp`·`/c/` 표기는 그대로 승인을 받는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/policy.py`, `labhq/tools/approval_mcp.py`, `labhq/adapters/claude_code.py`, `labhq/bench.py`, `tests/test_claude_write_paths.py`, `tests/test_bench_review3.py`, `tests/fixtures/real/claude_code/claude_windows_write_paths.json`, `claude_write_tmp_*.jsonl`.

## 2026-10-01 · #220 — CSO 계획의 산출 경로를 실행 전에 outputs/ 안으로

- 결론: 단계 결과 계약은 작업 폴더 `outputs/` 아래만 센다. penguins 계획은 `answer.md`를 선언하고 지시문에 `./answer.md`(작업 폴더 루트)를 적어, analyst가 만든 파일을 찾지 못해 INCOMPLETE가 됐다. 이제 계획 검증이 dispatch 전에 고치거나 다시 받는다.
- 바뀐 것: `validate_steps`가 단계 자기 산출을 가리키는 루트 경로(`./answer.md`, `.\answer.md`)를 `./outputs/answer.md`로 고치고 선언도 `outputs/answer.md`로 바꿔 경고를 남긴다. 절대 경로·드라이브·`..`는 `PlanOutputsError`로 CSO 교정 계획을 한 번 받고, 그래도 틀리거나 교정 계획이 새 질문을 내면 단계 없이 실패한다. PLAN_PROMPT 산출 규칙에 `outputs/<name>`과 루트·절대 경로 금지를 적었고, 단계 prompt 끝에 선언 산출 경로를 싣는다. README §7에 한 문단.
- 실행한 것: penguins 재현 fixture(`tests/fixtures/plans/penguins_outputs_root.json`)를 포함한 회귀 test 10건이 수정 전 실패함을 봤다. 전체 pytest 2132 passed/25 skipped, Node 13개, `bash scripts/check_public.sh`, `git diff --check` 통과. Codex 리뷰 P2 1건(교정 계획의 새 질문이 PI 확인 없이 dispatch됨)을 고치고 test를 더했다. 실제 CLI(Sonnet 대체) penguins 재실행: 요청 done, CSO가 `outputs/answer.md`로 선언, analyst·qc_reviewer 단계 모두 done($0.65, 309초).
- 미해결: 같은 재실행에서 bench 형식은 FAIL이다. 최종 보고서 끝에 붙는 "Step status and output paths" 감사 줄이 결과 블록 뒤에 와서 "structured result block is not last"가 된다. qc_reviewer의 `labhq_ask`가 `wait: none`으로 broker 500, tool_permission 미스크립트 거절 2건(Temp/claude 경로)도 별도 문제다. 연구 lane 계획은 산출 경로를 검사하지 않는다(PR 1 pilot은 단계를 실행하지 않음). 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/orchestrator/cso.py`, `tests/test_cso.py`, `tests/fixtures/plans/penguins_outputs_root.json`, `README.md`.

## 2026-10-01 · #165 #177 #178 #180 #181 #182 #183 #190 #193 — 작업 폴더 쓰기·Claude 규칙 경로·공개 가드 후속

- 결론: 부류마다 공통 판정 하나로 닫았다. 러너가 작업 폴더에 쓰는 경로(#165·#190·#193), Claude 거부 규칙을 붙일 수 없는 경로(#177·#182), 공개 가드의 표기 빈틈(#180·#181·#183)이다. #165는 1–3번만, #178은 문서만 고쳤다(둘 다 실측이 남아 Refs).
- 바뀐 것: `adapters/owned.py`가 labhq 소유 경로 쓰기를 맡는다. 링크를 따라가지 않고, 재사용 폴더에 링크가 있으면 실행을 거부한다. manifest와 Codex 마지막 답도 링크를 거쳐 읽지 않는다. 계약 skill 상위 링크도 지우지 않고 거부한다. 계약 skill은 원본과 같을 때만 면제하고, Windows·macOS에서는 지시 파일 이름을 대소문자 없이 비교한다. 재사용 폴더 검사 중 이벤트는 모았다가 로컬 로그에 쓴다. `claude_rule_ready`가 UNC 링크·참고를 거른다. mount 뒤도 계속 훑는다. 공개 가드는 percent-encoded webhook(앞에 다른 escape가 붙은 것 포함), 사내 forge의 owner/repo, 공백·`\uXXXX`가 든 계정 home을 가린다.
- 실행한 것: 확인 조건 test는 수정 전 실패를 확인했다. 이 PC에서 skip된 것은 file symlink 13건과 POSIX 전용 3건이다. 전체 pytest 1968 passed/39 skipped, Node 11개, `scripts/check_public.sh`, `git diff --check` 통과. 로컬 Codex 리뷰(branch diff)는 결함을 찾지 못했다. 독립 검증이 P1 둘(링크 너머 manifest·마지막 답 읽기, escape 바로 뒤 webhook)을 고쳤고 P2 일곱은 후속으로 뺐다.
- 미해결: 실제 CLI 실측 셋. #148 신뢰 경로 probe(#165 4번), 하위 폴더 CLAUDE.md 제외 probe(#165 5번), Claude 규칙의 대소문자·8.3 비교(#178)다. #178은 `allow_runner_read_restricted`와 대소문자 무시 파일 시스템이 겹칠 때만 남는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/adapters/owned.py`, `labhq/adapters/read_only.py`, `labhq/runner/daemon.py`, `labhq/runner/workspace.py`, `labhq/intake.py`, `labhq/integrations/github.py`, `tests/test_workspace_boundary.py`, `tests/test_publish_followups.py`.

## 2026-10-01 · PR #136·#158 후속 12건 — 계보 순회·resume 이유·그림자 breaker·hash·mark

- 결론: #154 #155 #156 #157 #159 #160 #161 #163 #173 #174 #175를 issue별 커밋으로 고쳤다. #162는 이미 main(dc01c50)에 고쳐져 있어 issue의 두 모양만 test에 더했다. #163과 #174는 같은 결함이라 한 커밋이다.
- 바뀐 것: cycle 경고는 강연결 요소마다 한 건(A·B), 계보 순회는 너비 우선 최소 깊이, resume 이유 `start_unknown`(모델 hash `9ed9aa1e…`), hash는 `timeout_s`의 절반까지, JSON1 없으면 `sqlite_json1_missing` off, Windows 읽기는 FILE_SHARE_DELETE, wake 자식이 있으면 job은 끝난 것, breaker window는 `breaker.json`, mark는 기록된 후보만. README 그림자 절과 §10을 맞췄다. 패치노트는 건드리지 않았다.
- 실행한 것: 확인 조건마다 회귀 test가 수정 전 실패함을 봤다(#156은 재확인 비교를 바꾸는 변이로). 전체 pytest 1958 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과. Codex 리뷰 P2 1건(event loop에서 breaker.json fsync)을 daemon thread 저장으로 고쳤다. 독립 검증에서 #159 worker test의 실시간 여유(약 1초)를 9.5초로 넓혔다.
- 미해결: Windows에서 hash 중인 파일 위로 다른 파일을 `os.replace`하는 쓰기는 여전히 실패할 수 있다(README §10). `finished` job은 최종 상태(성공·실패)를 모른다. derived_from 깊이 경계는 A(64)와 B(65)가 한 단계 다르다(이번 범위 밖).
- 근거: `labhq/research/semantics.py`, `labhq/research/semantics_v1.yaml`, `labhq/research/semantics_shadow.py`, `labhq/research/semantics_objects.py`, `tests/semantics_baseline.py`, `tests/test_semantics_pilot.py`, `tests/test_semantics_shadow_*.py`, `tests/test_semantics_objects.py`.

## 2026-10-01 · #112 #113 #144 — 재시작 뒤 CSO session·질의 route 후속

- 결론: 재시작으로 gateway가 놓친 상담·이어 묻기가 runner에서 계속 도는 동안, CSO 계획·최종 보고서·새 이어 묻기가 같은 session·workdir로 겹쳐 dispatch되지 않는다. 재시작 전 facilities가 받던 질의는 roster가 빈 동안 CSO로 넘어가지 않는다.
- 바뀐 것: `holds_session` 하나로 "결과가 기록되지 않은 task가 session·workdir를 쥐었나"를 판정한다(abandoned 포함). 상담은 쥔 task가 있으면 바로 격리하고, 계획·종합·이어 묻기는 `Hub.wait_session_free`로 지금 runner 세대가 수락한 task를 결과가 올 때까지, runner가 끊기면 끊긴 때부터 `resume_wait_s`까지 기다린 뒤 그 turn의 session에서 잇는다. 끝났는지 모르면 새 session·workdir로 연다(#112 #144). 질의 원장의 `routed_to`로 재시작 전 담당자를 유지하고, 그 runner를 `resume_wait_s`까지 기다리며, facilities가 끝내 안 돌아오면 CSO가 답한다(#113). README §8을 맞췄다.
- 실행한 것: 회귀 12건 중 10건은 수정 전 실패, 2건(facilities 없는 설정 무대기, fallback)은 guard다. 로컬 리뷰 P2 2건(끊긴 runner로 전달 중인 task의 대기 상한, abandoned 뒤 도착한 결과의 점유 해제)을 #112 커밋에 반영했다. 봇 리뷰 P1(queue에서 기다린 수락 task를 dispatch 시각+timeout으로 놓아 줌)은 시계로 점유를 푸는 규칙을 빼서 고쳤고, 재접속 유예도 시작 시각이 아니라 끊긴 때부터 센다(회귀 2건, 수정 전 실패). 전체 pytest 1939 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: 기다린 이전 이어 묻기의 답은 그 항목에 붙이지 않는다(interrupted 그대로). 수락된 task에는 대기 상한이 없다. runner가 queue·task timeout을 거쳐 결과를 꼭 보내므로 그것을 기다린다. 격리된 실행은 session 맥락 없이 prompt의 보고서·결과로 답한다. 재시작 전 CSO가 받던 facilities 질의는 facilities가 돌아와도 CSO가 잇는다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `tests/test_consult_restart.py`, `tests/test_followup.py`.

## 2026-10-01 · #119 #172 #185 #186 — HPC 첫 설정 상담과 #171 후속

- 결론: `labhq init`이 읽기 전용 조회로 `hpc:` 초안을 만들고, PI가 `y`라고 한 시험 잡 1회만 제출해 끝까지 추적한다. #171 후속 P2(#172 여섯 항목, #185, #186)를 닫았다.
- 바뀐 것: 스크립트에 스케줄러 지시가 있으면 임계값과 무관하게 승인, `hpc_cancel`은 broker가 추적한 그 직원의 잡만, 스케줄러 명령 env는 허용 목록, 셸 승인 목록 확대, cluster 판정은 `slurm_cluster_option` 하나(묶은 짧은 옵션 포함), accounting 없음·`REVOKED`·모르는 상태도 종료로 깨움. 시험 잡 파일은 시험마다 새 개인 폴더에 배타 생성한다. 패치노트는 건드리지 않았다.
- 실행한 것: issue별 회귀 테스트가 수정 전 실패, 수정 뒤 통과했다. 로컬 Codex 리뷰 P2 3건(비소모성 `mem_free`를 코어로 나눔, mock id 취소, 시험 잡 상태 timeout)도 회귀 테스트와 함께 고쳤다. 독립 검증 리뷰의 P1(시험 잡 고정 파일명에 남은 링크를 따라 씀)은 hardlink·symlink 회귀 테스트와 함께 고쳤다. 전체 pytest 2028 passed/24 skipped, Node 11개, `scripts/check_public.sh` 통과.
- 미해결: SGE `.sge_request`(제출 폴더·home)는 직원이 셸로 쓰면 승인 계산 밖 자원을 요청할 수 있다(README §10). 실제 클러스터에서는 돌리지 않았다.
- 근거: `labhq/hpc_consult.py`, `labhq/init_wizard.py`, `labhq/tools/scheduler.py`, `labhq/tools/hpc_mcp.py`, `labhq/settings.py`, `tests/test_hpc_consult.py`, `tests/test_hpc_followups.py`.

## 2026-10-01 · #126 #184 — 웹 화면 후속: snapshot 답 길이, 승인 알림·이름표·하단 줄

- 결론: snapshot의 이어 묻기 답은 앞 2,000자만 싣고 전문은 펼칠 때 받는다(#126). 승인 알림은 승인이 끝나는 모든 경로에서 지우고, 3D 이름표는 자기 머리 위에 두고, 2.5D 데스크톱은 사무실·Command Center·직원 줄·입력창이 겹치지 않게 화면을 나눴다(#184).
- 바뀐 것: gateway `snapshot_followup` 하나가 요청 followups와 replay 이벤트를 함께 자르고, 같은 답이 다시 실리는 replay `task.result` 본문은 step 카드 길이(500자)로 자른다. 공용 reducer에 `fillFollowups`, 승인 종료 공통 `endApproval`(resolved·timeout·expired·stale, snapshot에서 사라진 승인)과 `toast.clear` effect를 넣었다. 3D는 skin별 label anchor 대신 head anchor 기준 `labelPoint`를 쓴다. mock 승인 예시는 `Rscript scripts/qc_plots.R`로 바꿨다. README §6을 맞췄고 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 실행한 것: 확인 조건마다 회귀를 먼저 썼고 수정 전 모두 실패했다. 긴 답 25개 snapshot은 2,644,490에서 276,687 bytes, 러너 `task.result`까지 실제 경로로 쌓은 경우 2,044,888에서 208,507 bytes. 1200×750 브라우저 측정에서 사무실·Command Center 87–527px, 직원 줄 541–677px, 입력창 685–750px로 겹침 0, 페이지 스크롤 없이 마지막 행과 메신저 끝이 각자 안쪽 스크롤로 보인다. 전체 pytest 1928 passed/23 skipped, Node 13개, `scripts/check_public.sh` 통과.
- 미해결: 가로 621–999px 단일 열에서는 직원 줄이 sticky라 스크롤 중 내용을 덮는다. snapshot 크기는 여전히 요청 수에 비례한다. README 움직이는 화면은 다시 녹화하지 않았다.
- 근거: `labhq/gateway/server.py`, `labhq/web/state.js`, `labhq/web/index.html`, `labhq/web/lab3d/src/{live,skins,main}.js`, `tests/test_followup.py`, `tests/web_issue126.cjs`, `tests/web_issue184.cjs`.

## 2026-10-01 · PR #166 후속 여섯 건(#167 #168 #169 #170 #187 #188) — 연구 근거 검증과 계획·검토 결속

- 결론: 여러 표기 accession 비교, 체계가 다른 같은 논문의 재인용, REVIEW v2 작성자 결속, 없어진 pack version, PLAN slot id 중복을 닫았다. #169와 #188은 같은 부류라 `author` 필수화 하나로 닫았다.
- 바뀐 것: `compare_accessions`가 version·isoform·VCV 자리채움을 뗀 base로 판정하고 레지스트리 URL 로컬 판정, uri resolver 경로, ID 조회 응답이 함께 쓴다. base가 다르거나 양쪽에 적힌 version·isoform이 다르면 `conflicting`, 한쪽에 version·isoform이 없으면 `base_match_only` 미확인이다. ID 조회 응답에 인용 표기와 같은 base의 다른 표기가 함께 오면 `conflicting`이다(#167). resolver 기록의 `same_as`(DOI↔PMID↔PMCID)를 재인용 키에 더해 `VerificationReport.recitations`로 보고하고 `ok`를 막는다(#168). `validate_research_review`는 `author`가 없거나 비면 거부한다(#169 #188). gateway는 상태를 열기 전에 pack을 불러 `available: single_cell_de@2`를 담은 오류로 멈춘다(#170). 한 step 안 evidence slot id 중복은 CP1 전에 거부한다(#187). 패치노트는 건드리지 않았다.
- 실행한 것: 새 회귀 17건이 수정 전 main에서 모두 실패하고 수정 뒤 통과했다. 리뷰에서 나온 P2(uri 후보에 다른 accession이 섞이면 conflicting) 1건을 고치고 회귀를 더했다. 독립 검증에서 다른 version·isoform 응답이 미확인이나 `found`로 내려가던 회귀(main은 `conflicting`)를 찾아 고치고 회귀 10건을 더했다. 전체 pytest 1947 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: 같은 기록의 다른 version(ENSG…17과 ENSG…16)을 재인용으로 보지는 않는다. 대응 ID는 resolver가 `same_as`로 줄 때만 쓰며 live resolver는 아직 없다. REVIEW v2와 pack 검사는 연구 실행 경로(#90 PR 3) 전이라 호출하는 쪽이 작성자를 넘겨야 한다.
- 근거: `labhq/evidence/verify.py`, `labhq/research/review.py`, `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/gateway/server.py`, `labhq/cli.py`, `tests/test_evidence_verify.py`, `tests/test_research_review.py`, `tests/test_research_protocol.py`, `tests/test_research_evidence.py`.

## 2026-10-01 · #191 — 재사용 workdir 통제 링크 차단

- 결론: resume·retry가 기존 workdir을 열기 전에 통제 구역 링크를 검사하고, 링크나 검사 불완전이 있으면 실행을 거부한다.
- 바뀐 것: #132의 공용 `zone_links` 경로를 재사용했다. adapter와 `TASK.md` 생성보다 검사를 앞세웠으며 패치노트는 건드리지 않았다.
- 실행한 것: 회귀 2건은 수정 전 실패, 수정 뒤 통과했다. 관련 test 97 passed/1 skipped, 전체 pytest 1923 passed/23 skipped, Node 11개와 `scripts/check_public.sh`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/runner/daemon.py`, `tests/test_intake_references.py`.

## 2026-10-01 · #189 Windows CI flaky test 조건 대기

- 결론: bench process tree, e2e 이벤트·wake 결과, semantics breaker 저장을 고정 시간 대신 상한 있는 polling으로 확인한다. 검사는 그대로 두고 실패 시 PID·프로세스 상태·이벤트 순서·breaker 상태를 출력한다.
- 실행한 것: 수정 중 e2e 6회차에서 `recruit.suggested`·HPC 이벤트 누락을 재현했다. 최종 변경 파일별 20회 실패 0, 전체 pytest 3회 각각 1889 passed/23 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 바뀐 파일: `tests/test_bench.py`, `tests/test_e2e_mock.py`, `tests/test_semantics_shadow_breaker.py`, `STATUS.md`. 새 의존성과 patch note 변경은 없다.

## 2026-10-01 · PR #176 3회차 — 참고 공개 가림 부류 종료

- 결론: 같은 부류의 P1 4154275555를 닫았다. 공개 게시 전에 path와 PI 기본 GitHub 참고를 한 함수에서 정규화해 경로와 저장소 정체성을 함께 가린다. P3 4154275565의 README 중복도 합쳤다.
- 바뀐 것: `mask_published_references`가 POSIX·Windows drive·UNC·백슬래시·대소문자·home·URL 표기를 다루고, GitHub URL·`owner/name`·clone 폴더명을 가린다. 표기별 회귀 11건을 한 표로 묶었다. README 참고 항목은 2개에서 1개, 39,376자에서 38,282자로 줄었다. 패치노트는 건드리지 않았다.
- 실행한 것: 회귀 표는 수정 전 5 failed/6 passed, 수정 뒤 11 passed였다. 관련 test는 154 passed/1 skipped, 전체 pytest 1892 passed/22 skipped, Node 11개, `scripts/check_public.sh`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/intake.py`, `labhq/integrations/github.py`, `tests/test_intake_references.py`, `README.md`.

## 2026-10-01 · PR #176 봇 리뷰 P1 3건 — 짧은 home 참고와 공용 경로 scanner

- 결론: P1 3건을 닫았다. 짧은 home 참고를 모든 러너 표현에서 가리고, 경로·통제 구역 탐색은 공용 scanner로 입력 길이에 비례해 돈다.
- 바뀐 것: path mask의 길이 필터를 없앴다. `mentions_zone`·`touches`·`touches_resolved`·`sanitize`는 separator run 첫머리만 검사하는 scanner를 함께 쓴다. 패치노트는 건드리지 않았다.
- 실행한 것: 2회차 회귀 4개는 수정 전 각각 약 3초로 2초 상한을 넘었고 수정 뒤 합계 0.84초였다. 전체 pytest 1881 passed/22 skipped, Node 11개, `scripts/check_public.sh`가 통과했다.
- 미해결: 없음.
- 근거: `labhq/intake.py`, `labhq/policy.py`, `labhq/integrations/github.py`, `tests/test_intake_references.py`, `tests/test_policy.py`, `tests/test_github_reporter.py`.

## 2026-10-01 · #36 PR A 후속 8건 — 참고 자료 정보 경계와 폴더 링크 검사를 판정 하나로

- 결론: 참고 자료의 경로·URL이 공개 보고·라운드 기록·prompt로 새는 길과, 참고·upstream·project 폴더의 링크·통제 구역 검사를 각각 공통 함수로 모았다. #123 #124 #130 #131 #132 #133 #134를 고쳤다. #125의 확인 조건은 #122에서 이미 테스트와 함께 들어가 있어 판정만 공통 함수로 옮겼다.
- 바뀐 것: `intake.path_pattern`·`url_pattern`(구분자 run, drive·Git Bash, JSON `\\`·`\/`·`\uXXXX`, 기본 포트, 대소문자)을 prompt 지우기(#133)와 게시 가림이 함께 쓴다. 프로젝트 보고와 라운드 기록은 `github.publish_clean` 하나를 쓴다(#130). 가림 대상은 `published_reference_masks`가 정한다: 모든 path 참고, PI 기본 github(URL·`owner/repo`)·url(#123). `~` 참고는 적은 그대로 저장하고 러너가 자기 home으로 푼다(#124). 공개 가드는 webhook path·session·code도 가린다(#134). 실경로 게이트는 후보 256개를 넘으면 셸·MCP·Glob을 ask로 보낸다(#131). `intake._walk`·`overlaps_zone` 위에 참고(엄격)·upstream(구역 링크가 있으면 열지 않음)·project(열되 Claude 거부 규칙) 검사를 얹었다(#132). README §4·§8·§10을 맞췄다.
- 실행한 것: issue마다 회귀 test를 먼저 썼고 수정 전 코드에서 모두 실패함을 확인했다(새 test 11개·12건, 기존 루트 검사 1건 강화). 링크 훑기 비용을 쟀다: Windows 11에서 항목 20,200개가 1,078 ms에서 33 ms로 줄었다(ismount를 POSIX에서만 부름). 전체 pytest 1555 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh`, `git diff --check` 통과. 검증에서 여섯 가지를 더 고쳤다: project 링크의 별칭(`b`가 `a`와 같은 폴더, 안쪽 별칭, 폴더 자신으로 돌아오는 링크)에도 거부 규칙을 붙이고, 훑기가 통제 구역 안을 열거나 그 안의 링크 이름을 로그에 남기지 않게 했다. 가림 정규식이 긴 줄을 위치마다 다시 훑지 않게 했다. 구분자로 여는 match는 구분자 run 첫머리에서만 시작하고, 나머지 열린 반복은 상한으로 묶었다(공백 없는 60 KB 줄 여섯 개: 267초 → 0.5초, 구분자 수에는 상한 없음). `CORPlice:pw@` 같은 도메인 계정 userinfo가 다시 가려진다(이 PR에서 생긴 회귀). 공개 가드가 통제 구역을 참고 가림보다 먼저 본다(`/refs` 참고가 `/srv/refs/vault` 줄을 가려 구역 판정을 피하던 회귀). IPv6 host URL 참고도 query가 떨어지고 가려진다. 새 test 5건, 기존 1건 강화.
- 미해결: 자체 호스팅 webhook처럼 이름 없이 path에 든 비밀값은 못 가린다(README §10). PI 기본 url이 공개 사이트면 그 host URL도 보고에서 가려진다. project 링크 거부 규칙은 Claude만 따르고 Codex·셸은 막지 못한다. Windows에서 대소문자만 다른 유지 참고가 거부 경로와 함께 지워질 수 있다. 패치노트는 PR 번호가 생긴 뒤 쓴다. 거부 규칙은 적힌 경로 비교라 8.3 짧은 이름 같은 표기는 남을 수 있다. 나머지 P2는 후속 issue로 넘긴다.
- 근거: `labhq/intake.py`, `labhq/integrations/github.py`, `labhq/integrations/rounds.py`, `labhq/policy.py`, `labhq/runner/daemon.py`, `tests/test_intake_references.py`, `tests/test_github_reporter.py`, `tests/test_round_records.py`.

## 2026-10-01 · PR #164 2회차 봇 리뷰 P1 — 읽기 전용 workspace 지시 경계 통합

- 결론: 읽기 전용 실행이 workspace에서 읽을 수 있는 지시·memory·skill·설정 이름을 한 판정 함수로 모았다. 깊이와 숨김 폴더에 관계없이 Claude Code는 차단 목록으로 제외하고, 끌 수 없는 agents-md와 Codex project 지시는 실행 전에 거부한다.
- 바뀐 것: adapter가 쓰는 `CLAUDE.md`·`CLAUDE.local.md`·`.claude/**`·`AGENTS*.md`·`.agents/**`·`.codex/**` 규칙을 `read_only.py` 한 곳에 뒀다. 계약 skill은 매 실행 원본에서 다시 만들며 원본이 없으면 stale 사본을 지우고 실행을 거부한다. POSIX directory symlink와 Windows junction은 대상 내용을 건드리지 않고 링크만 제거한다.
- 실행한 것: 새 회귀 묶음은 수정 전 6 failed/1 skipped(POSIX 전용), 수정 후 관련 64 passed/1 skipped였다. 전체 pytest 1674 passed/22 skipped, Node 11개, `bash scripts/check_public.sh`, `git diff --check`가 통과했다.
- 미해결: 새 CLI가 다른 workspace 지시 파일 이름을 도입하면 중앙 목록을 갱신해야 한다(README §10).
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/claude_code.py`, `labhq/runner/workspace.py`, `tests/test_read_only_followups.py`.

## 2026-10-01 · PR #164 봇 리뷰 P1 — 재사용 workspace의 계약 skill 변조 차단

- 결론: 계약 skill은 매 실행 직전에 원본에서 새로 복사한다. 이전 writable step이 설치본 내용을 바꾸거나 skill 디렉터리를 symlink·Windows junction으로 교체해도 read-only run은 그 지침을 읽지 않는다.
- 바뀐 것: `TaskWorkspace.install_skill()`이 링크가 아닌 상위 폴더에서 임시 복사본을 만든 뒤 기존 설치본을 링크 대상까지 따라가지 않고 교체한다. 상위 폴더가 링크이거나 일반 폴더가 아니면 건드리지 않아 기존 workspace 검사가 read-only run을 거부한다.
- 실행한 것: 내용 변조와 junction 치환 회귀 2건이 수정 전 실패하고 수정 후 통과했다. 관련 테스트 48개, 전체 pytest 1562 passed/21 skipped, Node 테스트 11개, `bash scripts/check_public.sh`, `git diff --check`가 통과했다.
- 근거: `labhq/runner/workspace.py`, `tests/test_read_only_followups.py`.

## 2026-10-01 · PR #122 후속 다섯 건(#135 #145 #146 #147 #148) — 읽기 전용 실행의 env·작업 폴더 지침 파일

- 결론: 이어 묻기·상담의 허용 목록을 argv 밖까지 넓혔다. 엔진 env는 로그인·설정 위치·API 접속 변수만 받고, 러너가 물려받은 `CODEX_*` 세션 변수는 모든 직원 실행에서 빠지며, 엔진이 작업 폴더에서 지침·설정으로 읽는데 끌 플래그가 없는 파일이 있으면 읽기 전용 실행을 띄우지 않는다. 실측할 수 없던 동작(신뢰된 경로 아래 Codex project config, Claude built-in agents-md)은 거부 쪽으로 뒀다.
- 바뀐 것: `labhq/util.py`(`CODEX_ENV_PASSTHROUGH`, `strip_parent_session_env`), `labhq/adapters/read_only.py`(`READ_ONLY_ENV_KEEP`, `READ_ONLY_WORKSPACE_REFUSED`, `read_only_workspace_error`), `labhq/adapters/base.py`(`staff_env`, 뺀 env 이름을 피드에 경고), Codex preflight가 작업 폴더 `AGENTS.override.md`를 모든 실행에서 거부, 읽기 전용 Claude가 작업 폴더 CLAUDE.md류를 하위 폴더 것까지 `claudeMdExcludes`에 넣음(하위 폴더는 독립 검증에서 찾은 틈), `labhq/bench.py` baseline env 순서를 직원 실행과 맞춤. #135는 main에 이미 고쳐져 있어(12e3fba) 테스트만 더했다. B1 그림자 모드 PR(#150)의 파일(gateway/server.py, cli.py, research/semantics*.py, settings.py)은 건드리지 않았다.
- 실행한 것: 새 `tests/test_read_only_followups.py` 22개 중 20개가 수정 전 코드에서 실패했다(나머지 2개는 PI가 고른 env·Claude의 .codex 오탐 방지). 로컬 Codex 리뷰 1회 P2 1건(bench가 engine env의 CODEX_*까지 지움)을 고쳤다. 독립 검증에서 하위 폴더 CLAUDE.md 틈 1건을 고쳤다. 전체 pytest 1560 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh`, `git diff --check` 통과. 실제 CLI probe는 하지 않았다.
- 미해결: #148의 신뢰 경로 probe와 Claude agents-md가 `AGENTS.md`를 읽는지는 재지 않았다(재면 거부를 풀 수 있다). 러너를 띄운 셸의 `CLAUDE_*`·`CODEX_*` 밖 변수(`NODE_OPTIONS` 등)와 작업 폴더 상위의 `.codex/`·지침 파일은 보지 않는다. 일반 쓰기 step은 작업 폴더 `.codex/`를 그대로 둔다. 계약 skill 폴더를 앞선 실행이 고친 것은 알아채지 못한다.
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/codex.py`, `labhq/util.py`, `tests/test_read_only_followups.py`, README §8·§10.

## 2026-10-01 · #63 README 사무실 화면을 15초 움직이는 이미지로

- 결론: README의 2.5D·3D 사무실 정지 화면을 각 15초짜리 움직이는 WebP로 바꿨다. 요청 하나가 CSO 계획, 승인 카드, 단계 진행을 거쳐 끝난다. 기존 PNG는 정지 화면 링크로 남겼다.
- 바뀐 것: `docs/media/office-25d.webp`(1.31 MB, 1200×750, 15fps, 217프레임), `docs/media/office-3d.webp`(2.38 MB, 1200×750, 12fps, 177프레임), README 이미지 줄과 설명 문구. 코드·의존성·pyproject는 그대로다.
- 실행한 것: 임시 state_dir에서 mock demo(키·클러스터 없음)를 띄우고 headless Chrome을 CDP screencast로 녹화해 Pillow로 인코딩했다. 녹화 script는 PI 결정(일회성 코드)대로 저장소에 넣지 않았다. 녹화 중에만 mock 단계를 0.9초(계획 1.4초)씩 늦추고 승인은 3초 뒤 자동으로 했다. 같은 프레임의 GIF는 51.5 MB·52.3 MB라 WebP를 골랐다. 프레임에 로컬 경로·사용자명·token이 보이지 않는 것을 확인했다.
- 미해결: 3D는 headless Chrome의 하드웨어 GPU로 녹화했다. SwiftShader는 1600×1000에서 초당 4프레임 정도라 끊겼다. 화면이 바뀌면 다시 녹화해야 한다.
- 근거: `docs/media/office-25d.webp`, `docs/media/office-3d.webp`, `README.md`.

## 2026-10-01 · 연구 결과·출처 검증 후속 6건 (#114 #116 #117 #118 #128 #129)

- 결론: 연구 결과와 출처 검증에 남은 빈틈 여섯 개를 닫았다. 연구 단계 실행은 여전히 opt-in이고 기본 꺼짐이라, 바뀐 것은 schema·검사·verifier뿐이다.
- 바뀐 것: `single_cell_de@2`가 통과할 조합이 없던 `normalized_counts`를 field에서 거절한다(#114, 내장 pack hash 고정, 없어진 version을 적으면 남은 version을 알림). 결과는 선택한 단계의 `claim_ids` 밖 claim을 못 내고, 필수 evidence slot마다 `evidence.slots`로 채운 행이 있어야 한다(#128). 0건 검색 행의 출처도 해소하고, 추론·가설 행도 외부 출처면 `accessed_at`이 필요하다(#129). 조회는 동시 4개, 보고 전체 120초 deadline이고 넘긴 것은 미확인이다(#116). ID 옆 URI가 다른 곳을 가리키면 `conflicting`이고, 주요 레지스트리 URL(GEO·PubMed·PMC·identifiers.org 등)은 ID와 같은 출처로 묶인다(#117). DOI 옆 PubMed URL처럼 체계가 다르거나 version·isoform만 다른 URL은 코드가 결함으로 단정하지 않고 resolver에 넘기며, `/search`·`/docs` 같은 경로는 레코드로 읽지 않는다. 근거 행에 `result_count`를 두고, claim별 REVIEW v2(`labhq/research/review.py`)가 부재를 근거로 쓴 것을 늘 중대 결함으로 받는다(#118). 의미 모델 pilot fixture의 합성 결과 네 개에 slot을 적어 `FIXTURE_SHA256`이 바뀌었다(답은 그대로).
- 실행한 것: issue마다 회귀 test를 먼저 써서 수정 전 실패를 확인했다(#114 9개, #128 3개, #129 5개, #116 3개, #117 25개, #118 test 파일 전체·result_count 1개). 독립 검증에서 정상 출처를 결함으로 판정하던 두 경우를 고치고 회귀 test 13개(수정 전 12개 실패)를 더했다. 전체 pytest 1605 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과. Python 3.10 문법은 ast로만 확인했다.
- 미해결: live resolver가 아직 없고 deadline·동시성은 함수 인자다(설정 키 없음). 레지스트리 밖 URL을 ID와 함께 적으면 resolver가 uri 조회를 지원할 때까지 미확인이다. `result_count` 없이 "0 hits"만 적은 행은 reviewer가 잡아야 하고, REVIEW v2는 실행 경로에 연결하지 않았다. 설정에 `single_cell_de@1`이 남아 있으면 연구 계획 단계에서 오류가 난다.
- 근거: `labhq/research/packs/single_cell_de.yaml`, `labhq/research/contract.py`, `labhq/research/review.py`, `labhq/evidence/claims.py`, `labhq/evidence/verify.py`, `docs/research_protocol.md`, `tests/test_research_protocol.py`, `tests/test_research_evidence.py`, `tests/test_evidence_verify.py`, `tests/test_research_review.py`.

## 2026-10-01 · #150 B1 의미 모델 그림자 — 요청 뒤 두 모델을 계산해 로컬에만 기록

- 결론: `semantics: shadow`면 요청이 끝난 뒤 출처 의미 모델(#136)과 읽기 전용 객체·링크 뷰를 계산해 같은 줄에 나란히 남긴다. 기본은 off다. mock lab에서 off·shadow·보류 mode·오타 네 경우의 prompt·schema·계획·승인·결과·리뷰·round 기록·웹 snapshot·event가 같았다(diff 0). mock 요청의 재사용 후보는 0이었다. 산출 type 선언이 없어서이고, 이 공백을 재는 것이 B1의 목적이다.
- 바뀐 것: `Settings.semantics` 한 칸, 새 `labhq/research/semantics_shadow.py`(설정·worker·산출 hash·자동 off·report)와 `semantics_objects.py`(객체 8종·링크 12종, 액션 없음), `semantics.py`의 `records_from_rows`, `labhq semantics report|enable|mark`, `scripts/semantics_shadow_remove.py`. 연결은 `# semantics-hook` 표시 줄 24개(settings 2·server 9·cli 13)뿐이다. 기록은 `gateway.state_dir/semantics/`에 ID·종류·hash·개수만 남는다.
- 실행한 것: 기능 commit마다 새 test를 먼저 돌려 실패를 확인했다. 예외: 산출 hash의 승격 금지 test 2개와 git work tree 거부 test 1개는 앞 commit에서 이미 통과했다(후보가 원래 0이거나 worker commit의 기능). 전체 pytest 1668 passed/22 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과. 제거 시험은 임시 사본에서 지운 뒤 compile·설정 load·state 읽기와 e2e·pilot·cso test 통과. 로컬 Codex 리뷰 5회에서 P2 13건이 나왔고 모두 고쳤다(P1 없음). 이벤트 루프 부하는 1·3회차에 같은 부류로 나와 DB 읽기를 worker의 별도 읽기 전용 연결로 옮겨 닫았다. 나머지는 manifest 반영·구역 검사, observed 만료, 옵션 붙은 off, 멈춘 worker 뒤 새 epoch, 외부 off 즉시 반영, report 상태, git work tree 거부의 링크·CLI 우회, 멈춘 작업의 기록, 경로 표기였다. 리뷰는 5회에서 멈췄다. Python 3.10은 compile만 확인했다.
- 미해결: 원격 runner 산출 hash(생성 시점 hash를 provenance에 싣기)는 별도 issue가 필요하다. 판정 기한 issue(병합+90일, 중간 +30일)와 semantics CI job은 이 PR에 없다. B2(CSO advisory)·팔란티어식 액션·EDAM(#151)은 PI 결정으로 보류다. symlink test는 이 Windows 계정에 권한이 없어 skip했고 junction test는 통과했다. 패치노트는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/research/semantics_shadow.py`, `labhq/research/semantics_objects.py`, `scripts/semantics_shadow_remove.py`, `tests/test_semantics_shadow_*.py`, `tests/test_semantics_objects.py`, `tests/semantics_shadow_lab.py`.

## 2026-10-01 · #120 Slurm 스케줄러, #153 README 생성 블록 겹침

- 결론: `hpc.scheduler: slurm`으로 HPC 직원 도구가 Slurm 클러스터에 붙는다. 제출 전 PI 승인, 실패와 '잡 없음' 구분, 제출 재시도 없음, 수면·기상, `ssh_host`·`submit_prefix`·`job_group`이 SGE·PBS와 같은 경로로 돈다. README 배지에 SLURM이 settings Literal에서 저절로 생겼다. `integrations.py --write`는 두 생성 블록이 겹치면 아무것도 쓰지 않고 실패한다.
- 바뀐 것: `labhq/tools/scheduler.py`(`sbatch --parsable`, `squeue` 다음 `sacct`, `scancel`, 배열·het 기록 묶기), `HpcSettings.slurm.sbatch_args`(기본 `--export=NONE`, 알 수 없는 placeholder는 load 때 거부), `labhq init`의 `sbatch`·`sinfo` 판별(Slurm의 Torque wrapper `pbsnodes`는 PBS로 보지 않고, 두 종류가 보이면 묻는다), doctor의 Slurm 명령 점검. 같은 부류는 공통 판정으로 닫았다: 직원이 준 job id는 세 스케줄러 모두 숫자로 시작해야 받는다(옵션 `scancel --user=…`, Torque `qdel all`, SGE 잡 이름 같은 일괄 선택 차단), 스크립트 머리의 주석·지시는 세 종류 모두 preamble 위에 둔다, 셸 직접 실행 확인 목록에 `srun`·`salloc`·`scancel`·`qrsh`·`qlogin`을 더해 Bash·PowerShell이 같은 상수를 쓴다. 제출 시간 초과·id 해석 실패는 "제출됐을 수 있으니 hpc_queue부터 보라"고 알린다.
- 실행한 것: 가짜 `sbatch/squeue/sacct/scancel`(`tests/fixtures/fake_slurm.py`)로 제출→대기·보류→실행→완료·실패·OOM·시간 초과·취소, accounting 지연, 러너 watcher의 수면·한 번 기상을 확인했다. MCP stdio fixture로 Slurm 오류 5종과 잘못된 job id가 직원 tool error로 가는지 봤다. 회귀 test는 수정 전 코드에서 실패를 확인했다(#153 중첩 순서, MCP 6, init 4, doctor 3, 수정 전 import가 없는 `test_slurm.py` 전체). 로컬 Codex 리뷰 P2 2건(압축된 array id `4100_[1,8]`이 `hpc_status`에서 거부됨, `--clusters` 제출의 추적 대상 소실)은 `squeue -r`과 `-M`/`--clusters` 거부로 고쳤다. 독립 검증에서 같은 부류가 두 군데 더 나와 고쳤다. 직원 스크립트의 `#SBATCH -M`/`--clusters`는 제출 전에 거부하고, 다른 경로로 다른 cluster에 간 제출(`id;cluster`)은 로컬 id로 추적하지 않고 오류로 알린다. job id 판정을 '옵션 모양 거부'에서 '숫자로 시작'으로 바꿨다. 전체 pytest 1645 passed/21 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과.
- 미해결: 실제 Slurm 클러스터 제출은 하지 않았다. accounting이 없는 클러스터, `PrivateData=jobs`에서 다른 계정이 낸 잡, `--export=NONE`과 잡 안의 `srun`, `#SBATCH --array`가 core-hour에 안 들어가는 점은 README §10에 적었다. 패치노트 행은 PR 번호가 생긴 뒤 따로 쓴다.
- 근거: `labhq/tools/scheduler.py`, `labhq/settings.py`, `labhq/init_wizard.py`, `labhq/doctor.py`, `scripts/integrations.py`, `tests/test_slurm.py`, `tests/fixtures/fake_slurm.py`.

## 2026-10-01 · #127 출처·재사용 의미 모델 비교 pilot (PR A: opt-in으로 main에)

- 결론: 중단 기준은 충족했다. 기준선 A(메모리 SQLite + 관계 표 + 재귀 CTE)가 모델 B와 같은 의미·정확도를 냈다(base·변경 1·변경 2 모두 17/17, 잘못된 동일시 0, 소비자 불일치 0). 그러나 PI 결정(2026-10-01)으로 접지 않고 opt-in·실행 경로 미연결로 유지한다. 그림자 모드로 실데이터를 모아 #121에서 재평가한다. 합성 fixture 결과이고 실제 요청의 효과가 아니다.
- 바뀐 것: 새 파일 `labhq/research/semantics_v1.yaml`·`semantics.py`(B, 공유 read_records), `tests/semantics_baseline.py`(A), `tests/test_semantics_pilot.py`·`test_semantics_public.py`, `scripts/semantics_pilot.py`, `docs/reference/semantics_pilot.md`. 후속 6건을 고쳤다: B resume 시각 unknown(#137), A claim 반복 보고(#138), B independent_groups(#139), A 노드 단위 순회(#140), reader immutable 경쟁(#141), LLM 0 차단·script import 검사(#142). `expected.yaml`·hash·모델 YAML은 그대로다. `pyproject.toml` package-data 1줄. 설정·실행 경로·CSO·state_dir은 그대로다.
- 실행한 것: 후속마다 수정 전 실패하던 회귀 test(#137 TypeError, #138 IntegrityError, #139 group 누락, #140 16층 1.83초, #141 writer 행 놓침, #142 os 함수 실제 실행 시도). 측정 다시 실행(1× warm 200회·5 batch, 50× 복제, 20층 diamond A 1.9 ms·B 0.2 ms). Codex 리뷰 1회(새 결함 없음). 전체 pytest 1401 passed/20 skipped, `node tests/*.cjs` 8개, `bash scripts/check_public.sh`, `git diff --check` 통과. 격리 test(설정 키 0, 실행 경로 import 0, settings.py diff 0, CSO prompt 동일)도 그대로 통과한다.
- 미해결: #143(접는 커밋 순서)은 접지 않으므로 닫는다. 변경 내용을 구현 전에 알아 B에 분기를 미리 넣었으므로 변경 시간 지표는 변경 비용을 재지 못했다. A의 N+1 SQL 탓에 성능 비교는 근거가 약하다. 그림자 모드·팔란티어식 운영 객체 뷰는 다음 PR.
- 근거: `docs/reference/semantics_pilot.md`, `tests/fixtures/semantics/expected.yaml`, `scripts/semantics_pilot.py`, `tests/test_semantics_pilot.py`.

## 2026-10-01 · #63 README 맨 위 배지 — 종류별 개수 대신 대상 하나씩

- 결론: README 제목 바로 아래 배지가 "builtin MCP 3" 같은 개수가 아니라 대상 이름과 역할을 보여 준다. 누르면 그 대상의 공식 페이지나 README 해당 절로 간다. 없는 대상(Slurm #120, Gemini·Antigravity 직원, LICENSE)은 배지를 만들지 않는다.
- 바뀐 것: `scripts/integrations.py`가 `<!-- badges:start -->` 블록을 만든다. 엔진(Claude Code 직원 8명·Codex 직원 3명과 모델), 지원 스케줄러(`HpcSettings.scheduler` Literal을 ast로 읽어 SGE·PBS, mock·none 제외), labhq MCP(approval·ask·hpc 한 배지), PubMed, bioRxiv / medRxiv, bioinfo-agent plugin, Paper2Agent skill이 한 줄, CI(push·PR workflow `test`)·Python 3.10+(requires-python)·패치노트가 둘째 줄이다. logo는 shields.io에서 그려지는 것을 확인한 simple-icons slug 표(`LOGOS`)만 받는다. §2의 개수 배지는 지웠고 표는 그대로다. 손으로 적던 test·패치노트 배지는 생성 블록으로 옮겼고, `pyproject.toml`에 `[project.urls] Repository`를 넣었다.
- 실행한 것: 생성된 배지 URL을 모두 받아 제목과 logo(`<image>`)를 확인했고 링크 9개가 200이었다. `tests/test_integrations.py` 20개(없는 대상 미생성, settings Literal fixture, workflow·LICENSE 유무, 비공개 값·로컬 경로 미노출, badge anchor가 README 제목과 일치, 검증 안 된 logo 거부). 전체 pytest 1416 passed/20 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과. 로컬 Codex 리뷰는 지적 없음.
- 미해결: Codex(openai)와 bioRxiv는 simple-icons logo가 그려지지 않아 logo 없이 둔다. SGE는 하나로 정해진 공식 페이지가 없어 PBS와 함께 README §8로 링크한다. Python 3.10 실행은 못 했고 문법만 확인했다(3.10 CI가 본다).
- 근거: `scripts/integrations.py`, `tests/test_integrations.py`, `README.md`, `pyproject.toml`.

## 2026-10-01 · #36 PR A 리뷰 2회차 검증 — prefix_args 옵션, Claude·Codex 우회 실측

- 결론: 남은 통로 하나를 닫았다. 읽기 전용 실행은 PI `extra_args`를 빼지만 `prefix_args`는 그대로 앞에 붙였고, Codex가 `exec` 앞의 `--dangerously-bypass-approvals-and-sandbox`로 `-s read-only`에서 파일을 썼다. 이제 `prefix_args`에 옵션이 있으면 읽기 전용 실행을 거부한다.
- 바뀐 것: `read_only_launch_error`(`labhq/adapters/read_only.py`)를 `AgentAdapter.run`이 prepare 전에 부른다. 환경변수 전개 뒤 `-`로 시작하는 항목이 있으면 CLI를 띄우지 않는다. 일반 step은 그대로 쓴다.
- 실행한 것: 어댑터가 만든 읽기 전용 명령을 실제 CLI로 돌렸다. Claude 2.1.282: 엔진 env의 `CLAUDE_CODE_PLUGIN_DIRS` plugin은 실리지만 hook·plugin MCP는 뜨지 않았고 `CLAUDE_CODE_MANAGED_SETTINGS_PATH` hook도 돌지 않았다. Codex 0.159.2(Windows elevated sandbox): shell 쓰기 거부, 작업 폴더 `.codex/config.toml`의 notify·MCP와 `.codex/hooks.json`은 안 읽힘, `windows.sandbox` 없이도 정책 거부, workspace-write 세션을 이어도 read-only 유지. 새 테스트 2개는 수정 전 실패를 확인했다.
- 미해결: 같은 follow-up이 gateway 재시작 뒤 이전 실행과 같은 session·workdir를 쓸 수 있다(로컬 Codex 리뷰 P2). 엔진 env로 실린 Claude plugin은 실측상 쓰기 통로가 아니지만 허용 목록 밖이고, 러너가 물려주는 `CODEX_*` 변수(`CODEX_EXEC_SERVER_URL` 등)는 재지 않았다. 셋 다 후속 issue로 넘긴다.
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/base.py`, `tests/test_read_only_profile.py`.

## 2026-10-01 · #36 PR A 리뷰 2회차 — 읽기 전용 허용 목록 profile, 사후 파일 비교

- 결론: 이어 묻기·상담은 직원 설정에서 지우는 방식이 아니라 허용 목록 profile로 돈다. plugin·hook·MCP·PI extra_args가 빠지고, 그래도 실행 중 파일이 바뀌면 결과를 실패로 하고 PI에게 알린다. 1회차 MCP 우회와 2회차 plugin hook 우회는 같은 부류(지우기 목록에 남은 새 통로)라 구조로 닫았다.
- 바뀐 것: `labhq/adapters/read_only.py`의 `read_only_profile`이 직원의 이름·역할·지침·모델·한도·`project_dirs`·`disallowed_tools`만 가져온다. 분류되지 않은 AgentSpec 필드가 생기면 만들기를 거부한다. 러너는 보낸 쪽 override를 보지 않는다. Claude는 `--setting-sources ""`·`disableAllHooks`·plugin 없음, Codex는 `--disable hooks·plugins·apps·computer_use·browser_use`이고, 둘 다 `isolate_user_config`와 상관없이 격리한다. 러너가 실행 전후로 작업 폴더와 쓰기 가능한 project·upstream·참고 폴더를 비교한다(상한 `runner.read_only_check_max_entries` 50,000을 넘으면 실행 거부). 바뀐 목록은 manifest `read_only_changes`, 피드 경고는 `agent.log` level `alert`.
- 실행한 것: Claude 2.1.282로 실측했다. 수정 전 명령에서 작업 폴더·plugin의 SessionStart·Stop hook 4개가 plan 모드와 `Read,Glob,Grep`을 거치지 않고 돌았고 새 명령에서는 하나도 돌지 않았다(fixture `claude_read_only_*.jsonl`). Codex feature 이름은 codex-cli 0.159.2 `features list`로 확인했다. 새 테스트 22개 중 18개가 수정 전 코드에서 실패했다(나머지 4개는 기존 금지 목록 유지·오탐 방지·fixture 고정), Node 1개도 수정 전 실패를 확인했다. 로컬 Codex 리뷰 P1 1(profile이 `disallowed_tools`를 지움)·P2 2(폴더 ctime 오탐, 취소 때 비교 누락)를 모두 고쳤다. 전체 pytest 1402 passed/20 skipped, `node tests/*.cjs` 11개, `bash scripts/check_public.sh` 통과.
- 미해결: Windows에는 ctime이 없어 크기를 두고 mtime을 되돌린 수정은 못 본다. 다른 러너·프로세스가 같은 폴더를 쓰면 실패로 잡힌다. 감시 폴더 밖 쓰기와 Claude managed settings hook은 범위 밖이다. 옛 Codex에 `--disable` 이름이 없으면 읽기 전용 실행이 CLI 오류로 실패한다. Codex 실측 probe는 하지 않았다.
- 근거: `labhq/adapters/read_only.py`, `labhq/adapters/claude_code.py`, `labhq/adapters/codex.py`, `labhq/runner/integrity.py`, `labhq/runner/daemon.py`, `tests/test_read_only_profile.py`, `tests/fixtures/real/claude_code/claude_read_only_*.jsonl`.

## 2026-10-01 · #36 PR A 리뷰 1회차 — 읽기 전용 wrap-up, 링크로 적힌 통제 구역

- 결론: 이어 묻기·상담이 턴 한도에 걸려도 읽기 전용이 풀리지 않는다. 링크(symlink·junction)로 적힌 통제 구역은 러너가 실제 경로로 막는다. P1 두 건을 고쳤고 P2 다섯 건은 고쳤으며 네 건은 후속 issue로 넘긴다.
- 바뀐 것: read-only 작업은 wrap-up을 건너뛰고, 다른 작업의 wrap-up은 기존 override에 `max_turns`만 더한다. 러너가 통제 구역도 resolve해 비교한다. 질문 `options`가 list가 아니면 자유 입력 질문으로 읽고 list 밖의 질문 하나도 버리지 않는다. 참고 값의 NEL·DEL·U+2028/2029를 거부한다. 게이트웨이 루트 비교는 OS와 무관한 글자 비교다(Windows 게이트웨이의 POSIX 루트). 작업·프로젝트 폴더를 품은 참고는 뺀다. 프로젝트 보고의 경로 가림은 대소문자·구분자와 무관하다.
- 실행한 것: 로컬 Codex 리뷰(origin/main 대비) P1 1·P2 1 모두 고침. 새 테스트 13개 중 12개가 수정 전 실패함을 확인했다(1개는 이미 막히던 url 줄바꿈을 고정). 전체 pytest 1234 passed/19 skipped, `node tests/*.cjs` 10개, `bash scripts/check_public.sh` 통과.
- 미해결: PI 기본 참고의 github·url 가림, `~` 경로를 러너 계정 기준으로 풀기, 참고 폴더 안의 링크가 통제 구역을 가리키는 경우(project_dirs와 같은 한계), snapshot의 이어 묻기 답 길이. 실제 Claude·Codex CLI는 돌리지 않았다.
- 근거: `labhq/orchestrator/cso.py`(run_step wrap-up), `labhq/runner/daemon.py`(`_reference_dirs`), `labhq/intake.py`, `labhq/integrations/github.py`, `tests/test_followup.py`, `tests/test_intake_references.py`, `tests/test_intake_questions.py`.

## 2026-10-01 · #36 접수·참고 자료 PR A — 구조화 확인 질문, 참고 포인터, 이어 묻기

- 결론: CSO 확인 질문이 선택지 버튼·자유 입력·깊이(약 30/60/90분)로 폰에 뜨고, 답은 #34 경로로 재계획에 들어간다. 요청에 GitHub·DOI·PMID·URL·러너 경로 포인터를 붙이면 브리핑·계획·단계 prompt에 들어가며 경로는 쓰기 권한 없이 열린다. 끝난 요청은 같은 CSO 세션에 이어 물을 수 있다.
- 바뀐 것: `PLAN_SCHEMA.clarifying_questions`가 `{question, options 2-4, allow_free_text, depth?}`이고 문자열도 읽는다. PR 1 연구 PLAN도 같은 구조를 받되 문자열 질문은 그대로 둬서 기존 hash가 그대로다. `RequestIn.references`·`default_references`, `pi_profile.references`·`runner.reference_roots`(example은 빈 값), `labhq send --ref`·`--no-default-refs`, 2.5D **참고** 칩을 넣었다. 경로는 게이트웨이가 루트·통제 구역으로 거르고 러너가 실제 경로로 다시 확인한다. Claude는 `--add-dir`과 Edit·Write 거부, Codex는 `--add-dir` 없이 읽고, 쓰기 가능한 참고 경로는 러너가 한 번 경고한다. 프로젝트 GitHub 보고에서는 경로 참고를 가린다. `POST /api/requests/{id}/followup`은 읽기 전용으로 resume하고 요청 상태를 바꾸지 않는다. 4열 작업판이 패널 폭을 넓히던 문제도 고쳤다.
- 실행한 것: 기능마다 회귀 테스트를 먼저 썼다. 새 테스트를 main 위에서 돌려 실패함을 확인했다(pytest 61개: 질문 8·참고 44·이어 묻기 8·e2e 1, Node 3·4·2. 기존 승인 게이트 동작을 고정하는 테스트 1개는 main에서도 통과). 수정 후 전체 pytest 1222 passed/19 skipped, `node tests/*.cjs` 10개, `bash scripts/check_public.sh` 통과. 로컬 게이트웨이와 mock 러너로 2.5D·3D 질문 카드, 참고 칩, 이어 묻기를 브라우저에서 눌러 확인했다(375px 폭 포함). 로컬 Codex 리뷰(branch 대 main): P1 미리 허용된 셸이 참고 경로에 쓸 수 있음 → prompt 규칙·쓰기 가능 경고·README로 대응(셸 sandbox는 범위 밖), P2 이어 묻기 초안이 다른 요청으로 넘어감 → 고침.
- 미해결: 미리 허용된 셸 명령의 참고 경로 쓰기는 OS 권한으로만 막힌다. 실제 Claude·Codex CLI로는 돌리지 않았다. Codex가 `--add-dir` 없이 참고 경로를 읽는다는 것은 sandbox 기본 동작에 기댄 것이고, Windows elevated sandbox에서 확인하지 않았다. Gemini·Antigravity·cli 직원은 경로를 prompt로만 받는다. 범위 정책·`labhq_kb` 색인·브리핑 현실화는 PR B·C다. patch notes는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/intake.py`, `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `labhq/runner/daemon.py`, `labhq/web/ui/decide.js`, `labhq/web/ui/refs.js`, `tests/test_intake_questions.py`, `tests/test_intake_references.py`, `tests/test_followup.py`, `tests/web_clarify_options.cjs`, `tests/web_refs.cjs`, `tests/web_followup.cjs`.

## 2026-10-01 · #90 연구 수행 규약 PR 2 앞부분 — 리뷰 보강

- 결론: verifier는 지지·반박 출처가 모두 확인돼야 claim을 `verified`로 둔다. context 행·추론 행에 든 가짜 ID도 보고 전체를 실패로 만든다. 같은 출처를 ID·doi.org URL·artifact 별칭으로 나눠 독립 근거로 세던 길도 막았다.
- 바뀐 것: `verify.py`는 출처가 가진 식별자(외부 ID 또는 URI, artifact)를 모두 검사해 가장 나쁜 판정을 남기고(`resolutions`), 결함 행을 `defective_evidence`로 모은다. URI는 scheme·host만 대소문자를 무시하고 artifact 경로 구분자는 정규화한다. `claims.py`는 재인용을 출처의 모든 표기로 판정한다. 추론·가설 행은 관찰·조회 행까지 이어져야 하고, `comparable`은 적어 둔 method도 같아야 한다. `accessed_at`은 YYYY-MM-DD만 받는다(3.11+의 주 날짜 차단). 규약 문서 §3·§4를 맞췄다.
- 실행한 것: Codex 독립 리뷰 1회(P2 2건, 둘 다 고침)와 반대 관점 검토(P1 1건·P2 5건 고침). 새 회귀 12건이 수정 전 코드에서 모두 실패함을 확인했다. 수정 후 전체 pytest 1242 passed/19 skipped, `node tests/*.cjs` 7개, `bash scripts/check_public.sh`, `git diff --check` 통과.
- 미해결: 조회는 순차이고 전체 시간 상한이 없다. ID와 URI를 함께 적으면 URI는 검사하지 않는다. 0건 검색을 observed로 적는 우회와 REVIEW_SCHEMA의 R05·R07 판정은 R13 범위다. 셋 다 후속 issue로 넘긴다.
- 근거: `labhq/evidence/verify.py`, `labhq/evidence/claims.py`, `tests/test_evidence_verify.py`, `tests/test_research_evidence.py`, `docs/research_protocol.md`.

## 2026-10-01 · #90 연구 수행 규약 PR 2 앞부분 — 증거 원장 R04–R07

- 결론: 연구 결과 schema가 claim·evidence·link를 따로 받는다. 끊긴 참조, 근거 없는 supported/contradicted, 실패·0건 조회로 지지한 link, 평가·출처·정량 필수값 누락을 한 번에 거부한다. 출처 verifier는 조회 실패와 ID 부재를 나눠 남긴다. 연구 실행은 여전히 opt-in·기본 꺼짐이고 원장·verifier는 실행 경로에 아직 연결하지 않았다.
- 바뀐 것: `labhq/evidence/claims.py`(R04 inco 6종 evidence·revision·참조, R05 directness·source_level·independence_group·판정 이유, R06 조회일·원문 위치·검색 범위, R07 quantity·comparison), `labhq/evidence/verify.py`(lookup succeeded/failed/skipped와 found·not_found·insufficient·conflicting·requires_verification, 고정 응답 `StaticResolver`). result v2의 `findings`를 `claims`·`evidence`·`links`로 바꾸고 `validate_research_result`로 동결 계획에 묶었다. #58의 결과 계약·claim ledger·도구 실패 의미 항목을 이 원장으로 합쳤다. 규약 문서 §3·§4(7,741→10,359 bytes)와 README 한 줄을 고쳤다.
- 실행한 것: 기능 커밋마다 그 테스트를 직전 커밋 코드에서 돌려 실패를 확인했다(R04 23/23, R05 8/8, R06 schema 8/8·verifier 모듈 없음, R07 7/7, 리뷰 보강 4/4). 사전 Codex 리뷰의 P2 3건(scheme 혼동, artifact hash 변경, RefSeq 형식)을 고쳤다. 수정 후 전체 pytest 1230 passed/19 skipped, `node tests/*.cjs` 7개, `bash scripts/check_public.sh` 통과. pytest 임시 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: live resolver가 없고 기본은 조회 꺼짐이다. artifact 근거는 runner manifest(R10)가 생겨야 `found`가 된다. R08–R13, reviewer bundle, 실행 경로 연결은 다음 PR이다. 로컬에 Python 3.10이 없어 3.10은 CI로 확인한다. patch notes는 PR 번호가 생긴 뒤 쓴다.
- 근거: `labhq/evidence/claims.py`, `labhq/evidence/verify.py`, `labhq/research/contract.py`, `tests/test_research_evidence.py`, `tests/test_evidence_verify.py`, `docs/research_protocol.md`.

## 2026-10-01 · 재시작 뒤 consult 겹침 #93 · single_cell_de 조합 규칙 #109

- 결론: gateway가 consult 도중 재시작돼도 같은 ask가 같은 CSO session·workdir로 consult를 하나 더 띄우지 않는다. `single_cell_de@1`의 count scale·model 조합과 결론 모드는 rule로 판정해 맞지 않는 PLAN을 CP1 전에 재계획시킨다.
- 바뀐 것: 미완료 consult도 busy로 본다. 같은 ask의 이전 consult는 같은 runner 세대면 결과를 기다려 쓰고, 아니면 새 session·workdir로 다시 묻는다. 재개 중 roster가 비면 runner 재접속을 기다리고, dispatch 복구는 ask_id로 consult를 맞춘다. adopt 결과의 비용은 task ID별로 한 번만 더하고(병렬 task와 이중 합산 방지), 다른 직원이 돌린 consult는 adopt하지 않아 CSO session이 바뀌지 않는다. transient 실패는 남은 재시도만 돈다. pack rule `when`에 predicate 목록(모두 참)을 허용하고 `model_family` field와 rule 8개를 더했다. README §8, `docs/research_protocol.md` §6을 맞췄다.
- 실행한 것: 수정 전 #93 회귀 5건·리뷰 회귀 3건, #109 회귀(log_transformed + pseudobulk 통과, CP1 직행 포함)가 실패함을 확인했다. 수정 뒤 전체 pytest 1196 passed/19 skipped, `node tests/*.cjs` 7개, `bash scripts/check_public.sh`, `git diff --check` 통과. 로컬 Codex 리뷰 P2 4건(1회차 3, 2회차 1)과 자체 검토 1건은 이 브랜치에서 고쳤고, 수정 전 회귀 3건이 실패함을 확인했다.
- 미해결: 격리된 consult가 끝나면 `cso_session_id`가 그 session으로 바뀌는 기존 동작은 그대로다. cell 의사반복과 조건별 replicate 수는 구조화 field가 없어 rule로 만들지 않았다. `single_cell_de@1` 내용 hash가 바뀌어 이전에 고정한 PLAN은 다시 계획해야 한다(pilot, 기본 꺼짐). patch notes는 push 뒤 따로 쓴다. 후속 P2 3건(재개 중 CSO 비consult task의 busy 검사, facilities 재route, `normalized_counts`가 통과할 조합 없음)은 issue로 넘긴다.
- 근거: `labhq/gateway/server.py`, `labhq/orchestrator/cso.py`, `labhq/research/packs.py`, `labhq/research/contract.py`, `labhq/research/packs/single_cell_de.yaml`, `tests/test_consult_restart.py`, `tests/test_research_protocol.py`.

## 2026-10-01 · 후속 #104 #106 #107 #108

- #104: bench CLI tree 종료 검사를 남은 PID 조건 대기(10초 상한)로 바꾸고 timeout에 PID를 표시한다.
- #106: 같은 step은 running을 hibernating보다 우선하고 같은 상태면 최신 dispatch를 고른다. task board에 HPC 대기와 실행 중 task 취소를 복원한다.
- #107: 403 reset header는 remaining=0일 때만 rate limit으로 보고 reset·Retry-After까지 기다린다. 그 밖의 403은 1회 뒤 failed다.
- #108: 예전 `checks_passed`를 legacy 결과로 보존하고 구조화 채점 세 열은 N/A로 구분한다. report에 `bench rescore --all` 안내를 넣었다.
- 테스트: 수정 전 회귀 5건 실패 확인. 전체 pytest 연속 3회 각 1131 passed/18 skipped, Node 8개, `bash scripts/check_public.sh`, diff 검사가 통과했다.
- 한계: 실제 GitHub rate limit 응답은 호출하지 않았고 MockTransport로 header·대기·재시도를 검증했다. 기존 FastAPI deprecation warning 196건은 남아 있다.

## 2026-10-01 · #90 연구 수행 규약 PR 1 — 2회차 리뷰

- 결론: active pack의 acceptance를 machine rule로 판정한다. 모든 rule을 통과해야 PLAN을 동결하고 CP1을 열며, correction 계획 뒤 budget 거절도 즉시 중단한다. pilot은 여전히 CP1 뒤 멈춘다.
- 변경: pack `rules`는 `when`과 `require|forbid`, predicate의 `value|in|not_in`을 쓴다. loader가 모르는 field·연산자와 충돌하는 rule을 거부한다. acceptance 문자열은 설명으로만 남긴다. `single_cell_de@1`은 완전 혼동 시 비교·설명 가설, inferential statistics, condition-effect estimand을 금지한다.
- 검증: 수정 전 confounded PLAN·잘못된 rule 문법·correction 뒤 budget 거절 회귀가 실패함을 확인했다. 수정 뒤 연구 규약 14 passed, 전체 pytest 1108 passed/18 skipped, `tests/*.cjs` 5개와 `bash scripts/check_public.sh`가 통과했다. `PYTHONPATH`는 clone 루트, state·basetemp는 저장소 밖 임시 폴더를 썼다.
- 한계·인계: claim/evidence 원장·artifact manifest·감사·후행 무효화·CP2–4·연구 E2E 실행은 후속 PR 범위다. 실제 직원 CLI·HPC·live 연구 case는 실행하지 않았다. patch notes는 수정하지 않았다.
- 근거: `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/research/packs/single_cell_de.yaml`, `labhq/orchestrator/cso.py`, `tests/test_research_protocol.py`, `docs/research_protocol.md`.

## 2026-10-01 · bench 구조화 채점·baseline 설정 #92 #101

- 결론: 모든 arm이 같은 case별 JSON 결과 블록을 내고, 문장 표현 대신 그 값으로 채점한다. 형식 실패와 값 오답은 따로 기록·집계한다.
- 바뀐 것: 다섯 case에 key·type·기대값 규칙을 넣고 공통 prompt·mock 답을 갱신했다. 기존 문장 검사는 근거·한계의 보조 판정으로 남겼다. Claude·Codex baseline은 `extra_args`를 일반 adapter와 같은 위치에 넣는다. README에 사용자 동작을 반영했다.
- 실행한 것: 수정 전 회귀 4건 실패. 수정 후 전체 pytest 1099 passed/18 skipped, Node `tests/*.cjs` 5개, `scripts/check_public.sh` 통과. 임시·상태 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: 실제 유료 CLI 실행은 하지 않았다. 구조화 블록 도입 전 저장된 답은 재채점하면 형식 실패이며, 기존 15개 fixture는 보조 문장 검사 근거로만 남겼다.
- 근거: `labhq/bench.py`, `labhq/bench_data/cases/`, `tests/test_bench_structured.py`, `tests/test_bench_rescore.py`.

## 2026-10-01 · 러너 env·POSIX 종료 #83 · 라운드 게시 복구 #97

- 결론: 부모 Claude session marker만 지우고 운영자가 지정한 engine·task env는 보존한다. POSIX timeout은 leader가 먼저 끝나도 남은 process group을 SIGKILL한다. 라운드 기록은 GitHub의 rate-limit 대기 시각을 지키며, 토큰 누락은 재시작 때 다시 게시할 pending 상태로 둔다.
- 바뀐 것: 공용 env 병합 순서를 일반 직원 실행과 MCP 점검에 적용했다. `Retry-After`·`X-RateLimit-Reset`을 보존해 backoff의 하한으로 쓰고 rate limit은 일반 재시도 상한에서 뺐다. README의 engine env·라운드 기록 동작도 맞췄다.
- 실행한 것: 수정 전 #83 env와 #97 rate-limit·token·reset 회귀 4건 실패를 확인했다. 수정 후 관련 94 passed/10 skipped, 전체 pytest 1097 passed/19 skipped, Node `tests/*.cjs` 5개, `bash scripts/check_public.sh`, `git diff --check` 통과. pytest 임시·상태 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: POSIX 자손 종료 회귀는 Windows에서 skip되어 Linux CI 확인이 남았다. 실제 GitHub API와 토큰 교체는 fake API·재시작 상태로 대신했다. 범위 제한에 따라 저장소 밖 HARVEST·LEAD_NOTES와 patch notes는 수정하지 않았고 `.git` 읽기 전용이라 커밋하지 않았다.
- 근거: `labhq/adapters/base.py`, `labhq/util.py`, `labhq/tools/_mcpcompat.py`, `labhq/integrations/github.py`, `labhq/integrations/rounds.py`, `tests/test_isolation.py`, `tests/test_runner_safety.py`, `tests/test_round_records.py`, `tests/test_github_reporter.py`.

## 2026-10-01 · 웹 후속 #75 #87 #88

- 결론: 폰의 고정 직원 줄을 기본으로 접고 결정 이력을 최근 10건부터 요청별로 묶었다. 재접속 snapshot은 200개 이전의 활성 task도 실행 중으로 복원하며, 직원 카드와 3D 승인 시계가 실제 상태를 계속 보여 준다.
- 바뀐 것: **직원 보기** 토글, 결정 이력 **더 보기**, snapshot `running_tasks`, 완료·오류·순서 대기 문구와 오류 색, 3D 1초 갱신을 넣었다. main에 이미 있던 #87 REST 요청 요약의 활성 task 반영과 #88 2.5D 15초 갱신은 중복하지 않았다. README는 정규화 UTF-8 41,583→41,781 bytes(+198)다.
- 실행한 것: issue별 수정 전 Node 회귀 실패 확인. 수정 후 `node tests/*.cjs` 7개, 전체 pytest 1094 passed/18 skipped, `bash scripts/check_public.sh`, diff 검사가 통과했다. 375×812 headless Chrome에서 직원 줄이 접히고 사무실 첫 두 줄이 보였다.
- 미해결: 실제 iPhone·Android의 touch와 Safari 동작은 확인하지 않았다. FastAPI `on_event` deprecation warning 192건은 기존 경고다.
- 근거: `tests/web_issue75.cjs`, `tests/web_issue87.cjs`, `tests/web_command_center.cjs`, `tests/web_live.cjs`, `tests/test_ops_status_usage.py`.

## 2026-10-01 · PR #77 Sonnet 보정·Claude arm 권한 #40

- 판정: Sonnet 5개는 KRAS·GEO·Protein·Penguins 맞음, Plastome 부분/PASS다. 전체 fixture 15개는 맞음 13·부분 2이며 판정-검사 15/15 일치한다. 근거 줄·출처는 `bench/calibration.md`; Sonnet 원본 bytes/SHA-256 5/5 일치다.
- 변경: 표 헤더·단위·행 항목, species 종류 수·n=·EMT 이름·전수 양수 표현을 처리한다. accession 뒤 연도·단계 번호와 원본 전체 설명은 필수 집계와 구분한다. Claude는 acceptEdits·arm 경로·전용 hook으로 안쪽 파일 쓰기/단순 명령을 허용하고 밖 쓰기를 거부한다. 공통 prompt는 answer.md 저장을 요청하며 저장한 보고서를 채팅 요약으로 덮어쓰지 않는다.
- 검증: 전체 pytest 783 passed/18 skipped(TEMP 임시·상태 폴더), fixture 변조·누락·모순 회귀, dry-run argv/실제 hook 안·밖 판정/cwd 검사 통과. `bash scripts/check_public.sh`·신규 파일 공개 검사·diff 검사 통과. 새 의존성은 없다.
- 한계·인계: Native Windows의 Claude OS sandbox 부재로 임의 script/interpreter는 거부한다. Codex workspace-write와 그 실행 권한까지 완전히 같지는 않다(`docs/reference/bench-permissions.md`). 실제 CLI 재실행·결과 폴더 쓰기·patch notes·저장소 밖 노트 수정은 하지 않았다. `.git` read-only라 이번 변경만 `.pr-drafts/commits.json`, 같은 PR 보고는 `.pr-drafts/sonnet-pr.md`에 계획한다.

## 2026-10-01 · PR #77 3회차 P1·bench 예산 정책 #40

- 변경: bench 자료·검사기를 `labhq/bench_data/` package-data로 옮기고 `importlib.resources`로 찾는다. labhq와 baseline에 같은 case/runner timeout을 적용한다.
- 예산: 기본 거절을 유지한다. `--approve-budget-up-to N`은 labhq의 요청 총예산이 원래 case 예산 N배 이내일 때만 승인한다. 표에 승인 횟수·최종 비용/예산 배수를 기록하며 원래 예산 초과는 FAIL이다.
- 검증: 전체 pytest 705 passed/17 skipped(TEMP 임시·상태 폴더). wheel non-editable 설치 후 case 5개·참고 자료·검사기 실행, `bash scripts/check_public.sh`·diff 검사 통과.
- 인계: `.git`은 read-only라 이번 변경만 `.pr-drafts/commits.json`에 계획한다. patch notes·저장소 밖 로컬 노트는 수정하지 않는다. 실행 중인 병렬 단계의 비용까지 제한하는 정책은 아니다.

## 2026-10-01 · PR #77 실제 답 10개로 bench 검사 보정 #40

- 결론: 직접 판정은 맞음 9·부분 1·틀림 0. 부분은 KRAS sol의 추가 동일성 주장에 근거 범위 문제가 있지만 필수 ID·assay 한계를 충족해 PASS로 정했다. 판정과 근거 줄은 `bench/calibration.md`에 있다.
- 바뀐 것: 공개 답 10개를 fixture로 보존했다. 전체값과 하위 집계·다른 열·pair당 값을 구분하고, Markdown 수치·결측 개수/분모/비율·bp/kb 설계 범위를 처리한다. 같은 전체 문맥의 오답·모순은 계속 거부한다.
- 검증: 보정·기존 변형 테스트 111 passed, 전체 pytest 680 passed/17 skipped, mock 5/5 PASS, 원본-fixture SHA-256 10/10 일치, 공개 검사 통과. pytest 임시·상태 폴더는 저장소 밖 TEMP를 사용했다.
- 인계: `.git` 읽기 전용으로 이번 변경만 `.pr-drafts/commits.json`에 계획했다. 같은 보고를 `.pr-drafts/calibration-pr.md`에 남겼다. patch notes와 저장소 밖 로컬 노트·실제 run은 수정하지 않았다. 실제 run 재채점은 후속 작업이다.

## 2026-10-01 · PR #77 bench 검사·재채점 #40

- 결론: 표현 차이로 생긴 FAIL을 줄이고 저장된 산출물을 현재 검사로 다시 채점한다.
- 바뀐 것: `re:`·숫자 추출 비교, 다섯 case의 한국어·영어·단위·띄어쓰기 검사, `bench rescore <case> [--run-id ID | --all]`(기본 최신 run). 이전 판정은 `score_history`에 남기고 실행별·case별 comparison을 갱신한다. 실행 기록과 비용·모델은 보존한다.
- 실행한 것: 새 테스트 44개 포함 전체 pytest 613 passed/17 skipped, case마다 정답 변형 2개·숫자 오류·항목 누락 검사, mock test-agent 5/5와 저장된 mock 재채점, 공개 검사·diff 검사 통과.
- 인계: `.git` 읽기 전용이라 이번 변경만 `.pr-drafts/commits.json`에 남겼다. PR 보고 초안은 `.pr-drafts/rescore-pr.md`. patch notes는 요청대로 수정하지 않았다.
- 근거: `labhq/bench_data/checks/contains_terms.py`, `labhq/bench_data/cases/`, `labhq/bench.py`, `tests/test_bench_rescore.py`.

## 2026-10-01 · #40 비교 bench와 테스트 에이전트

- 결론: PI 결정(2026-10-01, #40)에 따라 기본 arm은 `labhq`, `sonnet-max`(sonnet/max), `sol-ultra`(gpt-5.6-sol/ultra), `astra-ultra`(gpt-6-astra/ultra)다. LabHQ의 Claude 직원은 `opus=sonnet`으로 치환하고 Codex 직원은 설정을 유지한다.
- 바뀐 것: `bench.arms`의 모델·effort 설정, `bench.staff_model`과 `--staff-model`, run·test-agent의 `--arms`, arm별 결과 누적과 `bench report`. 결과표는 실행 당시 모델·effort를 표시하며 real/mock을 따로 모은다. Opus는 설정으로 추가한다. README와 설정 예도 갱신했다.
- 실행한 것: arm 선택·누적 report·모델 치환 회귀 테스트와 전체 `pytest -q -p no:cacheprovider` 556 passed/17 skipped, 네 arm의 `--dry-run` 명령 확인, `bash scripts/check_public.sh` 통과. 임시·상태 폴더는 저장소 밖 TEMP를 썼다.
- 미해결: 실제 유료 CLI는 실행하지 않았다. 네 arm의 비용·token·과학 결과는 PI 머신에서 측정한다. Codex 비용은 `미집계`로 남긴다. `.git` 쓰기 제한으로 이번 변경의 커밋 계획만 `.pr-drafts/commits.json`에 남겼다.
- 근거: `labhq/bench.py`, `labhq/settings.py`, `labhq/cli.py`, `config/labhq.example.yaml`, `tests/test_bench.py`.

## 2026-10-01 · 막힌 단계 같은 세션 재개 #81 · 예외 종료 비용 #67

- 결론: PI 답을 받은 막힌 단계는 같은 session·workdir로 이어 간다(수정 자체는 #76). 예외로 끝난 요청도 최종 비용을 화면에 보낸다.
- 바뀐 것: `request.failed` 예외 경로 payload에 `cost_usd`·`cost_known`(#67). #81은 mock 직원 blocking_decision → PI 답 → 같은 session_id·workdir 재개, resume 없는 엔진은 같은 workdir 새 세션을 E2E로 고정했다.
- 실행한 것: 전체 `pytest -q -p no:cacheprovider` 804 passed/17 skipped(main 병합 뒤), `node tests/*.cjs`, `bash scripts/check_public.sh` 통과.
- 미해결: 실제 직원 CLI로 재개하는 것은 확인하지 않았다.
- 근거: `tests/test_e2e_mock.py`, `tests/test_cso.py`, `tests/web_state.cjs`.

## 2026-10-01 · 후속 P2 네 건 #51 #53 #54 #68

- 결론: 승인 timeout 기록, 자체 변경이 있는 merge의 패치노트 검사, 라운드 게시 재시도, IPv6 link-local 탐색과 doctor 오타 보고를 고쳤다.
- 바뀐 것: 명확화·예산 승인이 시간 초과되면 `timed_out` 결정으로 저장해 라운드 기록 `pi_decisions`에 보인다(#51). `scripts/patch_notes.py`가 git 자동 병합 결과와 달라진 파일이 있는 merge만 검사하고, STATUS.md·패치노트만 정리한 merge와 깨끗한 동기화 merge는 뺀다(#53). `RoundRecorder`가 일시 실패한 GitHub 게시를 상한 있는 backoff로 다시 넣고 영구 거부는 재시도하지 않는다(#54). global IPv6 route가 없으면 interface 열거로 link-local 주소를 찾고, `force_engine` 오타는 doctor fail 행으로 보고한다(#68).
- 실행한 것: issue마다 수정 전 실패하는 회귀 테스트, 전체 `pytest -q -p no:cacheprovider` 807 passed/17 skipped(main 병합 뒤), `bash scripts/check_public.sh` 통과.
- 미해결: GitHub `Retry-After`·rate-limit reset 시각을 backoff에 반영하는 것은 #97로 넘긴다.
- 근거: `tests/test_approval_timeout.py`, `tests/test_patch_notes.py`, `tests/test_round_records.py`, `tests/test_ipv6_interfaces.py`, `tests/test_doctor.py`.

## 2026-10-01 · #39 labhq_ask 질의 경로

- 결론: 직원은 `labhq_ask`로 CSO·시설팀·동료에게 묻고, 코드가 판정한 hard stop만 PI에게 올린다. 답이 늦으면 step을 hibernate하고 같은 session·workdir에서 재개한다.
- 바뀐 것: 5/20/15분 대기, task/대상/요청 3/2/12회 상한, 시설팀→CSO fallback, 동료 read-only consult, 메신저 질문·답변 이벤트, task별 capability token과 다른 task 사칭 403을 넣었다. #38의 묻고 멈추는 게이트도 이 경로를 쓴다.
- 실행한 것: 수정 전 새 테스트가 `AskRequest` import에서 실패함을 확인했다. 수정 후 대상 10 passed, 전체 504 passed/17 skipped, 공개 검사를 통과했다. 실제 직원 CLI는 실행하지 않았다.
- 미해결: 실제 PI 폰·직원 CLI 연동은 mock engine과 fake MCP client로 대신했다. commit 뒤 `patch_notes/README.md` 행을 만들고 amend해야 한다.
- 근거: `labhq/tools/ask_mcp.py`, `labhq/orchestrator/cso.py`, `labhq/runner/approvals.py`, `tests/test_ask.py`, `tests/test_e2e_mock.py`.

## 2026-10-01 · HPC 도구 실패 표시·제출 후 재시도 차단 #80

- 결론: HPC 운영 실패를 MCP 오류로 전달하고, 알려진 pending job이 있는 실패 시도는 자동으로 재실행하지 않는다.
- 바뀐 것: compat `ToolError`, submit·cancel·status·queue 오류 상세, qstat 반환 코드 검사, qacct·PBS 조회 실패와 job 부재 구분, Codex `isError`·`is_error` 처리. PI 거절은 정상 결과에 재제출 금지 안내를 붙인다.
- 실행한 것: 수정 전 회귀 18건 실패(두 번 실행), job 부재 2건 통과. 수정 후 대상 127 passed/9 skipped, 전체 `pytest -q -p no:cacheprovider` 540 passed/17 skipped, `bash scripts/check_public.sh`, `git diff --check` 통과. `PYTHONPATH`는 clone 루트, state·basetemp는 저장소 밖 임시 폴더를 썼다.
- 미해결: PI 거절을 오류로 올릴지와 제출 후 실패의 재개 정책은 보류한다. job 추적 등록 실패·러너 단절로 pending job을 모르는 경우의 중복 방지는 범위 밖이다. 실제 클러스터·직원 CLI·MCP 1.x는 검증하지 않았다. `.git` 읽기 전용이라 커밋·PR 발행 대신 초안을 남겼다. patch_notes와 작업 범위 밖 로컬 인계·수확 파일은 수정하지 않았다.
- 근거: `tests/test_hpc_failures.py`, `tests/fixtures/hpc_failure_server.py`, `tests/test_cso.py::test_failed_attempt_with_submitted_job_does_not_resubmit`.

## 2026-10-01 · 연결된 도구 목록·배지 #63

- 결론: README §2의 연결된 도구 표와 shields.io 배지를 정규직 YAML에서 생성한다.
- 바뀐 것: `scripts/integrations.py --write/--check`, 종류별 항목 수 배지, 직원·공개 출처 링크. 내장 MCP 2·외부 MCP 2·plugin 1·skill 2·엔진 기능 3. README 33,603→37,441 bytes(+3,838).
- 실행한 것: 전체 `pytest -q -p no:cacheprovider` 516 passed/17 skipped, README `--check`, `bash scripts/check_public.sh`, `git diff --check` 통과. 테스트는 `PYTHONPATH`를 clone 루트로, `LABHQ_STATE_DIR`를 임시 state로, `--basetemp`를 저장소 밖 임시 폴더로 지정했다.
- 미해결: 실제 설치·인증·MCP 접속은 검증하지 않았다. 파견직 예시·PI 개인 커넥터·이 clone에 없는 labhq_ask는 제외했다. `.git` 읽기 전용이라 커밋·PR 발행 대신 초안을 남겼으며 patch_notes는 수정하지 않았다. 작업 범위 밖 로컬 인계·수확 파일은 갱신하지 않았다.
- 근거: `agents/core/*.yaml`, `labhq/runner/daemon.py`, `labhq/recruit/paper2agent.py`, `tests/test_integrations.py`.

## 2026-10-01 · HANDOFF 개편: 개발 총괄 교대

- 결론: 개발 총괄을 Claude와 Codex가 번갈아 맡을 수 있게 HANDOFF.md를 지금 기준으로 다시 썼다(PI 결정 2026-10-01).
- 바뀐 것: 시작 순서, 규칙에 더해 겪어서 안 작업 방식, 작업 큐, 최근 PI 결정, 결정 대기, 구조 표. CLAUDE.md·AGENTS.md의 ⛔ 체크포인트 문구를 작업 큐·결정 대기로 바꿨다. 105줄·10,663바이트 → 93줄·8,053바이트.
- 실행한 것: 공개 검사 통과. 문서만 바꿨다.
- 미해결: 로컬 경로·진행 중 작업은 저장소 밖 노트에 있어 공개 저장소만으로는 이어받을 수 없다(의도).

## 2026-10-01 · #57 웹 Command Center 1–5

- 결론: Munder Difflin의 배치만 옮겨 2.5D 오른쪽 Command Center·폰 하단 탭·직원 카드 줄을 만들고, 결정 초안 보존·작업판·결정 이력을 구현했다. 6–8은 범위 밖으로 남겼다.
- 바뀐 것: keyed 결정 카드와 공통 3D 입력, roster 전원 카드와 턴/시간 게이지, step별 시도·산출물·누락·리뷰·취소, 인증된 `GET /api/approvals/history`, allowlist를 쓰는 `/ui/{path}`.
- 실행한 것: 수정 전 regression 4건 실패 확인, 전체 `pytest -q -p no:cacheprovider` 499 passed/17 skipped, `node tests/web_state.cjs`, `bash scripts/check_public.sh` 통과.
- 미해결: 직원별 로그·capability card·사무실 소품 연결(6–8), 픽셀 테마는 하지 않았다. 실제 기기 화면과 스크린샷은 완료 판정에서 제외했다.
- 근거: `labhq/web/ui/`, `labhq/web/state.js`, `labhq/gateway/server.py`, `tests/web_command_center.cjs`, `tests/test_web.py`.

## 2026-10-01 · Codex 직원·개발 라운드 기록 후속 #56 #69

- 결론: Codex 직원의 쓰기·웹 권한을 명시하고 문헌 원 출처 확인을 강화했다. 개발 라운드는 설정한 labhq commit 링크와 종료 조건을 남긴다.
- 바뀐 것: sandbox 기반 roster, 완료된 웹 검색 query parser와 실측 fixture, 직원별 모델·웹·PubMed·bioRxiv 설정, 전용 `CODEX_HOME` 안내, `dev_log.source_repo` 링크와 doctor·HANDOFF 운영 안내.
- 실행한 것: 수정 전 regression 실패 확인. 수정 후 전체 `pytest -q -p no:cacheprovider` 500 passed/17 skipped, `bash scripts/check_public.sh` 통과. mock E2E 이벤트 수집이 중간에 1회 실패했으나 단독·전체 재실행은 통과했다. README 33,738→33,576 bytes(-162).
- 미해결: 실제 Codex 로그인·hosted MCP·GitHub 발행은 실행하지 않았다.
- 근거: `tests/test_real_streams.py`, `tests/test_registry.py`, `tests/test_round_records.py`, `tests/test_doctor.py`.

## 2026-10-01 · #55 러너 안전 경계

- 결론: Windows timeout·취소가 CLI 프로세스 트리를 끝내고, 직원 subprocess는 부모 Claude 세션 마커를 받지 않는다. labhq MCP timeout과 Claude 교차 세션 tool 차단, 원자적 기록, resolved model provenance도 함께 적용했다.
- 바뀐 것: Windows `CREATE_NEW_PROCESS_GROUP`·`taskkill /T /F`와 제한 환경 fallback, Claude env allowlist·doctor 안내, Codex/Claude MCP timeout, `SendMessage`·`ListAgents` deny, registry·계약 YAML·manifest `os.replace`, CLI가 보고한 `model_id`, Windows Python 3.12 CI.
- 실행한 것: 수정 전 회귀 10건 실패를 확인했다. 수정 후 대상 60건과 Windows 손자 프로세스 종료 검사, 전체 `pytest -q -p no:cacheprovider` 501 passed/17 skipped, 공개 검사가 통과했다.
- 미해결: #55의 6번 broker task token은 D 묶음 범위라 건드리지 않았다. 확인되지 않은 `crossSessionInbound` 설정도 넣지 않았다. 실제 Claude/Codex에서 장시간 MCP 승인을 기다리는 probe는 하지 않았다.
- 근거: `labhq/adapters/base.py`, `labhq/adapters/claude_code.py`, `labhq/adapters/codex.py`, `labhq/runner/workspace.py`, `tests/test_runner_safety.py`.

## 2026-10-01 · 후속 #24 #25 #46 #47 가드·doctor

- 결론: plugin hash에 작업 트리의 실행 mode 반영(#24), hostname에 없는 IPv6 LAN 주소 탐색(#25), doctor가 러너의 data·계정 guard와 직원 adapter preflight를 그대로 재사용(#46), percent-encoded token 파라미터 이름도 로그에서 가림(#47).
- 바뀐 것: `labhq/adapters/claude_code.py`, `labhq/cli.py`, `labhq/doctor.py`, `labhq/runner/daemon.py`, `labhq/security.py`와 각 regression test.
- 실행한 것: 수정 전 regression 20건 실패 확인, 수정 후 전체 `pytest -q` 통과, 공개 검사 통과. 작업은 Codex(gpt-6.1-sol)가 했고 커밋·rebase·PR은 Claude가 만들었다.
- 미해결: 실제 HPC·CLI 로그인·LAN 연결은 검증하지 않았다(Windows에서 POSIX host API는 mock).

## 2026-10-01 · 후속 #30–#33 러너·운영

- 결론: npm shim 옆 Node 우선(#30), custom CLI token 보존(#31), 종료 비용으로 웹 합계 동기화(#32), HPC 기상 중 끝난 task를 hibernating 집계에서 제외(#33).
- 바뀐 것: `labhq/adapters/base.py`, `labhq/adapters/cli.py`, `labhq/web/state.js`, `labhq/gateway/server.py`와 각 regression test.
- 실행한 것: 수정 전 새 테스트 9 failed(네 issue 재현), 수정 후 전체 `pytest -q` 통과, 공개 검사 통과. 작업은 Codex(gpt-6.1-sol)가 했고 커밋·PR은 Claude가 만들었다.
- 미해결: 2.5D·3D 화면 확인은 reducer 테스트로 대신했다. 실제 CLI·HPC 실행은 범위 밖.

## 2026-10-01 · #43 라운드 기록

- 결론: 요청이 끝나거나 재시작으로 중단되면 `gateway.state_dir/rounds`에 Markdown·JSON 기록을 남기고, 설정한 private 저장소에는 요청마다 이슈 1건을 갱신한다.
- 바뀐 것: schema v1 기록, 게시 전마다 저장소 공개 여부 확인, 본문이 같으면 재게시 생략. 실행 환경 snapshot(labhq 버전, git commit, 러너 CLI 버전)은 요청을 만들 때 한 번 저장해 복구 때 덮어쓰지 않는다. 러너가 시작할 때 CLI `--version`을 capability로 보내고, 작업 결과에 manifest 요약(실행 시각, 모델, 턴, CLI 버전, plugin)을 실어 gateway가 러너 디스크를 못 읽어도 기록이 비지 않는다. 상위 저장소 안의 plugin도 하위 파일 전체(hook이 부르는 scripts 포함)를 hash한다.
- 실행한 것: Windows `pytest -q` 456 passed/17 skipped(임시 폴더를 저장소 안에 둔 경우도 통과), `scripts/check_public.sh` 통과, Codex 리뷰 5회.
- 미해결: 실제 GitHub 발행은 mock 전송만 검증했다. 기록 저장소(private) 생성은 PI 결정. timeout된 PI 승인을 결정으로 남기는 일은 후속 issue.
- 근거: `labhq/integrations/rounds.py`, `labhq/runner/versions.py`, `labhq/runner/workspace.py`, `tests/test_round_records.py`.

## 2026-10-01 · 패치노트와 README 갱신 규칙

- 결론: 커밋마다 `patch_notes/README.md`에 한 줄, main 커밋 3개 안에 README 갱신을 CI(`patch-notes` job)가 확인한다. 형식은 PI의 튜토리얼 저장소 패치노트와 같다.
- 바뀐 것: `scripts/patch_notes.py`(check·rows), `.github/workflows/test.yml`, 지난 커밋 96개 소급 기록, README 배지·동작 화면(2.5D·3D), 에이전트 규칙(CLAUDE.md·AGENTS.md).
- 실행한 것: `tests/test_patch_notes.py` 3개(임시 git 저장소로 규칙 확인), Windows `pytest -q`, 공개 검사.
- 미해결: 스크린샷은 mock demo 화면이다. UI 개편 뒤 다시 찍는다.

## 2026-10-01 · 문헌 담당 gpt-6-luna 전환

- 결론: `lit_scout`가 Antigravity/Gemini 대신 Codex `gpt-6-luna`로 돌고, Codex 자체 웹 검색을 쓴다.
- 바뀐 것: `agents/core/lit_scout.yaml`, Codex 어댑터(직원 `tools`에 `WebSearch`·`WebFetch`가 있으면 `-c web_search="live"`), 정적 demo roster, README 조직도.
- 실행한 것: Windows `pytest -q` 443 passed/17 skipped, 공개 검사 통과, codex-cli 0.159.2 실제 실행에서 격리 옵션과 함께 `web_search` 이벤트 확인.
- 미해결: Antigravity의 과학 DB skill(dbSNP·ClinVar 등)은 Codex에 없다. 문헌·DB MCP 연결은 #41에서 다룬다.

## 2026-09-28 · #37 #38 #35 보안·재시도

- 토큰 비교를 바이트 constant-time으로 통일하고 gateway 로그의 token 값을 가린다.
- PowerShell 위험 명령과 제한 구역 접근, Bash·PowerShell의 명백한 허용 루트 밖 쓰기를 승인 요청으로 돌린다. 셸 문자열 검사이며 샌드박스는 아니다.
- agy 모델 카탈로그의 관찰된 일시 오류와 재시도 신호를 transient로 분류한다. 인증·권한·정책 오류는 terminal이다.
- 검증: Windows 전체 pytest 410 passed/17 skipped; `bash scripts/check_public.sh` 통과. 실제 CLI·네트워크 장애 재현은 포함하지 않았다.

## 2026-09-28 · #35 doctor 사전 점검

- 결론: `labhq doctor`가 실행 전 기능과 누락 사항을 표로 보여 준다.
- 바뀐 것: 설정·엔진·직원·플러그인·계산 도구 점검, 선택적 네트워크 검사와 상태 디렉터리 manifest.
- 실행한 것: Windows `pytest -q` 375 passed/17 skipped, 공개 검사 통과, 실제 doctor 표·manifest 확인.
- 미해결: 로그인·원격 HPC·스킬 가시성은 비대화형 확인 범위까지만 판정한다.

## 2026-09-28 · #27 결과 보존·연속성

- 결론: 수정 실패 때 첫 성공 결과를 살리고, 단계 산출물과 실패 원인을 최종 보고에 남긴다.
- 바뀐 것: 재시도·수정 작업 공간 재사용, 의존 단계의 산출물 접근, 최대 턴 종료 후 부분 결과 저장, 누락 산출물 판정, CSO 세션 재개. 리뷰는 매번 새 세션이다.
- 실행한 것: Windows `pytest -q` 377 passed/17 skipped, 공개 검사 통과, 터미널 `labhq demo` 완료.
- 미해결: 실제 Claude CLI·HPC 운영 검증과 원본 HARVEST 반영은 남았다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/runner/`, `tests/test_cso.py`, `tests/test_e2e_mock.py`.

## 2026-09-28 · #27 계획·배정 결함 1·2·3·8·9·15

- 결론: 실행 환경과 직원 권한을 반영해 계획하고, PI 답변이 필요한 지점에서 배정을 멈춘다.
- 바꾼 것: 러너 기능·roster 표시, 질문 뒤 1회 재계획, 차단 단계 답변 뒤 재실행, 중복 리뷰 단계 제거, 의존성 검사, 어댑터별 재개 판정.
- 검증: Windows `pytest -q` 364 passed/17 skipped, `bash scripts/check_public.sh` 통과. 임시 파일과 사용자 홈은 작업 폴더 밖의 쓰기 가능 영역으로 지정했다.
- 미해결: 실제 CLI·HPC 연결 검증은 이 mock 테스트에 포함되지 않는다.

## 2026-09-28 · #26 운영 상태·사용량

- 결론: 실행 중인 요청·단계·승인을 확인하고 비용 미집계를 구분한다.
- 바뀐 것: 상태 API/CLI, UTF-8 JSON, 엔진별 token 집계·manifest, 종료 경고, 웹 비용 표시.
- 실행한 것: 대상 96 passed, 전체 335 passed/19 skipped/11 failed(기준 커밋도 같은 11 failed), 공개 검사·Node 상태 검사·Chrome 폰 화면 확인.
- 미해결: 이 환경의 기존 Windows/sandbox 실패 11건. 실제 엔진 과금·중단 시그널의 수동 운영 확인은 남았다.
- 근거: `tests/test_ops_status_usage.py`, `tests/test_real_streams.py`, `labhq/gateway/server.py`.

## 2026-09-28 · Windows 실행·demo 격리 #26

- 한 일: demo의 gateway·runner 상태와 작업·인재·직원 경로를 임시 폴더로 격리했다. Windows npm shim은 Node.js 스크립트로 풀어 실행하고, 풀 수 없는 batch는 실행 전에 거부한다. `prefix_args`와 Windows 설정 안내를 추가했다.
- 테스트: Windows `pytest -q` 342 passed/6 failed/17 skipped (원본 332 passed/10 failed/17 skipped; shebang 4건 해결, 새 실패 0). `bash scripts/check_public.sh` 통과.
- 남은 점: 기존 plugin provenance 3건과 러너 상태 DB 3건 실패. 실제 직원 CLI 실행은 하지 않았다.

## 2026-09-28 · 후속 P2 #19·#21·#22

- 무엇을: `labhq demo --host ::`가 IPv6 주소로 폰 URL을 출력한다(#19). plugin provenance 해시에 git index mode(실행 비트)를 넣었다(#21). `pr_gate.py --dry-run` advice가 병합이 막혀도 현재 head의 P2 목록을 담는다(#22). HANDOFF에 P5 완료와 P3 러너 계정 결정 대기를 적었다.
- 테스트: Windows `pytest -q` 332 passed → 338 passed, 실패는 기존 shebang 4건 그대로. 공개 검사 통과.
- 남은 점: 실제 IPv6 LAN 접속은 미검증. P2 두 건은 후속 issue로 넘겼다(작업 트리 mode, hostname에 없는 IPv6 주소).

## 2026-09-28 · PR 규칙: 자동 병합 게이트 끔

- 무엇을: PI 결정으로 `pr-gate.yml`의 자동 트리거를 없애고 수동 dry-run만 남겼다. dry-run은 상한 없이 병합 조건 충족 여부와 이유만 알린다(`advisory`). 리뷰 깊이와 병합은 Claude가 판단한다: 새 라운드는 직전 수정 확인이나 다른 부류의 결함이 있을 때만, 같은 부류의 좁은 변형은 부류를 닫는 수정 한 번 뒤 병합하고 나머지는 후속 issue.
- 테스트: `tests/test_pr_gate.py` 40 passed. 공개 검사 통과.
- 다음: 없음. 판단이 서지 않는 쟁점만 PI에게 넘긴다.

## 2026-09-28 · P5 공개 가드·Windows 인코딩

- 무엇을: 통제접근 경로를 접근 정책과 같은 후보 추출·정규화(구분자·대소문자·`.`/`..`·file URI 파싱과 percent-decode)로 판정해 해당 줄을 통째로 가리고, 글자에 붙은 구역 경로도 가린다. 네트워크 URL은 두고, `/`·`E:/` 같은 루트 구역이면 GitHub 보고를 끈다. 모든 GitHub 쓰기(제목·본문·코멘트·보고서·커밋 메시지)를 한곳에서 검사하고 branch·sha는 그대로 둔다. 파일 I/O는 UTF-8, CLI 콘솔은 기존 인코딩에서 대체 출력한다.
- 테스트: mock GitHub 전체 흐름·AST 인코딩·cp949 콘솔 검사 통과. Windows 전체 pytest는 기존 11 failed에서 9 failed로 줄었고, 남은 실패 목록은 같다.
- 다음: 병합 뒤 실제 private 테스트 저장소에서 end-to-end 재검증한다.

## 2026-09-28 · P2 bioinfo-agent plugin 연결

- 무엇을: `bioinfo-agent`에 Claude Code plugin `bioinfo`를 `--plugin-dir`로 연결했다. 이 직원만 skill을 허용하고 나머지 개인 설정 격리는 유지한다. 실행 전 plugin 경로와 manifest를 검사한다.
- 근거: 두 실제 init probe에서 skill 허용 시 `bioinfo:bioinfo-analyze`만 로드되고 `--disable-slash-commands` 시 skill은 비었다. 공개 fixture에는 해당 이름만 남겼다.
- 테스트: 대상 71 passed. 전체 Windows `pytest -q`는 기준 커밋과 동일한 7 failed/14 skipped, 260→269 passed(새 실패 0). `bash scripts/check_public.sh` 통과.
- 남은 점: Windows sandbox의 기준선 실패 7개와 PI 머신의 실제 plugin 분석 task·QC handoff 확인.

## 2026-09-28 · demo phone mode

- 한 일: `labhq demo --web --phone`이 LAN 주소의 `/3d` URL을 출력하고, client·runner token을 새로 만들고, 폰 승인을 기다린다. 기본 120초 뒤에만 자동 승인한다. 데모 publish 래퍼가 `runner_id`·`runner_seq`를 넘기지 않아 P1+ ⑤ 이후 `--web` 데모가 멈추던 문제도 고쳤다.
- 테스트: phone mode 8 passed, 전체 `pytest -q` 268 passed/14 skipped/7 failed(Windows shebang 4, 긴 임시 경로 2, 기존 공개 가드 1). `bash scripts/check_public.sh` 통과.
- 바꾼 파일: `labhq/cli.py`, `tests/test_demo_phone.py`, `README.md`, `STATUS.md`.
- 검증: 폰 모드를 실제로 띄워 승인 대기 → 자동 승인, REST로 먼저 누른 거절 반영, 토큰 없는 요청 401을 확인했다.
- 막힌 점: 전체 test 7건은 현재 Windows 환경·기존 코드에서 실패한다. iPhone Safari 실기와 Windows firewall 동작은 미검증.
- 질문: 없음.

## 2026-09-28 · 후속 issue #12·#13

- 무엇을: 공유 표시 규칙으로 3D 완료 포즈·표지를 3초 뒤 대기로 돌리고, 리뷰 요약 누락·파싱 실패·stale 상태도 8·9회에 한 번 경고한다. Fixes #12, Fixes #13.
- 테스트: Windows pytest 11 failed/256 passed/14 skipped → 11 failed/266 passed/14 skipped(새 실패 0). 대상 테스트 63 passed/1 skipped, Node 검사 2개, 공개 검사 통과.
- 막힌 점: Windows 기준선 실패 11개는 그대로다. Linux CI와 실제 브라우저는 미검증.
- 다음: Claude가 PR을 열고 Linux CI와 3D 실화면을 확인한다.

## 2026-09-28 · PR 게이트 실운영 점검

- 무엇을: select 잡의 `PYTHONPATH`를 고정하고 쓰기 권한 부족은 경고로 끝낸다. `select`·`gate` check를 판정에서 제외하되 다른 check가 없으면 보류한다.
- 테스트: Windows 기준선 8 failed/255 passed/14 skipped → 8 failed/259 passed/14 skipped(새 실패 0). 게이트 테스트 29 passed, YAML 파싱·공개 검사 통과.
- 막힌 점: 현재 `origin/main`에는 `scripts/pr_gate.py`가 없어 기본 브랜치 배포 전 select import는 여전히 실패한다.
- 다음: Claude가 기본 브랜치 배포 후 PR 이벤트로 select·gate 재실행을 확인한다.

## 2026-09-28 · PR #10 실제 봇 형식 대응

- 무엇을: 이미지형 P1/P2 배지를 읽고, 현재 head에서 작성된 지적만 `original_commit_id`로 고른다. 요약이 없거나 파싱되지 않아도 10회면 PI를 호출하며 Running은 기다린다.
- 테스트: 원본 `a7e4350`의 Windows pytest 8 failed/251 passed/14 skipped → 8 failed/255 passed/14 skipped(새 실패 0). 실제 봇 fixture를 포함한 게이트 테스트 25 passed, 공개 검사 통과.
- 막힌 점: 실제 GitHub 쓰기 동작은 push 전이라 미검증.
- 다음: Claude가 새 커밋을 push해 PR #10의 판정 결과를 확인한다.

## 2026-09-28 · PR 게이트 상한 정체 해소

- 무엇을: 10회 뒤 새 head를 아직 보지 않은 Completed 리뷰도 `needs-pi`로 보내고, 같은 head의 중복 호출을 막는다. Running은 기다린다.
- 테스트: fast-forward 기준 `bef633e`의 Windows pytest 8 failed/247 passed/14 skipped → 8 failed/251 passed/14 skipped(새 실패 0). 게이트 테스트 21 passed, 공개 검사 통과.
- 막힌 점: 실제 GitHub 이벤트는 push 전이라 미검증.
- 다음: Claude가 새 커밋을 push해 PR #10의 PI 호출 경로를 확인한다.

## 2026-09-28 · PR #10 후속 issue 보존

- 무엇을: P2 후속 issue를 모두 확보한 뒤에만 squash merge한다. 지적 ID marker와 기존 issue의 원문 링크로 재시도 중복을 막는다.
- 테스트: Windows 8 failed/219 passed/13 skipped → 8 failed/223 passed/13 skipped(새 실패 0). 게이트 테스트 17 passed, 공개 검사 통과.
- 막힌 점: 실제 GitHub API 쓰기는 push 전이라 미검증.
- 다음: Claude가 새 커밋을 push해 PR #10에서 동작을 확인한다.

## 2026-09-28 · PR 게이트 후속 수정

- 무엇을: P2만 남은 PR은 👍 없이 병합한다. 배지가 없는 지적은 P1처럼 막는다. CI 완료는 `workflow_run`으로 받고, 30분마다 열린 PR을 재판정한다.
- 테스트: Windows 8 failed/217 passed/13 skipped → 8 failed/219 passed/13 skipped(새 실패 0). 게이트 테스트 13 passed, YAML 파싱·공개 검사 통과.
- 막힌 점: 실제 GitHub 이벤트와 Linux CI는 push 전이라 미검증.
- 다음: Claude가 후속 커밋을 push해 PR에서 트리거를 확인한다.

## 2026-09-28 · PR 리뷰 게이트

- 무엇을: 현재 head의 Codex 리뷰·P1/P2·CI·병합 가능 상태를 판정한다. 합의 시 squash merge와 P2 후속 issue, 8회 경고, 10회 `needs-pi` 호출을 구현했다.
- 테스트: Windows 기준선 8 failed/206 passed/13 skipped → 8 failed/217 passed/13 skipped(새 실패 0). 새 판정 테스트 11 passed. 공개 검사 통과.
- 막힌 점: GitHub 실이벤트·Linux CI는 push 전이라 미검증.
- 다음: Claude가 브랜치를 push해 PR을 열고 Linux CI와 실제 Codex 요약 형식을 확인한다.

## 2026-09-28 · P4 1단계 — 공유 reducer와 실시간 3D
- 무엇을: `state.js`로 상태 처리를 분리하고 `/3d`에 실제 roster·포즈·요청 보드·DOM 승인/거절·since 재연결을 연결했다. 2.5D 전환과 데모 유지. 정적 경로 제한과 배포 에셋 포함.
- 테스트: Windows 기준선 11 failed/203 passed/13 skipped → 11 failed/227 passed/14 skipped(새 실패 0). Node에서 원본 43개 이벤트 상태·재연결·승인 검사, 공개 검사 통과.
- 화면: Chrome 153 headless, 390×844/DPR 2·1280×900. 실시간 승인/거절·roster 추가/삭제·재연결·2.5D 렌더 통과, 콘솔 오류·가로 넘침 0. 데모 9 calls/69,382 triangles 유지, 실제 12명 9 calls/69,538 triangles(+0.22%, 배지).
- 막힌 점: Windows 기존 실패 11개와 symlink 권한 skip. Linux CI·iPhone Safari 실기·wheel 설치 실행은 미검증. Chrome은 샌드박스 제약 때문에 테스트 전용 프로필에서 SwiftShader로 측정했다.
- 다음: Claude가 브라우저 확인 후 push·PR. 신규 production 모듈은 `state.js`와 `lab3d/src/live.js` 2개.

## 2026-09-27 · P1+ 데이터 경계
- 무엇을: 경로 비교·상대 쓰기·설정 검증을 닫고, 통제 구역이 있는 Windows 러너와 원본을 읽는 POSIX 러너의 시작을 거부한다. `hpc.submit_prefix`는 잡 제출·취소에 적용한다.
- 테스트: Windows 기준선 10 failed/60 passed → 9 failed/69 passed/1 skipped. 새 실패 0개. `scripts/check_public.sh` 통과.
- 막힌 점: POSIX 파일 권한 검사는 Windows에서 건너뜀. Linux CI 확인 필요. UNC Claude 규칙은 미실측이라 러너가 거부한다.
- 다음: Linux CI에서 POSIX 권한 테스트와 계정 분리 배치를 확인한다.
- 후속 수정: `docker -v`·`scp`·`rsync`·`file://`처럼 인자에 붙은 경로를 탐지한다. Windows 9 failed/80 passed/1 skipped(신규 실패 0), 공개 검사 통과.
- 리뷰 반영: 선행 `\` 경로를 절대경로로 판정한다. 계정 전환 제출은 `hpc.job_group`과 POSIX 그룹 소속을 확인하고 잡 스크립트·로그 권한을 맞춘다. Windows 9 failed/82 passed/3 skipped(신규 실패 0).
- 2차 리뷰 반영: execute-only 구역도 거부한다. 전환 잡은 `hpc.user`·양쪽 계정의 그룹 소속을 확인하고 `hpc_out/`에서 실행한다. Windows 9 failed/83 passed/4 skipped(신규 실패 0).
- 4차 리뷰 반영(2026-09-27): main의 CSO 변경을 병합하고, 전환 잡 입력의 other 권한과 드라이브 상대경로의 불확실한 쓰기·구역 판정을 닫았다. Windows 9 failed/112 passed/5 skipped(신규 실패 0), 공개 검사 통과.
- 5차 리뷰 반영(2026-09-27): 전환 잡 제출 때 `workspace_root`·날짜 폴더에 group traverse를 주고, 바깥 상위 경로가 막히면 명확히 거부한다. Windows 9 failed/112 passed/7 skipped(신규 실패 0), 공개 검사 통과.
- 6차 리뷰 반영(2026-09-27): Linux CI의 umask 022 테스트 setup에서 부모 폴더를 각각 0700으로 만들고, 생성 직후·거부 후 권한을 모두 확인한다. 제품 코드는 부모 mode를 가정하지 않아 변경하지 않았다. Windows 9 failed/112 passed/7 skipped(신규 실패 0), 공개 검사 통과.
- 7차 리뷰 반영(2026-09-27): 반복 제출 중 workdir의 g+x를 유지하고, 연결된 task 입력은 원본 mode를 바꾸기 전에 거부한다. 명시적 경계에서만 경로를 분리해 `/scratch/data/cohort` 오탐을 없앴다. Windows 9 failed/114 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 8차 리뷰 반영(2026-09-27): Python 3.12 `Path.chmod`의 `follow_symlinks`를 테스트 monkeypatch에서 전달하고 g+x 단언은 유지한다. 같은 형태의 `os.access` wrapper도 원본 인자를 전달한다. Windows 9 failed/114 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 9차 리뷰 반영(2026-09-27): `hpc.submit_prefix`를 `qdel`에도 적용하고, 취소 실패 때 `sudoers`의 `qdel` 권한을 안내한다. `qstat`은 러너 계정으로 조회한다. Windows 9 failed/115 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 10차 리뷰 반영(2026-09-27): 공백이 든 통제 구역을 구조화된 경로·따옴표 셸 경로에서 보존하고, 따옴표 없는 셸 입력은 원문 경계 검사로 놓침을 막는다. Windows 9 failed/116 passed/10 skipped(신규 실패 0), 공개 검사 통과.
- 11차 리뷰 반영(2026-09-27): Bash의 backslash-escaped 공백을 경로로 인식하고, `jobs`의 기존 입력을 private으로 정리하되 이전 잡 스크립트 접근을 유지한다. Windows 9 failed/116 passed/13 skipped(신규 실패 0), 공개 검사 통과.

## 2026-09-26 · P1+ 새 페이지 스냅샷 복구
- 한 일: 웹의 `lastSeq`를 페이지 메모리에만 둔다. 새로 연 페이지는 스냅샷으로 시작하고 같은 페이지의 재연결만 `since`를 보낸다. 예전 localStorage 키는 무시한다.
- 테스트: 정적 회귀 테스트 추가. Windows 10 failed·65 passed → 10 failed·66 passed, 새 실패 0. 공개 검사 통과.
- 막힌 점: 실제 브라우저 재연결은 미검증.
- 다음: Linux CI와 브라우저에서 새로 고침·재연결 확인.

## 2026-09-26 · P1+ 상태 복구·이벤트 재전송
- PR #9 10차: 불확실 task는 같은 runner 세대에만 재전송하고, 완료된 direct 요청은 agent 없이 종결하며, GitHub 계획·리뷰 등의 미게시 이벤트를 재시작 후 전달한다. Windows 10 failed·148 passed(신규 실패 0), 공개 검사 통과.
- PR #9 9차: runner 세대가 바뀐 수락 task는 수동 복구로 종결하고, 같은 세대의 runner 재시작 뒤 추적 중인 HPC job은 재제출 없이 wake로 잇는다. Windows 10 failed·138 passed(신규 실패 0), 공개 검사 통과.
- PR #9 8차: 수락 task의 완료 대기에서 연결 timeout을 빼고, 재시작 후 기존 GitHub issue를 찾아 중복 생성을 막으며 terminal 이벤트를 다음 seq보다 먼저 보낸다. Windows 10 failed·135 passed(신규 실패 0), 공개 검사 통과.
- PR #9 7차: 제어 단계 task와 리뷰 수정 횟수를 재시작 후 이어받고, GitHub 최종 보고·코멘트·닫기 작업을 중복 실행하지 않는다. Windows 10 failed·132 passed(신규 실패 0), 공개 검사 통과.
- PR #9 6차: runner 재시작 task를 실패 결과로 종결하고 불확실한 전송은 같은 ID로 재전송한다. 누락된 GitHub issue를 최종 보고 전에 복구하며 replay-gap 스냅샷은 웹 상태를 교체한다. Windows 10 failed·119 passed(신규 실패 0), 공개 검사·Chrome headless 통과.
- PR #9 5차: HPC 완료 알림은 단계 결과 채택까지 보존하고, 종료 상태·이벤트·GitHub 전달 대기를 한 트랜잭션에 저장해 재시작 때 전달한다. Windows 10 failed·115 passed(신규 실패 0), 공개 검사 통과.
- PR #9 4차: `task.result`는 attempt·revision 이력으로만 저장하고 DAG가 채택한 결과만 완료 처리한다. revision 피드백을 복구하며 runner 수신 ACK와 같은 task ID 재전송·중복 실행 방지를 추가했다. Windows 신규 실패 0, 공개 검사 통과.
- PR #9 3차: CSO main을 병합해 재개도 skip·재시도·리뷰 판정을 쓰게 하고, 늦게 온 task 결과·HPC 완료 복구, 중요 outbox 이벤트 보존, 완료 단계 runner 대기 제외, 교체 소켓 격리, 재개 거절 실패 이벤트를 고쳤다. Windows 신규 실패 0, 공개 검사 통과.
- PR #9 리뷰 반영: 재개 후 과학 리뷰·수정, runner 세대별 seq, 단계 비용, runner 대기, 복원 불가 승인 만료, GitHub issue 번호 복원을 추가. Windows 10 failed·66 passed → 10 failed·71 passed(새 실패 0), 공개 검사 통과.
- 한 일: 게이트웨이 요청·승인·이벤트와 러너 HPC 잡·outbox를 각 SQLite WAL에 저장. 재시작 요청은 `interrupted`로 두고 PI 승인 뒤 남은 DAG 단계만 실행. 이벤트 `schema_version`·전역 `seq`, WS/REST `since`와 보관 범위 초과 스냅샷을 추가.
- 테스트: Windows 기준선 10 failed·60 passed → 10 failed·65 passed, 새 실패 0. UTF-8 mock 통합 흐름 2개 통과. 공개 검사 통과.
- 막힌 점: Windows 기존 실패 10개는 그대로. 헤드리스 브라우저 재연결은 미검증.
- 다음: Linux CI와 실제 브라우저·HPC 재연결 확인. push·PR은 Claude 담당.

## 2026-09-26 · P1+ ④ 직원 CLI 개인 설정 격리
- 한 일: 어댑터가 PI 개인 CLI 설정을 빼고 직원 CLI를 띄운다(`engines.*.isolate_user_config`, 기본 켜짐). `engines.*.env`가 실제로 전달되게 고쳤다(전에는 무시됐다).
- 실측(Windows 11, 실제 계정): 개인 지침에 있는 단어를 묻는 질문으로 확인했다.
  - Claude 2.1.282: 기존 명령은 skill 55·plugin 6·개인 서브에이전트 3·SessionStart hook·전역 CLAUDE.md를 불러왔다. 격리 후 skill 0, 내장 plugin 2, 내장 서브에이전트 6, hook 0, 전역 지침 없음. 같은 질문의 입력 토큰이 약 12k 줄었다.
  - Codex 0.155: `--ignore-user-config --ignore-rules`로 config.toml의 plugin·notify hook·MCP가 빠졌다(입력 24k → 15.5k 토큰). 전역 AGENTS.md는 남는다. `project_doc_max_bytes=0`, `features.agents_md=false`로도 안 빠졌다.
  - Codex on Windows: config.toml을 건너뛰면 `[windows] sandbox`도 빠져 파일 쓰기가 막히는데 종료 코드는 0이었다. `windows.sandbox="elevated"`를 다시 넣어 쓰기를 확인했다.
  - agy 1.2.11: 격리 전에도 전역 지침을 읽지 않았다. `--disable-slash-commands`는 과학 DB skill까지 끌 수 있어 넣지 않았다.
- 가림 보강: Claude `rate_limit_event`에 요금제 사용률·재설정 시각·조직 초과사용 설정이 있었다. main의 Claude fixture 7개에 들어 있던 것을 가리고 구조 테스트를 붙였다.
- 리뷰 반영(Codex 봇 P1): 전역 AGENTS.md가 있으면 Codex 직원 작업을 실행 전에 거부한다(`engines.codex.allow_global_agents_md`로만 허용). 어댑터 파일 입출력을 UTF-8로 고정했다. 한국어 Windows에서 역할 지침 파일을 cp949로 쓰다 죽던 기존 결함이다.
- 리뷰 반영 2차: `isolate_user_config`는 구현된 Claude·Codex에만 둔다(엔진 설정의 모르는 키는 거부). 실측 스크립트도 같은 preflight를 거친다. prepare 이후에 subprocess 환경을 다시 만든다.
- 리뷰 반영 3·5차: 거부 검사와 CLAUDE.md 제외는 자식 CLI가 쓸 수 있는 home 후보 전부(병합된 `HOME`과 `USERPROFILE`)를 본다. `CODEX_HOME`·`CLAUDE_CONFIG_DIR`이 있으면 그것 하나(상대경로는 작업 폴더 기준).
- 리뷰 반영 4차: 실측 스크립트가 `LABHQ_CONFIG`를 읽는다. 설정 파일을 UTF-8로 읽는다(한국어 주석이 든 예시 설정이 Windows에서 cp949로 깨지던 결함).
- 리뷰 반영 6차: Claude는 작업 폴더에서 위로 올라가며 CLAUDE.md를 읽는다. 상위 폴더에 카나리 CLAUDE.md를 두고 격리 세션에 물으니 YES였고, 모든 상위 폴더의 CLAUDE.md·`.claude/CLAUDE.md`를 제외한 뒤 NO가 됐다(실제 계정, haiku, 회당 약 $0.009).
- 규칙: PR 리뷰 답글에 `@codex`를 붙이지 않고, push 뒤 `@codex review`를 한 번만 단다(PI 결정 2026-09-27, 댓글마다 봇 세션이 따로 떴다). CLAUDE.md·AGENTS.md·HANDOFF와 직원에게 주입되는 `ROLE_FOOTER`, README §4를 고쳤다.
- 리뷰 반영 8차: 실측 스크립트의 `--name`은 파일 이름만 받는다(가리지 않은 원본이 `--output-dir` 밖, 예컨대 공개 저장소로 나가지 않게). 예시 설정·`settings.py`의 멘션 안내도 새 규칙으로 맞췄다.
- 테스트: Windows 기존 실패 10개 → 9개(남은 것은 모두 기존 실패), 새 테스트 29개 통과. Linux는 CI.
- 막힌 점: Codex 전역 AGENTS.md는 직원용 `CODEX_HOME` 로그인(PI 조치)이나 러너 전용 계정으로만 빠진다. 직원용 로그인 방식은 미검증.
- 다음: P1+ 나머지 갈래(② CSO, ③ 데이터 경계, ①⑤ 영속화·이벤트 순번) 병합.

## 2026-09-26 · P1+ ② CSO 실패 전파·리뷰·재시도
- PR #7 3차: runner 재연결 신호 대기(기본 30초), skipped 웹 상태, review_unparsed 웹·GitHub 실패 표기를 추가. Windows 8 failed·90 passed(기준선 외 WebSocket 연결 대기 2건), 공개 검사 통과; 헤드리스 DOM 확인 실패.
- PR #7 P2 재검토: 예산 판정·승인을 요청 단위로 직렬화하고 완료 단계는 보존, 미시작 단계만 budget 사유로 skip. Windows 6 failed·89 passed(신규 실패 0), 공개 검사 통과.
- PR #7 리뷰 반영: runner offline·전송 실패는 재시도, 일반 nonzero exit은 중단, 리뷰 스키마 전체 검사. Windows 6 failed·87 passed(기존 실패만), 공개 검사 통과.
- 한 일: 실패·예산 거부·취소의 하위 단계만 skip하고 사유를 보고한다. 리뷰 판정은 엄격히 재요청한 뒤에도 읽지 못하면 `review_unparsed`로 실패 처리한다.
- 설정: `step_max_attempts=2`, `step_retry_backoff_s=0.2`. 재시도 비용도 요청 예산에 합산한다.
- 테스트: Windows 기준선 6 failed·64 passed → 6 failed·80 passed. 신규 실패 0. `scripts/check_public.sh` 통과.
- 막힌 점: Linux CI는 PR에서 확인 필요. 다음: Claude가 PR을 열고 P1+ 다른 갈래와 병합 검토.

## 2026-09-26 · P1 실제 CLI 연동 (Claude·Codex·agy·Gemini)
- 버전: claude 2.1.282 · codex-cli 0.155.0-alpha.16 · gemini-cli 0.57.0 · agy 1.2.11 (Windows 11 호스트, 실제 계정)
- 한 일: 실측 스트림 19개를 가려 `tests/fixtures/real/`에 넣고 파서 테스트를 붙였다. 조용한 실패 세 가지를 막았다: Claude의 `is_error: true`+`subtype: success`, Gemini CLI의 빈 stdout+종료 코드 0, agy의 권한 거부(`denied_actions`에만 남음).
- Codex: `exec` 기본 승인 정책 `never`가 MCP 호출을 실패시킨다(#24135 재현). labhq 내장 MCP 서버에만 `default_tools_approval_mode="approve"`를 붙인다. 승인은 도구 안의 폰 승인이 맡는다.
- Gemini: 개인 계정은 Gemini CLI 지원이 끝났다(`IneligibleTierError`). 새 `antigravity` 엔진을 추가하고 여우 문헌 담당을 옮겼다. agy에는 호출 단위 승인 훅이 없어 통제 데이터에 닿는 직원에게 쓰지 않는다.
- Claude: labhq 게이트웨이·러너를 거친 직접 요청이 정상 응답했다. 승인 도구가 실제 Claude와 호환되지 않던 결함을 고쳤다(mcp 2.x의 구조화 결과 때문에 거부). 수정 후 작업 폴더 안 쓰기는 바로 허용, 밖 쓰기는 폰 승인 요청 → 승인하면 기록, 거부하면 차단을 확인했다.
- Claude deny 규칙: Windows에서는 `Read(//c/Users/...)` 형식만 막힌다. 정책이 `C:\...`를 이 형식으로 바꾸게 고쳤고, 가짜 비밀 파일 읽기가 막히는 것을 실제 계정으로 확인했다.
- 도구: `scripts/probe_engines.py`(실측 재캡처), `scripts/redact_stream.py`(경로·계정·ID 가림, init 이벤트는 허용 목록 필드만).
- 테스트: Linux(WSL Ubuntu 22.04, Python 3.10) 70 passed. Windows는 기존 실패 11개 중 10개가 남았다(Claude 규칙 테스트는 이번 경로 수정으로 통과), 새 실패 없음. agy 어댑터는 실제 계정으로 한 번 돌려 확인했다.
- 막힌 점: 헤드리스 CLI가 PI 개인 설정(hook·서브에이전트·config·전역 지침)을 불러온다. 한 줄 응답에 $0.26이 든 것도 이 때문으로 보인다(UNVERIFIED). P1+에서 격리한다. Codex CLI는 OpenAI 쪽 401 오류로 현재 쓸 수 없다.
- 다음: P1+ 안전·복구 최소선.

## 2026-09-26 · 로드맵 갱신 (PI 결정)
- 한 일: HANDOFF [3]에 P1+ 안전·복구 최소선을 넣었다. P1 다음, P2·P3 전에 상태 영속화·CSO 실패 전파·통제 데이터 경계·CLI 개인 설정 격리·이벤트 순번을 한다.
- P4를 실사용 점검 + 3D 도입으로 고쳤다. 3D 사무실(#3)을 `/3d`로 연결하고 2.5D와 함께 둔다.
- 근거: 세 자문(gpt-6-sol, gpt-6-astra, Gemini 3.8 Flash) 설계 검토. 셋 다 교체가 아니라 수정이라고 판정했다.
- 다음: P1 실제 CLI 연동.

## 2026-09-25 · PR #3 리뷰 수정 · 스킨 scale과 상태 표지 분리

- 한 일: glTF의 named/추정 head·양손·label 앵커를 scene-space proxy로 제공. world 위치·회전만 따라가고 scale은 1로 유지한다. 손 포즈는 원본 노드를 움직이며 proxy는 숨김·dispose도 따른다.
- 회귀 확인: scale 0.01·1·5 × 앵커 유/무, 108포즈 표본 통과. 표지 부품 크기는 procedural 대비 1.0배(오차 <1e-7), world AABB 대각선은 포즈 차이로 0.975–1.031배. 콘솔 오류·외부 요청 0.
- 테스트: Windows: lab3d 10 passed; 전체 11 failed·25 passed, 기존 실패 11개 ID 동일. 기본 procedural 사무실 9 drawCalls/69,382 triangles 유지. `bash scripts/check_public.sh` 통과.
- 바꾼 파일: `src/skins.js`, `lab3d/README.md`, `lab3d/SKINS.md`, `STATUS.md`. 브라우저 QA와 상세 로그는 로컬 `.pr-drafts/p4-3d-artlab/review3/`.
- 막힌 점: 이번 지적 없음. Linux·실기 브라우저는 UNVERIFIED. push·merge는 수행하지 않는다.
- 두 번째 지적: 메시를 움직이는 손 노드·bone을 L/R 이름으로 추정하고, 한쪽이라도 없으면 상태별 전신 포즈를 적용한다. `skin.motion`에 선택 경로를 기록했다.
- 추가 검증: 명시 앵커·이름 추정·L/R 접미사·weighted bone·손 없음·빈 노드 6모델의 실제 정점 이동 통과. scale 108표본·9 drawCalls 유지, pytest 10 passed/전체 기존 실패 11개 동일·공개 검사 통과. 로컬 근거: `.pr-drafts/p4-3d-artlab/review3-motion/`.

## 2026-09-25 · P4 선행 · 3D 아트 랩 (Codex gpt-6-astra, 게이트웨이 미연결)

- 한 일: 원본 종이숲의 12석·캐릭터 형상을 유지해 독립 lab3d로 이관. procedural/glTF 스킨 계약, 공용 상태 표지, 폰 이름표·승인 칩, 파견직 최대 4명과 모자 배지 추가.
- 바꾼 파일: `labhq/web/lab3d/`, `labhq/web/vendor/three/`, `tests/test_lab3d.py`, `STATUS.md`.
- 테스트: Windows Python 3.12: 새 pytest 10 passed. 전체 11 failed · 25 passed로 기존 실패 11개의 ID가 기준선과 같다. 공개 검사 통과.
- 브라우저: Chrome 153 headless · 390×844 CSS px · DPR 2: 12종×6상태, glTF/GLB, 클립·앵커 누락, 실패 복구, 파견직, 폰 탭·승인 순회, 480/481px 경계, reduced-motion 확인. 페이지 콘솔 오류·외부 요청·가로 넘침 0.
- 막힌 점: GPU 자식 프로세스가 샌드박스에서 종료돼 SwiftShader로 측정했다. 수치는 해당 실행의 표본이다. Linux·iPhone Safari 실기 성능/발열·외부 rigged 모델은 UNVERIFIED.
- PI 결정: 3D로 간다. 두 아트 시안 중 gpt-6-astra 시안(캐릭터·배치)을 골랐다. 캐릭터는 나중에 외부 3D 모델로 바꿀 수 있어야 한다.
- 의존성: three.js 0.186.1(MIT)을 `labhq/web/vendor/three/`에 벤더링했다. 세 자문(gpt-6-sol, gpt-6-astra, Gemini)이 모두 권한 방식이다. 되돌리려면 이 커밋 하나를 revert한다.
- 다음: `/3d` 서빙과 본 구현은 P1–P3 뒤 P4에서 한다. 기존 2.5D 사무실과 게이트웨이는 이 PR에서 바뀌지 않는다.
- 근거: 로컬 `.pr-drafts/p4-3d-artlab/`의 스크린샷 3장·검증 로그. PR 첨부는 요청자가 진행한다.

| 화면 | drawCalls | triangles | fps |
|---|---:|---:|---:|
| 사무실 | 9 | 69,382 | 39 |
| 80px 도감 | 6 | 69,316 | 54 |
| CSO glTF 교체 | 13 | 65,694 | 37 |
| 파견직 3명 | 9 | 75,970 | 35 |

## 2026-09-25 · P0+ CI (Codex)
- 한 일: 모든 브랜치 push·PR·수동 실행에서 Ubuntu Python 3.10·3.12 pytest를 돌리는 CI 추가. README에 배지와 저장소 주소 추가.
- 테스트: `bash scripts/check_public.sh` 통과. Windows 11(Python 3.12) `pytest -q`는 main db49ed4와 같은 11 failed · 15 passed. Linux 26 passed는 WSL·CI에서 별도 확인.
- 다음: P1 실제 CLI — 첫 실계정 실행 전 ⛔.

## 2026-09-25 · P0 인수인계 문서 (Codex)
- 한 일: 부록 A 파일 5개 추가, 버전 0.2.0 → 0.2.1, .gitignore에 로컬 전용 노트 파일 2개 추가
- PI 결정: 엔지니어 에이전트는 Codex. 웹 사무실은 최종적으로 3D로 간다 (설계 제안은 별도 PR).
- PI 위임: Codex 리뷰 봇과 PR당 최대 10회 주고받고, 합의된 PR은 Claude가 병합한다.
- 테스트: Linux(WSL Ubuntu 22.04, Python 3.10) 26 passed. Windows 11(Python 3.12)은 main db49ed4와 같은 11 failed · 15 passed.
- 막힌 점: Windows 전용 실패 11개는 기본 인코딩 cp949(4개, PYTHONUTF8=1로 풀림), fake CLI 셸 스크립트 실행 WinError 193(4개), 경로 구분자 가정(3개).
- Windows 경로 문제: 통제 경로 Read가 deny 대신 allow, Claude 권한 규칙이 `Read(/\data\cohort/**)`로 깨짐, 공개 가드가 `/data/cohort`를 가리지 못함. Linux 러너 대상이라 이번 범위 밖이지만 Windows 러너에서는 데이터 구역 가드가 작동하지 않음.
- 다음: P0+ CI PR (이 PR 위에 쌓음)

## 2026-09-25 · P0 GitHub 저장소 (PI, 확인: Claude)
- ehojune/bioinfo-team-3d에 public으로 올림 (main db49ed4, v0.2.0, 파일 62개)
- 확인: main에서 pytest 26 passed, 비밀값·실제 설정 파일 없음 (검사에 걸린 두 곳은 탐지용 정규식 문자열)
- 인수인계본(v0.2.1)과의 차이: 버전 표기와 인수인계 문서뿐, 코드는 같음
- 다음: handoff-docs PR → P0+ CI → P1
- 결정 대기: HANDOFF.md 맨 아래 목록
