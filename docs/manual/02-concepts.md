[← 매뉴얼 목차](../manual.md)

## 핵심 개념

이 문서에 자주 나오는 말입니다.

| 개념 | 한 줄 뜻 | 자세히 |
|---|---|---|
| gateway | 요청·승인·이벤트를 SQLite에 저장하고 웹·폰·CLI를 잇는 서버(`labhq gateway`) | [구조](08-structure.md#구조와-이벤트) |
| runner | 직원 CLI를 실제로 띄우는 데몬. 게이트웨이로 나가는 연결만 쓴다(`labhq runner`) | [구조](08-structure.md#구조와-이벤트) |
| 직원과 엔진 | 직원은 역할·모델·도구를 적은 YAML(`agents/core/`), 엔진은 그 직원을 돌리는 CLI(Claude Code·Codex, 그 밖에 Gemini와 범용 `cli` 어댑터) | [직원](09-staff-tools.md#직원과-도구) |
| CSO·계획·단계 | CSO가 요청을 단계로 나눈 계획(JSON DAG)을 세우고, 단계마다 직원 한 명이 맡는다 | [구조](08-structure.md#구조와-이벤트) |
| orchestrate·direct 요청 | orchestrate는 CSO 계획부터 시작한다. direct(`--agent`)는 직원 한 명에게 바로 간다. `--plan-only`는 계획만 받는다 | [CLI](07-cli.md#명령줄cli) |
| 연구 lane | `research.enabled`일 때 CSO가 연구로 분류한 요청. 고정된 계획과 PI checkpoint를 따른다 | [연구 lane](13-research-lane.md#연구-lane과-검증) · [연구 규약](../research_protocol.md) |
| checkpoint CP1~CP4 | 연구 요청이 PI 결정을 기다리는 관문. CP1 계획·CP2 증거(`evidence_checkpoint`를 켤 때)는 연결됐고 CP4 수용은 `final_checkpoint` opt-in이며 CP3 선택은 아직이다 | [연구 규약 §5](../research_protocol.md#5-pi-checkpoint와-완료) |
| claim·evidence | 연구 단계 결과를 주장과 근거로 나눠 적는 계약(`result v2`). 근거는 실제로 모은 산출을 가리켜야 한다 | [연구 규약 §3](../research_protocol.md#3-직원-결과-계약) |
| domain pack | 분야별 판정 규칙 묶음(`id@version`). 단일세포 DE와 벌크 두 조건 DE pack이 있다 | [연구 lane](13-research-lane.md#연구-lane과-검증) |
| 승인·결정함 | HPC 제출·위험 명령·예산 초과·채용은 PI가 정한다. 웹 결정함에 대기 건과 이력이 모인다 | [안전 장치](11-safety.md#안전-장치) · [웹 사무실](06-web-office.md#웹-사무실) |
| labhq_ask·상담(consult) | 막힌 직원이 묻는 MCP 도구. CSO나 동료가 읽기 전용 상담으로 한 번 답하고, 위험한 것만 PI에게 간다 | [연결된 도구](09-staff-tools.md#연결된-도구) · [설정](15-config.md#설정-포인트) |
| 데이터 구역 | 경로마다 붙는 등급 public·internal·restricted(`policy.data_zones`). restricted 원본은 파일 도구로 열지 않고 HPC 잡 안에서만 다룬다 | [데이터 구역](11-safety.md#데이터-구역) |
| 쓰기 게이트 | Claude 직원은 작업·project·upstream 폴더 안에만 미리 허락된 쓰기를 한다. 그 밖 쓰기는 승인을 거친다 | [쓰기 범위](11-safety.md#승인-게이트와-쓰기-범위) |
| PI 개인 경로 | 같은 계정 실행에서 PI 개인 파일(`~/.ssh`·브라우저 프로필·labhq 설정 등)을 직원에게서 가리는 목록(`policy.private_paths`). Claude 직원은 차단 규칙·게이트·지침 세 겹, Windows elevated sandbox의 Codex 직원은 sandbox 읽기 거부와 지침 두 겹, 다른 엔진 직원은 지침만 받는다. 실행 중에 경로를 만드는 스크립트까지는 막지 못한다 | [PI 개인 경로](11-safety.md#pi-개인-경로) |
| 작업 폴더·manifest·outputs | 단계마다 `runner.workspace_root` 아래 폴더가 생긴다. `manifest.json`에 엔진·모델·세션·비용이 남고, 산출은 `outputs/` 아래만 센다 | [구성요소](18-components.md#구성요소와-상태) · [구조](08-structure.md#구조와-이벤트) |
| 라운드 기록 | 요청마다 남는 개발 기록. `dev_log.repo`(private)에 올릴 수 있다 | [설정](15-config.md#설정-포인트) · [HANDOFF](../../HANDOFF.md) |
| 인스턴스 | `--instance <이름>`으로 한 PC에 연구소를 여럿 띄운다. 설정·state·포트·token은 나뉘고 엔진 로그인·한도는 공유한다 | [설치](04-install.md#설치와-첫-설정) |
| 구독 한도 대기 | 엔진 구독 한도에 걸린 단계를 reset 시각까지 세워 두었다가 자동으로 재개한다 | [구성요소](18-components.md#구성요소와-상태) |
| 의미 모델 | 오프라인 #136 출처 모델·pilot. live 그림자 계열 제거안은 #1388에서 검증 중 | [설명](14-semantic-model.md) |
| ontology·EDAM | PI가 검토한 어휘 125개 key(`labhq/vocab/`)와, 그중 79개 key를 이은 공개 생명정보 ontology EDAM(용어 75개) | [의미 모델](14-semantic-model.md#의미-모델과-온톨로지) · [설정](15-config.md#설정-포인트) |
| 데이터 종류 선언 | CSO가 단계 산출에 data_type·format key를 적는 선택 기능(`plan.declare_output_types`, 기본 꺼짐) | [설정](15-config.md#설정-포인트) |
| 입력–방법 적합성 | 단계 입력의 선언 종류가 그 방법에 맞는지 `fit`·`mismatch`·`unknown`으로 세는 판정. CP1 전 계획 검증에서 쓴다 | [의미 모델](14-semantic-model.md#의미-모델과-온톨로지) · [설정](15-config.md#설정-포인트) |
| 액션 A1·A2 | A1은 기존 액션 7종이 그때 가능했는지 기록만 한다. A2는 PI가 터미널에서 확인한 `request.followup` 하나만 실행한다 | [설정](15-config.md#설정-포인트) |
