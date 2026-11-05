[← 매뉴얼 목차](../manual.md)

## 명령줄(CLI)

웹 사무실 입력창 옆 **작업 종류**(자동 판단·간단한 일·연구)는 연구 lane이 켜져 있을 때 이 요청을 CP1·CP2로 보낼지 정합니다. 자동이면 [접수 규칙](../research_protocol.md)이 정하고, 연구는 팀(CSO)으로만 보낼 수 있습니다. 웹 사무실에서 하는 일은 명령줄로도 됩니다. 설정은 `LABHQ_CONFIG`(또는 `--instance <이름>`)로 고릅니다.

```bash
labhq send "공개 폐선암 scRNA-seq에서 CD276 고발현 세포유형을 찾고 QC까지"   # CSO 오케스트레이션
labhq send --project my-project "새 WGS 배치 표준 QC"                     # 결과를 그 프로젝트 GitHub에도 보고
labhq send --agent analyst "outputs/의 DE 결과로 volcano plot"             # 한 직원에게 직접
labhq send --team "작아 보여도 독립 검토까지 해 줘"                       # 자동 단독 처리를 끄고 팀 강제
labhq send --work-kind simple "GSE10072 시료 메타데이터 표"            # 연구 lane이 켜져 있어도 CP1·CP2 없이(research면 반대로 강제)
labhq send --ref scverse/scanpy --ref doi:10.1038/nature12373 "같은 방식으로 재현"  # 참고 자료 포인터(여러 번)
labhq send --plan-only "같은 roster로 분석 계획만 작성"                   # 실행·과학 리뷰 전에 종료
labhq send --plan-only --cso-model gpt-6-astra "같은 요청의 계획 비교"     # 이 요청의 CSO 모델만 변경
labhq send --dry-run "같은 요청의 호출·비용 미리 보기"                    # 모델·도구·네트워크 호출 없음
labhq note req_123 "표도 함께 만들어 주세요"                            # 실행 중 요청의 다음 단계부터 전달
labhq cancel req_123         # 요청 취소: 도는 작업 중단, 대기·카드 정리, 부분 결과 보고서. 두 번 해도 같음
labhq task steer task_123 "chr1만 보세요"   # 실행 중 직원 제어: steer | pause | resume | halt (#59)
labhq watch                  # 실시간 이벤트. 승인 결과는 허가·거절·수정 요청·시간 초과로 나눠 적음
labhq approvals              # 대기 중 승인 → labhq approve <id> [--deny --note "..."], CP2는 --choice approve|revise|deny(revise는 --note 필수)
labhq approve <clarify-id> --note "1: a, 2: b"  # 질문 카드는 답이 없으면 보내지 않고 요청을 유지
labhq approve <question-id> --note "a"          # 선택지가 있는 직원 질문도 같음. 선택지는 approvals와 거절 메시지에 a) b) …로 나옴
labhq verify <request_id>    # runner PC에서 산출 sha256과 보고서 claim 앵커를 다시 검사(--json, --bundle audit.zip)
labhq report <request_id> --pdf   # 그림이 들어간 보고서를 PDF로 저장(--out 파일). 브라우저가 없으면 HTML과 이유
labhq recruit --repo https://github.com/scverse/scanpy --focus "Preprocessing and clustering" --ttl 14   # 채용 담당 러너가 꺼져 있으면 보내지 않고 409
labhq talent                 # 인재풀
labhq contract extend c_scanpy --days 14    # extend | release | activate | rehire
```

모델 안전장치가 계획을 거부하면 `model_refused` 사유를 저장하고 웹·`status`에 표시합니다. 자동 재시도나 모델 전환은 하지 않습니다. 결정 카드에서 허용된 다른 CSO 모델 또는 수정한 요청 문구를 직접 고르고 승인하면 새 요청으로 계획하며 기존 승인·안전 검사를 다시 거칩니다. ‘그대로 종료’는 새 요청을 만들지 않습니다. 원래 실패 기록은 보존합니다. 이어 가기의 새 계획이 거부된 경우 이전 round 결과·종료 outcome과 거절 원인은 별도로 남기며, 단계 이름이 같아도 이전 결과를 덮어쓰지 않습니다. 이때 다른 모델 선택은 같은 요청·예정 round를 새 계획과 CP1부터 재개하며 누적 비용·예산을 유지합니다. 문구 수정만 명시적으로 새 요청을 만듭니다. 요청·계획·거절 기록이 바뀐 카드는 적용하지 않습니다.

`labhq send`(기다리는 기본 동작)와 `labhq watch <request_id>`는 요청이 끝났다는 줄과 보고서 뒤에, 이어서 오는 요청 묶음 경로와 재현 등급(`요청 묶음:`·`재현 등급:`)까지 출력하고 끝납니다. gateway는 끝났다는 기록을 먼저 저장한 뒤 묶음을 만들기 때문에 이 정보는 별도 `request.bundle` 이벤트로 늦게 오며, CLI는 그 이벤트(또는 요청 기록의 묶음 필드)를 최대 60초 기다립니다. 단계를 하나도 돌리지 않아 묶음이 없는 요청은 기다리지 않고, 60초 안에 끝나지 않으면 "요청 묶음을 아직 만드는 중"이라고 알리고 끝나며 경로는 나중에 `labhq status`에 나옵니다.

`send --dry-run`은 로컬 설정과 직원 YAML만 읽습니다. 브리핑·선행 연구 조사·첫 CSO 계획까지의 호출과 직원별 수를 보여 주고, 모델이 계획을 만들어야 알 수 있는 뒤 단계는 **미정**으로 남깁니다. `agents/core/*.yaml`의 `model_tier`와 파견직의 `recruit.contract_model_tier`는 `fast`·`balanced`·`reasoning` 중 하나입니다. 호출당 범위는 `model_tier_prices` 한 곳에서 읽으며, 공식 출처와 확인일을 적지 않았거나 값이 비어 있으면 **비용 모름**입니다.

`model_tiers`(`config/labhq.yaml`)는 등급(`fast`·`balanced`·`reasoning`)이 엔진마다 어떤 모델을 뜻하는지 적는 표입니다. 기본은 비어 있고, 비어 있으면 등급은 모델을 고르지 않아 모든 직원이 자기 YAML의 `model`로 돕니다(모델명과 가격을 지어내지 않습니다). 값을 적으면 두 가지가 생깁니다. ① 직원 YAML에 `model`이 **없는** 직원은 러너가 그 엔진의 등급 모델로 돌립니다. 직원 YAML에 `model`을 명시했으면 항상 그것이 이기고, task의 `agent_overrides`와 CSO 모델 선택(`cso_model`)도 등급보다 앞섭니다. ② 요청이 끝난 뒤 비용 요약은 **선언한 등급이 뜻하는 모델과 실제로 돈 모델이 다른** 직원을 `model_tier_mismatch:<직원>:<등급>:<실제 모델>`로 알리고, 비용 줄에는 `선언 등급과 다른 모델: lit_scout(fast) → claude-opus-5-5`처럼 나옵니다. 모델명은 별칭과 정식명을 같게 봅니다(`opus`는 `claude-opus-5-5`와 같고, `gpt-6`과 `gpt-6-luna`는 다릅니다). 표에 그 엔진 항목이 없거나 표가 비어 있으면 경고하지 않습니다. 등급은 effort를 바꾸지 않고, `send --dry-run`은 행마다 `(등급·모델)`을 보이며 모델을 정할 수 없으면 **모델 미정**이라고 적습니다. 계획 단계가 자기 `model_tier`를 지닐 수 있는 길(`orchestrator.step_model_tiers`, 기본 꺼짐)은 열려 있지만 **CSO가 등급을 고르게 하는 기능은 아직 없습니다**(#625 2단계의 한 부분이고, 저장된 계획의 값만 따릅니다). 켜면 단계 등급은 직원이 선언한 `model_tier` **이하로만** 낮출 수 있고, 넘으면 서버가 선언 등급으로 되돌리고 계획 경고에 남깁니다. `validation`·`report` 단계는 `balanced` 아래로 내려가지 않으며, 직원에게 선언 등급이 없으면 단계 등급은 무시합니다. 단계 등급은 계획 hash에 들어가 PI가 CP1에서 보고 승인합니다(없으면 기존 계획의 hash는 그대로입니다). 러너는 그 엔진의 `model_tiers` 모델로 단계를 돌리며, 이 값은 직원 YAML의 명시 `model`보다 앞섭니다(낮추는 방향만 허용되므로). 표에 그 엔진 항목이 없으면 바꾸지 않습니다. 비용 기록은 그 단계의 등급을 남겨, 일부러 낮춘 단계를 `model_tier_mismatch`로 경고하지 않습니다.

`labhq codex-review`는 [프로젝트별 GitHub 보고](12-github-reports.md#프로젝트별-github-보고)에, bench·semantics 명령은 [비교 bench](16-bench.md#비교-bench)와 [의미 모델과 온톨로지](14-semantic-model.md#의미-모델과-온톨로지)에 있습니다.

### 과거 요청 검색

`labhq recall "유전자"`로 현재 instance의 완료·실패·취소·거부 요청을 찾습니다. `--instance NAME`도 지원합니다.
client token으로 인증한 `GET /api/knowledge/search?q=유전자`와 같은 결과이며 최대 5건, 본문 합계 2,000자입니다.

- 검색어는 2~200자의 문자 그대로입니다. 3자 이상은 SQLite FTS5 trigram, 2자 또는 trigram 미지원 환경은 최근 색인 2,000건의 LIKE 검색을 씁니다.
- gateway만 자기 `state_dir/knowledge.sqlite3`에 요청 ID·가린 요청 요약·step 상태·`failure_kind`·요청 출처를 씁니다. 로그·산출 파일·원본 reference는 검색 자료가 아닙니다.
- restricted 문장은 marker로 바꾸고 private reference·비밀값은 가립니다. 형식을 판정하지 못한 자료는 제외하며 검색 시에도 현재 가림 규칙을 적용합니다.
- 시작 시 과거 요청을 25건씩 처리하고 checkpoint부터 이어 갑니다. 새 완료 요청은 재처리해도 한 건으로 남습니다. 색인 실패는 요청 완료를 바꾸지 않으며 30초 뒤 또는 다음 완료 때 재시도합니다.
- `labhq doctor`의 `local recall` 행은 색인 수·마지막 성공 시각·LIKE fallback 여부를 보여 줍니다. 검색 결과의 prompt 자동 주입·reflection·registry는 아직 없습니다.

### 요청 수명과 PI 검토

**일부 실패 표지**(#1220): 요청은 단계나 갈래 일부가 실패해도 `완료`로 끝날 수 있습니다. 이때 결과가 실패나 건너뜀으로 기록된 단계가 있으면 상태 라벨이 `완료 · 일부 실패 N`으로 바뀌고, 어느 단계인지 `labhq status`(`일부 실패한 단계: s2, s3`)와 `labhq watch`의 완료 줄에 나옵니다. 웹 요청 목록·상세의 상태와 PDF 보고서 머리말도 같은 라벨을 씁니다. 요청 요약(`/api/requests`)에는 `failed_steps`가 있습니다. 새 상태나 outcome 값이 아니라 요청에 이미 있는 단계 결과에서 그때그때 계산하므로 저장되지 않습니다. 결과가 기록된 단계만 세므로 계획만 하는 요청(`plan_only`)·단독 경로로 바뀐 요청·증거 체크포인트를 끈 계획 승인처럼 계획 단계를 일부러 돌리지 않은 요청에는 붙지 않고, `실패`·`취소됨` 요청에는 붙지 않습니다. 연구 보고서의 한계 절은 단계 id에 갈래 id를 붙여 적습니다(`B1 (갈래 B): 단계 실패 …`). 알림 팝업은 띄우지 않습니다.

runner만 종료·재시작돼도 gateway는 요청을 `중단됨`으로 저장하고 재개 카드를 한 장 엽니다. 종료 중 task 취소와 재시작 복구 결과에도 종료 근거가 남습니다.

이어 묻기도 `interrupted`로 남아 다시 질문을 받습니다. 상담 질의는 중단 이유와 재시도 안내로 대기를 끝내며, 결과 편집은 편집자 runner가 다시 연결되면 자동으로 재개합니다. 작업 취소·gateway 종료의 asyncio 취소는 그대로 전파합니다.

`labhq request mark-interrupted <id>`는 저장된 종료 근거(종료 코드·취소와 종료 중 표시, 또는 실패 직전 10초 안의 gateway 신호 기록)가 일치하는 failed 요청만 정정하고 재개 카드를 엽니다. 이전 상태는 이벤트에 남기며 근거가 없으면 거부합니다.

실행과 보고서 작성 → **보고서 검토 대기** → PI **검토 완료** 순서입니다. 실패·취소·거부도 결과와 이유를 확인한 뒤 마무리합니다. CLI에서는 `labhq review-done <request_id>`를 씁니다. `status`에 검토 대기가 보이며 `send`·`watch`는 보고서 출력 후 종료합니다. gateway는 실행 결과를 보존하고 PI 확인 시각(`pi_reviewed_at`)·감사 기록·`request.reviewed` 이벤트를 함께 저장합니다. 다시 눌러도 중복 기록하지 않습니다.

### 단독 처리

일반 요청 중 한 직원이 약 30분·한 턴 안에 끝낼 조회, 표, QC 한 건, 그림 한 장, 문헌 목록만 CSO가 단독으로 보냅니다. 계획의 DAG는 폴백용으로 그대로 보이며, 단독 답·산출이 비거나 실패하면 `단독 실패 → 팀`으로 이어 갑니다. 연구 lane, HPC, 통제 데이터, 여러 의존 단계는 늘 팀입니다. 웹 **팀으로** 또는 CLI `--team`으로 팀을 강제할 수 있고 `--agent`와 `--team`은 함께 쓸 수 없습니다.

`orchestrator.solo_agent`가 없으면 기능은 꺼집니다. 예를 들어 roster의 `solo` 직원을 아래처럼 연결합니다. `solo_review: true`면 단독 결과도 과학 리뷰 한 번을 거치며, 기본은 `false`입니다.

```yaml
# agents/core/solo.yaml
id: solo
name: 단독 처리 직원
role: 작은 요청 한 턴 처리
engine: codex
model: gpt-6-astra
effort: ultra
```

```yaml
# config/labhq.yaml
orchestrator:
  solo_agent: solo
  solo_review: false
```
