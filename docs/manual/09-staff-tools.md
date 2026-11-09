[← 매뉴얼 목차](../manual.md)

## 직원과 도구

기본 역할 설정은 13개(Claude Code 8개·Codex 5개)입니다. 아래 로컬 `solo`와 파견직은 이 수에 넣지 않습니다. 실제 등록 인원은 설치·로그인·엔진 준비에 따라 다릅니다.

| 캐릭터 | id | 직무 | 기본 엔진/모델 | Codex sandbox | 도구 |
|---|---|---|---|---|---|
| 🦉 부엉이 CSO | `cso` | 질문 설계·업무 배분·증거 통합 (분석 안 함) | Claude Code / opus | — | Read·Glob·Grep만 |
| 🐧 펭귄 비서실장 | `chief_of_staff` | 착수 브리핑: 동향·데이터 접근성·리스크 | Claude Code / sonnet | — | 웹 |
| 🐻 곰 Biology 만물박사 | `biologist` | 가설·메커니즘·교란요인 | Claude Code / opus | — | 웹 |
| 🦦 수달 bioinfo-agent | `bioinfo-agent` | 반복·정형 분석 전담 | Claude Code + bioinfo plugin | — | HPC* |
| 🐿️ 다람쥐 데이터 담당 | `data_steward` | 데이터 확보, 매니페스트·체크섬 | Claude Code / sonnet | — | HPC* |
| 🦊 여우 문헌·헤드헌터 | `lit_scout` | 문헌·파견직 후보 검색 | Codex / gpt-6-luna | workspace-write | live 웹·PubMed·bioRxiv |
| 🦝 너구리 분석가 | `analyst` | 분석 설계·실행 | Claude Code / opus | — | HPC* |
| 🐙 문어 엔지니어 | `engineer` | 파이프라인·도구·테스트 | Codex / gpt-6.1-sol | workspace-write | 웹 disabled·HPC* |
| 🦔 고슴도치 Data QC | `qc_reviewer` | PASS/WARN/FAIL QC | Claude Code / sonnet | — | HPC* |
| 🐢 거북이 과학 리뷰어 | `sci_reviewer` | 3기준 교차 리뷰 | Codex / gpt-6-astra | 자기 outputs/·.tmp/만 쓰기 | live 웹, `labhq_annot`의 `pdb`·`chembl` |
| 카피바라 인사팀 | `recruiter` | Paper2Agent 변환·검증 | Claude Code / opus | — | Skill·Agent |
| 🦁 사자 단독 처리 | `solo` | 한 요청을 계획부터 보고까지 단독 처리 | 로컬 설정 | 로컬 설정 | 로컬 설정 |
| 🐹 시설팀 | `facilities` | 환경 오류 진단·PI 조치 안내 | Codex / gpt-6.1-sol | read-only | 전달된 진단 근거만 |
| 🦫 비버 편집자 | `editor` | 끝난 요청의 결과·재현 방법·출처 정리 | Codex / gpt-6-luna | read-only | 전달된 정리 자료·prompt 점검 규칙 |
| 🐥 파견직 | `c_<slug>` | 논문의 방법 적용 | Claude Code / sonnet | — | 논문 MCP·skill |

PI 2026-10-19 승인 배정 기준([#732](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6263295610)): 표준 생물정보학은 bioinfo-agent/nf-core, 단순 작업은 직접 처리합니다. 없는 복잡한 파이프라인은 직접 코딩한 뒤 기여 여부를 PI에게 묻고 yes일 때만 bioinfo-agent PR을 엽니다. pipeline이 아닌 분석도 같은 데이터·질문의 탐색·전처리·핵심 통계는 직원 사이에 넘기지 않고 한 분석 단계로 계획하며, 데이터셋·질문·근거 갈래가 다른 독립 작업만 나눕니다. 생물학적 해석, 독립 technical·scientific QC 단계, 다른 회사 과학 리뷰, CP1·CP2 승인·근거 검사는 따로 둡니다([#799](https://github.com/ehojune/bioinfo-team-3d/issues/799), 속도·품질 효과는 미측정). 실제 담당은 CSO 계획에서 확인합니다.

엔진·모델·도구는 `agents/core/*.yaml`에서 직원별로 바꿉니다. (예: `engine: codex`, `model: gpt-6-luna`)
단독 처리 직원을 쓰려면 gitignore 대상인 `agents/core/solo.local.yaml`에 `id: solo`, `name`, `role`, `engine`, `model`, `sandbox`, `system_prompt`를 적고 `orchestrator.solo_agent: solo`로 지정합니다. `character`를 생략해도 웹에는 사자로 나옵니다.
Codex 직원은 `tools`에 `WebSearch`나 `WebFetch`가 있으면 `web_search="live"`, 없으면 `"disabled"`를 명시합니다.
`HPC*`는 scheduler가 `none`이 아닐 때만 배선됩니다.

기존 설치는 업데이트 뒤 `runner.agents_dir/core/`에 새 `facilities.yaml`만 추가하세요. 기존 roster는 덮어쓰지 않습니다. PI가 `labhq doctor --json`으로 직원 엔진 준비를 확인하고 작업이 끝난 뒤 runner를 다시 시작하면 등록됩니다. 엔진 준비 검사에 실패한 직원은 roster에서 빠집니다.

### 연결된 도구

정규직 설정(`agents/core/*.yaml`)과 labhq 배선 코드에서 만든 목록입니다. 설치·로그인·네트워크 연결 성공을 뜻하지 않으며, 파견직 예시와 PI 개인 커넥터는 제외합니다.
`scripts/integrations.py`가 직원 설정에서 이 표와 README 배지를 만듭니다.

<!-- integrations:start -->
| 종류 | 이름 | 무엇 | 쓰는 직원 | 출처 |
|---|---|---|---|---|
| 내장 MCP | labhq_annot | 공개 자원 조회: Ensembl VEP·gnomAD·ClinVar·ChIP-Atlas·ENCODE cCRE·GTEx, Europe PMC 본문, OpenAlex·Crossref·Unpaywall 문헌 검색·접근, API 키가 있으면 변이 효과 예측 모델 (판본 기록·캐시) | [analyst](../../agents/core/analyst.yaml), [bioinfo-agent](../../agents/core/bioinfo-agent.yaml), [biologist](../../agents/core/biologist.yaml), [chief_of_staff](../../agents/core/chief_of_staff.yaml), [lit_scout](../../agents/core/lit_scout.yaml), [sci_reviewer](../../agents/core/sci_reviewer.yaml) | [runner](../../labhq/runner/daemon.py) · [직원 설정](../../agents/core/) |
| 내장 MCP | labhq_approval | PI 승인 요청 | [analyst](../../agents/core/analyst.yaml), [bioinfo-agent](../../agents/core/bioinfo-agent.yaml), [biologist](../../agents/core/biologist.yaml), [chief_of_staff](../../agents/core/chief_of_staff.yaml), [cso](../../agents/core/cso.yaml), [data_steward](../../agents/core/data_steward.yaml), [editor](../../agents/core/editor.yaml), [qc_reviewer](../../agents/core/qc_reviewer.yaml), [recruiter](../../agents/core/recruiter.yaml) | [runner](../../labhq/runner/daemon.py) · [직원 설정](../../agents/core/) |
| 내장 MCP | labhq_ask | 막히면 CSO·시설팀·동료·PI에게 묻고 같은 세션으로 이어 가기 (CSO 먼저, 위험한 것만 PI) | [analyst](../../agents/core/analyst.yaml), [bioinfo-agent](../../agents/core/bioinfo-agent.yaml), [biologist](../../agents/core/biologist.yaml), [chief_of_staff](../../agents/core/chief_of_staff.yaml), [cso](../../agents/core/cso.yaml), [data_steward](../../agents/core/data_steward.yaml), [editor](../../agents/core/editor.yaml), [engineer](../../agents/core/engineer.yaml), [facilities](../../agents/core/facilities.yaml), [lit_scout](../../agents/core/lit_scout.yaml), [qc_reviewer](../../agents/core/qc_reviewer.yaml), [recruiter](../../agents/core/recruiter.yaml), [sci_reviewer](../../agents/core/sci_reviewer.yaml) | [runner](../../labhq/runner/daemon.py) · [직원 설정](../../agents/core/) |
| 내장 MCP | labhq_hpc | HPC 제출·감시 (scheduler가 none이 아닐 때) | [analyst](../../agents/core/analyst.yaml), [bioinfo-agent](../../agents/core/bioinfo-agent.yaml), [data_steward](../../agents/core/data_steward.yaml), [engineer](../../agents/core/engineer.yaml), [qc_reviewer](../../agents/core/qc_reviewer.yaml) | [runner](../../labhq/runner/daemon.py) · [직원 설정](../../agents/core/) |
| 외부 MCP | ChEMBL | 화합물·생물활성 검색 | [biologist](../../agents/core/biologist.yaml), [lit_scout](../../agents/core/lit_scout.yaml) | [Claude for Life Sciences](https://www.anthropic.com/news/healthcare-life-sciences) · [MCP](https://hcls.mcp.claude.com/chembl/mcp) · [직원 설정](../../agents/core/) |
| 외부 MCP | ClinicalTrials.gov | 임상시험 검색 | [biologist](../../agents/core/biologist.yaml), [lit_scout](../../agents/core/lit_scout.yaml) | [Claude for Life Sciences](https://www.anthropic.com/news/healthcare-life-sciences) · [MCP](https://hcls.mcp.claude.com/clinical_trials/mcp) · [직원 설정](../../agents/core/) |
| 외부 MCP | Open Targets | 표적–질병 근거 검색 | [biologist](../../agents/core/biologist.yaml), [lit_scout](../../agents/core/lit_scout.yaml) | [Claude for Life Sciences](https://www.anthropic.com/news/healthcare-life-sciences) · [MCP](https://mcp.platform.opentargets.org/mcp) · [직원 설정](../../agents/core/) |
| 외부 MCP | PubMed | 생의학 논문 검색 | [biologist](../../agents/core/biologist.yaml), [lit_scout](../../agents/core/lit_scout.yaml), [sci_reviewer](../../agents/core/sci_reviewer.yaml) | [Claude for Life Sciences](https://www.anthropic.com/news/healthcare-life-sciences) · [MCP](https://pubmed.mcp.claude.com/mcp) · [직원 설정](../../agents/core/) |
| 외부 MCP | bioRxiv / medRxiv | preprint 검색 | [biologist](../../agents/core/biologist.yaml), [lit_scout](../../agents/core/lit_scout.yaml), [sci_reviewer](../../agents/core/sci_reviewer.yaml) | [Claude for Life Sciences](https://www.anthropic.com/news/healthcare-life-sciences) · [MCP](https://hcls.mcp.claude.com/biorxiv/mcp) · [직원 설정](../../agents/core/) |
| Claude Code plugin | bioinfo-agent (`bioinfo`) | bioinfo plugin 로드 (plugin_dirs) | [bioinfo-agent](../../agents/core/bioinfo-agent.yaml) | [직원 설정](../../agents/core/) |
| skill | Paper2Agent | 논문·코드를 파견직으로 변환 (setup-paper2agent 필요) | [recruiter](../../agents/core/recruiter.yaml) | [Paper2Agent](https://github.com/jmiao24/Paper2Agent) · [직원 설정](../../agents/core/) · [채용 코드](../../labhq/recruit/paper2agent.py) |
| skill | bioinfo:bioinfo-analyze | 실행 전 존재 확인 (required_skills) | [bioinfo-agent](../../agents/core/bioinfo-agent.yaml) | [직원 설정](../../agents/core/) |
| skill | graphical-abstract | Claude plugin·Codex .agents/skills (required_skills) | [analyst](../../agents/core/analyst.yaml), [cso](../../agents/core/cso.yaml), [editor](../../agents/core/editor.yaml) | [직원 설정](../../agents/core/) |
| skill | humanizer | Claude plugin·Codex .agents/skills (required_skills) | [analyst](../../agents/core/analyst.yaml), [biologist](../../agents/core/biologist.yaml), [cso](../../agents/core/cso.yaml), [editor](../../agents/core/editor.yaml), [qc_reviewer](../../agents/core/qc_reviewer.yaml) | [직원 설정](../../agents/core/) |
| 엔진 기능 | Claude Code | 직원 실행 엔진 | [analyst](../../agents/core/analyst.yaml), [bioinfo-agent](../../agents/core/bioinfo-agent.yaml), [biologist](../../agents/core/biologist.yaml), [chief_of_staff](../../agents/core/chief_of_staff.yaml), [cso](../../agents/core/cso.yaml), [data_steward](../../agents/core/data_steward.yaml), [qc_reviewer](../../agents/core/qc_reviewer.yaml), [recruiter](../../agents/core/recruiter.yaml) | [직원 설정](../../agents/core/) |
| 엔진 기능 | Codex | 직원 실행 엔진 | [editor](../../agents/core/editor.yaml), [engineer](../../agents/core/engineer.yaml), [facilities](../../agents/core/facilities.yaml), [lit_scout](../../agents/core/lit_scout.yaml), [sci_reviewer](../../agents/core/sci_reviewer.yaml) | [직원 설정](../../agents/core/) |
| 엔진 기능 | Codex 웹 검색 | WebSearch / WebFetch → web_search="live" | [lit_scout](../../agents/core/lit_scout.yaml), [sci_reviewer](../../agents/core/sci_reviewer.yaml) | [adapter](../../labhq/adapters/codex.py) · [직원 설정](../../agents/core/) |
<!-- integrations:end -->

### HPC 도구와 세션 (#1295)

`labhq_hpc`는 `hpc_submit`(제출), `hpc_status`(상태), `hpc_queue`(큐), `hpc_cancel`(취소)을 줍니다. 제출은 PI 승인 정책(11장)을 따르고, 취소는 직원이 `hpc_submit`으로 낸 잡만 됩니다.

설정의 `hpc.site_profile`이 접속 명령(`connect_argv`)이 있는 [사이트 프로필](15-config.md)을 가리키면, runner가 HPC에 **한 번 접속해 셸 하나를 열어 두고** 모든 scheduler 명령을 그 셸에 한 줄씩 보냅니다. 직원의 `hpc_status`·`hpc_queue`·`hpc_cancel`도 도구 프로세스가 직접 접속하지 않고 runner에 요청해 같은 셸로 돕니다. 그래서 상태 조회를 아무리 해도 로그인은 늘지 않고, 연결이 끊겨 다시 열 때만 로그인으로 셉니다(요청당·하루 상한, [15장](15-config.md)). 원격 명령 실행을 무시하는 게이트웨이에서도 셸만 열리면 됩니다.

이때 status·queue·cancel은 전용 broker route를 씁니다. 토큰 신원과 실행 중인 쓰기 가능 task를 확인하며, status·cancel은 같은 직원의 task 또는 요청에서 runner가 제출한 잡만 받습니다. 완료 잡 status는 저장된 상태를 반환하며 queue는 소유 잡만 보여 줍니다. runner 제출 기록이 없는 잡을 scheduler가 반환하면 queue는 불확실성 오류를 냅니다. 제출 결과가 모호하면 빈 queue로 실패를 단정하지 말고 PI가 확인할 때까지 재제출을 멈춥니다. status 대기 한도에는 두 명령 각각의 줄서기·재접속·실행 시간을 넣습니다. [route·시간 계산](../reference/hpc-broker-routes-1307.md)을 참고하세요.

- **명령 하나는 시간 제한과 출력 크기 상한**이 있고, 한 번에 하나씩 처리합니다. 시간이 지나면 셸을 닫고 다음 명령이 새로 접속합니다. 도구 호출이 다른 명령 뒤에서 60초 넘게 기다리면 그 명령은 보내지 않고 실패합니다(포기한 명령이 나중에 실행되지 않습니다).
- **유휴 잠김 전에 가벼운 명령**(`true`)을 보내 연결을 살려 둡니다. 간격은 프로필의 `idle_lock_minutes`보다 짧게(그 값의 4분의 1, 최대 15분) 잡습니다. 열린 잡이 없고 15분 동안 쓰지 않으면 셸을 닫습니다.
- **다시 접속하는 횟수에 상한과 대기 간격**이 있습니다(3번, 5초에서 두 배씩). 다 쓰면 1분 동안은 로그인 없이 실패만 돌려줍니다. 로그인 상한에 닿았으면 접속하지 않고 웹 피드에 알립니다.
- **알려야 할 일은 웹 피드 경고로 뜹니다**: host key가 바뀌었을 때, 접속 통로를 사람이 열어야 할 때, 다시 접속하지 못했을 때. 복구 절차는 [15장](15-config.md)입니다.
- **파일은 두 가지로 다룹니다.** 사이트 프로필에 `remote_workdir`가 없으면 PC와 로그인 노드가 같은 경로를 본다고 보고 스크립트·로그 경로를 그대로 씁니다(공유 파일시스템). `remote_workdir`가 있으면 파일시스템을 공유하지 않는 사이트로 보고, 같은 셸로 **스크립트(와 작은 입력)를 올리고** 끝난 잡의 **로그와 작은 결과를 가져옵니다**. 올릴 때마다 클러스터에서 계산한 sha256이 같을 때만 제출하고, 가져온 파일은 sha256을 `jobs/logs/<스크립트>.fetch.json`에 적습니다. 올리기는 파일당 512KiB, 가져오기는 파일당 1MiB(로그는 끝부분)·잡당 8MiB까지이고, 넘는 파일은 가져오지 않고 알립니다(사이트 프로필의 전송 방법으로 직접 옮기세요). 경로는 `remote_workdir` 아래로만 허용하고 `..`·절대 경로·링크로 나가는 경로는 거부합니다. 잡마다 클러스터에 **새 폴더**(`<remote_workdir>/<task>/job-<무작위 12자>`, 권한 700)를 `mkdir`(`-p` 없이)로 만들어 그 안에만 쓰므로, 미리 깔아 둔 링크나 파일이 있을 수 없고 다른 잡과 폴더를 나누지 않습니다. 로컬 입력은 폴더마다 링크를 따라가지 않고 연 핸들에서 읽고(Windows는 같은 핸들이 열려 있는 동안 폴더가 바뀌지 않게 잡습니다), 같은 핸들에서 크기와 종류를 확인합니다. 이 전송에는 로그인이 더 들지 않습니다.

### 공개 자원 조회 도구

`labhq_annot`은 변이·영역·유전자와 논문을 공개 DB에서 조회하는 내장 MCP입니다(#435, #697, #720, #744). `builtin_mcp`에 `annot`이 있는 직원(analyst·bioinfo-agent·biologist·lit_scout, 그리고 도구를 좁힌 chief_of_staff·sci_reviewer)에게 붙습니다. 이어 묻기·상담·리뷰 같은 읽기 전용 실행에는 기본으로 붙지 않고, 직원 파일이 `annot_tools`에 명시한 도구 중 읽기 전용에 허용된 `pdb`·`chembl`만 예외로 붙습니다. 지금 이 예외를 받는 직원은 sci_reviewer입니다.

직원 파일의 `annot_tools`로 그 직원이 받는 도구를 좁힐 수 있습니다(#1200). 없으면 서버의 도구를 전부 줍니다. `annot_tools: [fulltext, unpaywall]`처럼 이름을 적으면 그 도구만 주고, 빈 목록 `[]`은 하나도 주지 않습니다. 서버가 모르는 이름이 있으면 `labhq_annot`이 시작하지 않아 오타가 도구를 조용히 열거나 닫지 못합니다. 엔진과 관계없이 서버가 적용하고, 직원 명단(CSO가 계획에 쓰는 글)에도 실제로 받는 도구만 나옵니다. `sci_reviewer`(과학 리뷰어)는 `pdb`와 `chembl`만 받습니다(#1055). 구조·화합물 주장을 확인할 때 셸에서 RCSB·ChEMBL을 직접 부르지 않고 이 도구로 조회해야 요청 조회 한도에 세어지고 원본 응답이 남습니다. 도구를 주는 것이 셸 HTTP를 막는 것은 아닙니다. 읽기 전용이고 웹 도구가 없는 Codex 직원의 셸에는 닫힌 프록시(`http://127.0.0.1:9`)를 환경 변수로 주어 `urllib`·`requests`·`curl` 같은 기본 클라이언트의 우연한 외부 조회가 실패하게 합니다(#1055). 프록시 변수를 무시하는 코드는 막지 못하는 실수 방지이고, 경계는 #666입니다. 기본 `chief_of_staff`는 문헌 도구 7개(`fulltext`, `key_fulltext`, `citation_chase`, `preprints`, `openalex_works`, `crossref_doi`, `unpaywall`)만 받으며, 새 조회 도구가 생겨도 목록에 넣기 전에는 닫혀 있습니다. 읽기 전용 실행(리뷰·이어 묻기·상담)은 기본으로 `labhq_annot`을 받지 않습니다. 예외는 하나입니다. 직원 파일이 `builtin_mcp`에 `annot`을 적고 `annot_tools`에 이름을 명시했을 때, 그중 읽기 전용에 허용된 도구(`pdb`, `chembl`)만 남습니다(`READ_ONLY_ANNOT_TOOLS`, #1055). 직원 파일의 목록은 이 허용 목록을 좁히기만 하고 넓히지 못하며, 보내는 쪽의 override로도 바뀌지 않습니다. 허용 목록을 넓히는 것은 코드에서 따로 정하는 일입니다. runner가 서버에 넘기는 환경 변수 `LABHQ_ANNOT_TOOLS`(`*`은 제한 없음)는 직접 설정하지 않습니다.

읽기 전용 실행(검토·블라인드 초안·상담·이어 묻기·슬롯 보정)은 직원 프롬프트가 쓰라고 한 도구 중 일부를 쓸 수 없습니다. 그래서 읽기 전용 프로필이 프롬프트 끝에 `## Tools in this read-only run`과 이번 실행에서 실제로 쓸 수 있는 도구(Claude는 파일 도구, Codex는 `outputs/`·`.tmp/`에만 쓸 수 있는 읽기 전용 셸, Codex 직원이 유지한 웹 도구, 허용 목록에 든 `labhq_annot` 도구)를 한 번 붙이고, 그 밖의 도구는 부르지 말고 확인하지 못한 것을 말하라고 알립니다(#1224). 직원 파일이 `disallowed_tools`로 통째로 막은 도구(`Write` 등)는 목록에서 빠지고, 일부만 막은 도구(`Read(./data/**)`)는 남습니다. 직원마다 프롬프트를 따로 고치지 않아도 됩니다.

| 도구 | DB | 받는 것 | 판본 기록 |
|---|---|---|---|
| `vep(variants, assembly, species)` | Ensembl REST VEP (사람 GRCh38은 AlphaMissense 플러그인 포함) | `chr:pos:ref:alt`·HGVS·rsID | `/info/data`의 Ensembl release (GRCh37은 grch37 서버). AlphaMissense는 `source.additional_sources`에 라이선스와 인용 |
| `gnomad(variants, dataset)` | gnomAD GraphQL | `chr:pos:ref:alt`·rsID. chrM은 mtDNA 전용 query로 보냄(heteroplasmy 포함) | dataset ID (예: `gnomad_r4`) |
| `clinvar(variants_or_ids, assembly)` | NCBI E-utilities ClinVar | 위 셋 + VCV 번호·Variation ID | einfo의 build와 last update |
| `chipatlas(genes_or_regions, genome, antigen_class, cell_class, threshold, distance_kb, permutations, wait_s, request_id)` | ChIP-Atlas enrichment analysis (DDBJ WABI) | 유전자 목록(배경: RefSeq 전체) 또는 영역 목록(배경: random permutation). 섞으면 다른 쪽이 항목 오류 | `experimentList.tab` 갱신일 |
| `chipatlas_targets(antigens, genome, distance_kb, genes, top)` | ChIP-Atlas target genes 표 | 단백질 이름(예: POU5F1), TSS ±1·5·10 kb | 표 파일의 갱신일 |
| `encode(regions, assembly)` | ENCODE cCRE registry (GRCh38·mm10) | 영역 `chr:start-end`(1부터, 닫힌 구간)·`chr:pos` | registry 판본·annotation·파일 accession·공개일 (예: V4, ENCSR800VNX, ENCFF420VPZ) |
| `gtex(genes_or_variants, tissues, dataset, eqtl)` | GTEx Portal API v2 | 유전자 기호·Ensembl ID, `chr:pos:ref:alt`(GRCh38)·rsID | dataset 이름·GENCODE·dbSNP (예: GTEx Analysis v10, GENCODE v39) |
| `pdb(ids_or_queries, top)` | RCSB PDB (Data API·Search API) | PDB ID(4자), 그 밖의 문자열은 전문 검색, 한 번에 25개 | 응답의 `revision_date`(API가 판본 번호를 주지 않음), 라이선스 CC0 1.0 |
| `chembl(targets_or_compounds, kind, limit)` | ChEMBL REST | `kind` `target`·`compound`는 ChEMBL ID나 검색어, `activity`는 target ChEMBL ID만, 한 번에 25개 | `status.json`의 `chembl_db_version`, 라이선스 CC BY-SA 3.0 |
| `alphagenome(variants_or_intervals, outputs, ontology_terms, sequence_length, organism)` | Google DeepMind AlphaGenome (공식 `alphagenome` client) | `chr:pos:ref:alt`·`chr:start-end` (hg38·mm10) | client 판본과 기본 모델(ALL_FOLDS) |
| `alphagenome_variant(variants, sequence_length, ontology_terms, tissues, top)` | AlphaGenome 권장 변이 scorer | `chr:pos:ref>alt` 또는 `chr:pos:ref:alt` (GRCh38) | SDK·API v1·ALL_FOLDS·scorer 설정 |
| `fulltext(ids)` | Europe PMC → Unpaywall·OpenAlex 합법 OA 위치 | PMID·PMCID·DOI, 한 번에 20편 | REST 판본과 실제 읽은 출처. 답은 30일 캐시 |
| `key_fulltext(papers)` | 허용 OA → 기관 세션 → PI 제공 PDF | 결론 핵심 논문 최대 20편. 제목·DOI/ID·핵심 이유·필요 절/표·시도 경로 | 본문 확보 경로·PDF 요청 상태·접근 실패 원장 |
| `citation_chase(paper_id, limit)` | Europe PMC REST references·citations | PMID·PMCID·DOI 한 편. 앞·뒤 방향별 최대 1,000편 | REST 판본. 답은 30일 캐시 |
| `preprints(query, limit)` | Europe PMC REST `SRC:PPR` | bioRxiv·medRxiv 검색어. 최대 100편 | REST 판본. 답은 30일 캐시 |
| `openalex_works(query, filters, limit)` | OpenAlex Works | 검색식·filter 목록. 최대 200편 | API 응답과 검색 상한. 답은 30일 캐시 |
| `crossref_doi(dois)` | Crossref REST | DOI 최대 50개 | version·license·relation·update-to·철회. 답은 30일 캐시 |
| `unpaywall(dois)` | Unpaywall API v2 | DOI 최대 50개 | 합법 OA 위치. 답은 30일 캐시; email 미설정 시 비활성 표시 |

**AlphaGenome Atlas — [PR #1186](https://github.com/ehojune/bioinfo-team-3d/pull/1186), 2026-10-31 병합**

- 직원은 `labhq_annot`의 `alphagenome_atlas(variants, requested_scorers, ontology_terms, gene_ids, top, assembly)`로 사전 계산 점수를 조회합니다. 별도 CLI 하위 명령은 없습니다. 입력은 GRCh38 SNV `chr:pos:ref>alt` 1–10개이며 scorer 기본값은 `RNA_SEQ`입니다. scorer·ontology·gene 필터를 지원하고 raw/quantile 점수·metadata·출처·사용 조건을 반환합니다. 미수록·빈 결과를 무효과로 해석하거나 자동 inference로 넘기지 않습니다.
- Atlas는 호출마다 현재 키로 조회하며 기존 디스크 캐시를 읽거나 새 캐시를 쓰지 않습니다. 한 호출 안의 중복 변이만 합치며 ontology 누락값은 필터에서 제외하지 않습니다.
- runner의 `ALPHAGENOME_API_KEY`와 `alphagenome>=0.10.0`이 필요하며 기존 키 파일은 쓰지 않습니다. 키·별칭은 직원 환경에서 제거합니다. 기존 요청 승인 범위 안에서 `annot` 허용 역할에만 task 조회 권한을 주며 읽기 전용 task·Atlas deny는 거부합니다. task 종료·오류·취소 때 권한을 회수하고 이후 호출은 거부합니다. 진행 중 SDK 호출의 즉시 종료를 보장하지는 않습니다. 요청 전체 조회 상한이 켜져 있으면 Atlas도 거부합니다.
- 켜는 법: Atlas는 runner 프로세스의 환경 변수 `ALPHAGENOME_API_KEY`만 키로 씁니다. `labhq init`이 저장하는 키 파일(추론 도구용)과는 별개이고 설정 파일에 적지 않습니다. 키가 화면과 명령 기록에 남지 않게 Windows 사용자 환경 변수로 넣으려면 PowerShell에서 한 번만 이렇게 합니다(입력은 가려지고 값은 어디에도 출력되지 않습니다).

```powershell
$k = Read-Host 'AlphaGenome API key' -AsSecureString
[Environment]::SetEnvironmentVariable('ALPHAGENOME_API_KEY', [System.Net.NetworkCredential]::new('', $k).Password, 'User')
Remove-Variable k
```

  `setx ALPHAGENOME_API_KEY <키>`처럼 키를 명령줄에 쓰면 기록에 남으니 쓰지 마세요. 키를 issue·방·로그에 붙여 넣지 마세요. 새 터미널을 열어 `labhq down` 뒤 `labhq up`으로 runner를 다시 시작해야 반영됩니다. POSIX에서는 Bash나 zsh에서 `read -rs ALPHAGENOME_API_KEY && export ALPHAGENOME_API_KEY`로 그 셸에만 넣고 같은 셸에서 runner를 시작합니다(dash 같은 `/bin/sh`의 `read`에는 `-s`가 없어 이 줄이 실패합니다. 그 셸에서는 `trap 'stty echo' INT TERM; stty -echo; read ALPHAGENOME_API_KEY; stty echo; trap - INT TERM; [ -n "$ALPHAGENOME_API_KEY" ] && export ALPHAGENOME_API_KEY`를 씁니다. `trap`이 있어야 입력 중 Ctrl-C로 끊어도 화면 입력 표시가 돌아오고, 마지막 `[ -n ... ]`이 끊긴 뒤 빈 값을 내보내지 않게 합니다). SDK(`alphagenome>=0.10.0`)는 runner가 쓰는 가상환경에 설치합니다. 가상환경을 켜 두지 않는 설치 방법이라 활성화에 기대지 말고 그 python을 직접 부르세요: Windows는 `.\.venv\Scripts\python.exe -m pip install -e ".[alphagenome]"`, POSIX는 `./.venv/bin/python -m pip install -e ".[alphagenome]"`. `labhq doctor`의 `AlphaGenome Atlas` 행이 `환경변수 키·SDK 있음`이면 준비된 것이고, 이 행은 실제 접근 여부까지는 확인하지 않습니다. Atlas를 쓰는 직원은 `annot`을 받는 직원 중 `annot_tools`가 없거나 목록에 `alphagenome_atlas`가 든 직원(analyst·bioinfo-agent·biologist·lit_scout)이고, `chief_of_staff`와 읽기 전용 `sci_reviewer`는 받지 않습니다.
- 시작 점검: launch 설정(엔진 인자·prefix·MCP 서버 환경·Claude 설정)에 키 **값**이 있거나 환경 변수 이름 `ALPHAGENOME_API_KEY`가 어떤 모양으로든 있으면(설정·목록·참조·`Env:\` 경로 포함) 실행을 시작 전에 `Atlas credentials/config overrides…`로 거절합니다. 예외는 설정된 키 파일 경로(`annot.alphagenome_key_file`: `~` 펼침·절대·실제 경로)와 labhq가 그 경로로 만드는 비공개 경로 목록·Claude 규칙뿐입니다. 그래서 `labhq init`으로 키 파일을 둔 PC에서도 `annot`을 쓰는 직원은 정상으로 실행됩니다(#1225). 파일 이름만 적은 상대 경로 설정은 예외가 되지 않고, 키 파일이 아닌 다른 경로에 같은 이름이 들어 있으면 거절합니다.
- 격리 설치본의 CLI 도움말·MCP stdio·SDK mock 왕복과 권한 회수 후 403을 확인했습니다. 의존성은 기존 승인 설치본을 복사했습니다. **실제 Atlas 조회·설치 Codex sandbox shell은 UNVERIFIED**입니다. 검증은 새 서비스·연구 실행 승인이 아닙니다. [설치 근거·검증 한계](../reference/alphagenome-atlas-621.md#installed-package-verification-rev12).

- 형식이 틀린 항목은 그 항목만 오류로 돌아오고 나머지는 조회합니다. 항목 오류는 조회 실패이고, `found: false`는 조회에 성공했는데 DB에 없다는 뜻입니다.
- 한도: VEP는 200개씩 나눠 보내고, 429면 `Retry-After`만큼 기다려 다시 보냅니다(최대 5회, 2분이 넘는 대기는 실패로 돌려줌). gnomAD는 분당 10회라 한 호출에 새 변이 50개까지, ClinVar는 초당 3회로 보냅니다. 속도 조절은 도구 프로세스마다 따로라 여러 직원이 동시에 부르면 합이 한도를 넘을 수 있고, 그때는 재시도가 받습니다.
- ClinVar에 `chr:pos:ref:alt`를 주면 VCF padding base부터 ref 끝 다음 base까지 구간으로 찾습니다. ClinVar는 indel을 처음 바뀐 base에 색인하기 때문입니다. record마다 `match`가 붙습니다: `exact`(같은 allele, repeat 안에서 위치가 달라도 같은 변이면 exact), `other_allele`(같은 구간의 다른 변이), `not_compared`(GRCh37이라 비교 못 함). GRCh38에서 같은 allele이 없으면 `exact_match: false`와 그 뜻이 붙습니다. VCF는 left-normalise된 것으로 봅니다.
- 결과마다 `source: {db, release_or_version, url, queried_at, request_sha256}`가 붙습니다. 같은 내용이 작업 폴더 `outputs/annotation_queries.jsonl`에 한 줄씩 쌓이고(변이 목록은 넣지 않고 개수와 hash만), DB 응답 전체는 `outputs/annotation/<도구>-<요청 hash>-<내용 hash>.json`에 남아 요청 묶음에 들어갑니다. 같은 요청을 다시 해도 앞 파일을 덮어쓰지 않고, 로그 줄의 `result_sha256`으로 파일을 확인할 수 있습니다.
- 캐시: 같은 (도구, 판본, 옵션, 입력)의 답은 `runner.state_dir/annot_cache`에서 다시 씁니다. 판본이 바뀌면 다시 묻습니다. gnomAD와 문헌 API 답은 30일 뒤 다시 조회합니다. 일시 오류는 저장하지 않습니다.
- 변이 효과는 `alphagenome_variant`를 씁니다. 발현·스플라이싱·접근성·histone·TF·contact 등 설치된 SDK의 권장 차이 scorer로 점수를 구합니다. 기본·최대 창은 1MB이고 창은 REF 구간의 가운데에 맞춥니다(결실처럼 REF가 여러 염기여도 한쪽으로 치우치지 않습니다). 호출당 변이 10개·allele 1,000 bp까지입니다. 한국어 요약과 `abs(quantile_score)` 상위 `top`행(기본 20, 최대 100)을 돌려주고 파일·캐시에는 상위 1,000행과 잘림 여부를 남깁니다. 부호 있는 점수만 증가·감소로 설명하며 절대 차이는 방향 미제공입니다. `|quantile| ≥ 0.99`는 큰 효과 후보의 표시 기준일 뿐 임상 근거가 아닙니다. `ontology_terms`·`tissues`는 각각 최대 20개이며 **조회 후 필터**라 호출량은 줄지 않습니다. SDK·모델·scorer 설정이 캐시 키에 들어갑니다. 선택 설치는 `pip install 'labhq[alphagenome]'`; 키·SDK가 없으면 기존처럼 도구를 등록하지 않고 doctor가 안내합니다. [출처·점수 해석·API 이용 조건](../reference/alphagenome-variant.md).
- AlphaMissense(#621): 사람 GRCh38의 `vep`는 Ensembl VEP 옵션 `AlphaMissense=1`을 함께 보냅니다(2026-10-15 실제 응답으로 확인: `transcript_consequences[].alphamissense = {am_pathogenicity, am_class}`, missense transcript에만 붙음). 결과마다 `alphamissense`가 붙습니다. missense는 요약에 고른 MANE(없으면 canonical) transcript의 점수 0~1과 분류를 `AlphaMissense: 0.93 (병원성 가능성 높음)` 꼴로 보여 줍니다. 그 transcript에 점수가 없으면 `점수 없음 (이 transcript)`으로 두고, 다른 transcript의 점수는 `alphamissense_other`에 따로 밝힙니다. missense가 아니면 `해당 없음`입니다. 분류는 `likely_pathogenic`(점수 > 0.564) 병원성 가능성 높음, `ambiguous` 불확실, `likely_benign`(< 0.34) 양성 가능성 높음이고, GRCh37 서버의 옛 이름(`pathogenic`·`benign`)도 읽습니다. `status`는 `ok`·`not_applicable`(missense 아님)·`no_score`(missense인데 점수 없음)·`unavailable`(플러그인 실패)·`not_requested`(GRCh37이나 사람이 아닌 종)입니다. Ensembl은 모르는 옵션을 조용히 무시하므로 `no_score`는 "표에 없거나 붙지 않았다"는 뜻입니다.
- AlphaMissense를 Ensembl이 거부하면(400·500) 같은 요청을 플러그인 없이 한 번 더 보내 나머지 VEP 결과를 살리고, 그 항목은 `unavailable`로 적고 캐시하지 않습니다. 옵션이 캐시 키에 들어가므로 AlphaMissense 전에 저장된 답은 완전한 답으로 다시 쓰이지 않습니다.
- AlphaMissense 예측값의 라이선스는 CC BY 4.0이라 출처 표기(Cheng et al. 2023 Science)가 필요합니다. 2024-03-13 전 배포본은 CC BY-NC-SA 4.0(비상업)이었습니다. DeepMind는 임상용으로 검증·승인하지 않았다고 밝히며, 예측은 임상 근거가 아닙니다. 라이선스·이용 조건·인용은 결과의 `source.additional_sources`와 `outputs/annotation_queries.jsonl`에 남습니다. 데이터 판본은 REST가 알려 주지 않아 Ensembl release로만 기록합니다.
- 논문 본문(#697, #744): `fulltext`는 Europe PMC 본문이 없으면 Unpaywall·OpenAlex의 합법 OA 위치를 차례로 찾습니다. PDF·HTML에서 확인한 본문은 `.tmp/literature/<id>.md`에만 두며, 구조가 부족한 PDF 조각은 `format_failure`입니다. 못 읽으면 사유를 남기고 초록이나 서지로 답하며 일시 오류는 캐시하지 않습니다. `access_level`이 실제로 읽은 수준입니다. 압축(`Content-Encoding`) 응답도 한 번만 풀어 읽고(#954), 한 논문의 조회·저장 예외는 그 ID의 결과에 사유(`예외 종류: 설명`)로만 남겨 같은 호출의 다른 ID를 실패시키지 않습니다. 모든 ID가 실패해야 호출이 실패합니다.
- 핵심 논문 전문(#745, #746): `key_fulltext`는 허용 OA가 없을 때 `literature.institution_enabled`가 켜진 경우에만 PI가 로그인해 둔 전용 browser profile 또는 기관 proxy를 한 DOI씩 씁니다. TDM 허용과 외부 모델 전송 허용도 각각 켜야 하며, 요청 간격·하루 상한을 지킵니다. labhq는 비밀번호·cookie·token을 읽거나 저장하지 않습니다. 실패하면 제목·DOI·핵심 이유·필요 절/표·시도 경로를 한 카드로 요청합니다. PI는 카드에 PDF를 올리거나 `.tmp/literature/inbox/`의 지정 파일에 둡니다. 기본 2시간(`pdf_request_timeout_s`) 안에 파일이 없으면 카드를 닫고 초록 또는 다른 OA 논문으로 진행합니다. 실패 상태는 `outputs/literature_access.jsonl`과 보고서 한계에 남습니다. PDF·추출문은 `.tmp/literature/` 밖으로 나가지 않습니다.
- PDF 승인 경계: 외부 모델 읽기 허용이 꺼져 있으면 카드를 열지 않습니다. 업로드는 runner 저장 확인 뒤에만 성공하며, 승인된 파일에서 요청한 절·표가 확인될 때만 본문으로 기록합니다.
- 문헌 발견(#720, #744): `citation_chase`는 참고문헌과 인용 논문, `preprints`는 bioRxiv·medRxiv, `openalex_works`는 다분야 후보를 찾습니다. OA로 후보를 미리 거르지 않습니다. Crossref·Unpaywall 조회는 본문을 읽었다는 증거가 아닙니다. 직원은 방향별 후보 전체와 선정·확보·결론 근거 분모를 따로 남기고 OpenAlex-only 근거도 셉니다.
- Unpaywall 연락용 email은 `annot.unpaywall_email`에 둡니다. 비어 있으면 `unpaywall` 도구가 `enabled: false`로 답하고 API를 부르지 않습니다. 결론 핵심 논문의 `key_fulltext`도 같은 email로 합법 OA 위치를 먼저 찾으며, 비어 있으면 Unpaywall만 건너뛰고 다른 합법 OA·기관 세션·PDF 요청 순서는 그대로 진행합니다. 값은 결과·조회 기록·캐시 키에 넣지 않습니다.
- 통제 구역 데이터에서 나온 변이·영역도 조회합니다. 경고·승인·거부를 붙이지 않습니다(PI 결정 2026-10-06). 그래서 Claude는 `--allowedTools`, Codex는 approve 모드로 승인 없이 부릅니다.
- `labhq_annot` API 조회는 `key_fulltext`의 공개·기관 자동 조회와 요청 간격 대기를 포함해 단계 첫 조회부터 20분 상한을 공유합니다. 대기·경과 시간을 작업판 로그에 알리고(#676), 응답을 기다리는 동안은 직원 카드가 `외부 응답 대기 n분째 / 상한 20분`을 보이며 `labhq status`의 해당 단계 줄에도 같은 문구가 붙습니다(마지막 진행 알림 뒤 경과 시간을 더해 상한을 넘지 않게 표시하고, 조회가 끝나거나 시간 초과가 나면 지웁니다). `labhq_annot`의 모든 도구(variant·regulatory·문헌·`key_fulltext`·AlphaGenome)는 이 실행기를 거치며, 새 도구가 실행기를 거치지 않으면 test가 실패합니다. 시간 초과는 `outputs/external_lookup_status.jsonl`에 남깁니다. 직원은 자동 재호출 대신 남은 작업을 진행하고 빠진 조회를 보고서에 씁니다. PI PDF 승인 대기 시간만 예산에서 빼며 이미 쓴 조회 시간은 유지합니다. 승인 정책은 바꾸지 않습니다. 기관 브라우저는 취소 시 소유한 process의 종료를 확인하며 완료된 PDF만 반영합니다. 중단할 수 없는 AlphaGenome SDK thread는 잔류 호출 상한에 집계합니다. 단계가 끝나면 runner가 이 기록을 다시 읽어 시간 초과·실패·취소·끝난 기록 없음을 도구별로 세고, 작업판 경고, 보고서 경고, 성공한 연구 단계의 CP2 카드와 `## 한계`에 표시합니다. 원장에 대응 행이 없어도 표시하고, 기록을 읽지 못한 경우도 파일 없음과 구분해 표시합니다. 이 기록은 직원 workspace의 기록일 뿐 호출이나 본문 독해의 증명이 아니므로 증거 칸을 채우지 않고, 단계 성공이나 필수 산출물 판정도 바꾸지 않습니다. [범위·검증 근거](../reference/external-wait-676.md).
- 요청 전체 조회 상한(#858, 선택): `annot.request_lookup_limit`에 숫자를 두면 `labhq_annot`이 한 요청에서 공개 서비스로 보내는 HTTP 요청을 그 횟수까지만 허용합니다. runner가 요청마다 보내기 직전에 한 번 셉니다. 실패한 요청, 재시도, 따라간 redirect, 논문·항목마다 보내는 요청도 각각 한 번입니다. 허용되지 않은 요청은 보내지 않고 도구 오류로 돌려줍니다. 요청 구분은 runner가 작업마다 발급한 token의 요청이며, 직원이 적은 값은 쓰지 않습니다. 같은 요청의 모든 직원·단계와 runner 재시작이 `runner.state_dir/lookup_admission.sqlite3` 하나를 함께 씁니다. 요청을 확인할 수 없을 때도 보내지 않습니다. 상한이 켜져 있으면 요청마다 셀 수 없는 AlphaGenome 도구와 `key_fulltext`의 기관 browser 경로는 쓰지 않습니다. 비우면(기본) 세지 않습니다. runner가 둘이면 각자 따로 셉니다. `research.live_source_check`를 켜면 gateway의 출처 실시간 확인(Crossref·doi.org·NCBI)의 요청도 같은 요청 단위 카운터에 들어가며(#891), 재시도도 한 번씩 셉니다. 상한에 닿으면 그 조회는 보내지 않고 `확인 못 함`(`requires_verification`, 사유 `lookup_limit`)으로 표시하고 이어 갑니다. `근거 없음`(`not_found`)과 다릅니다. 상한이 남아 있어도 한 호스트가 연속 실패(HTTP 5xx 3회 또는 429·5xx 합쳐 5회 연속)로 쉬는 중이면 그 호스트로는 보내지 않고 사유 `host_paused`로 표시합니다. 이때는 상한을 쓴 것이 아니고 조회 몫이 남아 있으며, 상세에 호스트와 다시 보낼 수 있는 시점(`retry_after_s`)이 적힙니다. 쉬는 시간이 지나면 한 번만 시험 삼아 보내고, 성공하면 다시 열립니다. `lookup_limit`은 상한이 실제로 소진된 경우에만 씁니다. gateway와 `labhq verify`는 같은 설정을 쓰는 PC에서 runner와 같은 파일을 세며, 상한을 비우면 세지 않고 동작도 그대로입니다. 웹 검색이나 다른 MCP·셸 접속은 이 상한이 막지 않으므로, 상한을 증거로 쓰려면 그런 경로를 직원 구성에서 빼야 합니다. 상한이 켜져 있으면 CSO 계획(재계획 포함)·선행 연구 검색·모든 단계의 prompt에 이 요청의 사용 횟수를 사실로 적습니다: "이 요청 사용 N/M, 남음 K"(재시도·redirect 포함, 담당별 내역, 이 PC runner의 계수이며 요청문에 적힌 횟수와는 별개로 labhq가 집행하지 않음). 상한이 없으면 아무것도 적지 않고, 상한이 있는데 계수를 읽지 못하면 0이 아니라 UNKNOWN으로 적습니다. 새 요청은 새 예산이라 다른 요청의 사용량을 이어 받지 않으며, 같은 요청의 이어 가기·재개는 계속 쌓입니다(#1088).
- 조회 상한의 호스트 차단기(#1279): 같은 호스트가 5xx를 3회 연달아 돌려주거나, 429와 5xx를 합쳐 5회 연달아 돌려주면 그 호스트로는 300초 동안 보내지 않습니다. 보내지 않은 요청은 조회 몫에서 빼지 않고 도구 오류로 돌려줍니다. 오류에 호스트 이름과 "5xx/429를 연달아"가 적혀 있으면 상한에 닿은 것이 아니라 이 차단입니다. 300초가 지나면 한 번만 다시 보내 보고, 성공하면 풀리며 실패하면 다시 300초 멈춥니다. 한 조회 안에서 429가 4번 나오다 풀리는 경우는 자르지 않도록 429는 5회로 두었습니다. 다른 호스트로 가는 요청과 다른 요청에는 영향이 없고, 조회 몫을 세는 방식(재시도·redirect 포함)은 그대로입니다. 차단된 호스트와 남은 초는 각 단계 prompt의 사용량 문단에 나옵니다. 한 호출 안의 재시도는 도구 프로세스가, 다른 호출·단계·직원은 `lookup_admission.sqlite3`의 응답 기록이 막으며, 쿨다운은 마지막 응답이 도착한 때부터 셉니다. 조회 상한을 비우면 이 차단기도 없습니다. CP1 계획이 확정될 때 쉬는 호스트가 있으면 계획 경고에 호스트와 남은 초, 보내지 않은 시도는 조회 몫을 쓰지 않는다는 사실이 오릅니다(#1282). 카운터를 읽지 못하면 경고만 빠지고 계획은 막히지 않습니다.
- 서비스 오류는 조회 몫을 쓰지 않음(#910): 보낸 요청이 HTTP 5xx나 429 응답을 받으면, 응답이 기록되는 순간 그 요청을 조회 몫에서 되돌리고 대신 일시 오류로 셉니다. 서비스 장애나 속도 제한 때문에 상한이 바닥나 정작 답하는 조회를 못 하는 일을 막으려는 것입니다(8차 시운전에서 503 15회·429 4회로 20회를 다 썼습니다). 일시 오류도 끝이 있어야 하므로 한 요청의 일시 오류는 조회 상한과 같은 횟수까지만 받고, 그 뒤에는 보내지 않고 거절합니다(사유 `transient exhausted`, 출처 확인에서는 `lookup_limit`). 응답을 기다리는 중인 요청, 응답 없이 끝난 요청(시간 초과·연결 실패), 429가 아닌 4xx는 그대로 셉니다. 선행 연구 몫(`annot.precedent_lookup_share`)도 같은 방식입니다. 단계 prompt의 사용량 문단은 일시 오류 수와 그 상한을 따로 적습니다.
- 브리핑·선행 연구 조회 합산 몫(#691): 상한을 설정하면 `annot.precedent_lookup_share` 기본 0.5를 적용합니다. `floor(상한 × 몫)`까지 허용하므로 상한 1이면 기본 몫은 0입니다. 0~1로 조정하고 `null`이면 몫 제한을 끕니다. 전체 상한은 그대로이며 기존 원장의 단계 미기록 조회는 전체 사용량에만 포함됩니다.
- 같은 단계의 동시 조회와 PDF 승인 대기를 차례로 처리합니다. 승인 대기 중에는 같은 단계의 새 조회를 시작하지 않으며 다른 단계는 독립적입니다. [범위·한계](../reference/approval-wait-784.md).
- ChIP-Atlas enrichment는 DDBJ 서버에서 잡으로 돕니다. 제출 뒤 결과 파일을 15초마다 확인하며 `wait_s`(기본 600초, 최대 1500초)까지 기다립니다. 시간을 넘기면 오류 문구에 `request_id`가 붙고, 자동 재호출은 하지 않습니다. 명시적으로 이어받기를 요청받은 경우에만 같은 입력·옵션에 그 값을 붙여 결과를 받습니다. 제출할 때 `request_id`와 입력·옵션의 hash, 그때의 release를 캐시에 함께 적어 두므로, 다른 입력·옵션으로 이어 받으려 하면 오류입니다. 그 사이 `experimentList.tab`이 갱신됐으면 답을 제출 때 release로 기록하고 캐시하지 않으며 `release_note`를 붙입니다. 제출 기록이 없는 `request_id`(다른 PC·캐시 없음)는 답은 주되 캐시하지 않고 `request_id_note`를 붙입니다. 유전자 기호는 적은 철자 그대로 보내고 캐시도 그 철자로 나눕니다(mm10의 `Sox2`처럼 대소문자가 뜻을 가짐). 결과는 실험(SRX)마다 겹친 수·log10 Q·fold enrichment이고, 답에는 단백질별 최고 값과 상위 실험 20개가 요약됩니다.
- ENCODE cCRE: SCREEN GraphQL API는 개인 API 키(90일마다 갱신)가 필요해서 쓰지 않습니다. ENCODE portal REST에서 가장 새 cCRE registry 파일(GRCh38 V4 bed.gz, 약 33 MB)을 찾아 md5를 확인한 뒤 캐시에 두고 겹침을 직접 셉니다. 첫 호출만 내려받는 시간이 듭니다.
- GTEx: 유전자 ID는 dataset의 GENCODE 판본으로 찾습니다(gtex_v10은 v39, gtex_v8은 v26). eQTL은 항목당 1,000줄까지 받고 전체 개수를 함께 돌려줍니다. 조직을 고르면 조직마다 따로 받아 1,000줄 한도와 전체 개수도 조직마다 셉니다. 조직 이름은 `tissueSiteDetailId`(예: `Liver`, `Whole_Blood`)이고, 없는 이름이면 호출 전체가 오류입니다.
- RCSB PDB·ChEMBL(#1187): 구조·화합물 조회 도구입니다. `pdb`는 PDB ID로 구조 요약(제목·실험 방법·해상도·등록·공개·수정 날짜·결합 리간드·대표 논문)을 돌려주고, 그 밖의 문자열은 전문 검색해 상위 `top`(1~25)개를 요약합니다. 좌표 파일은 받지 않습니다. `chembl`은 target·compound는 ID나 검색어(상위 5개), `activity`는 target ChEMBL ID의 측정값(`standard_type`·값·단위·assay·문서)을 `limit`(1~200)행까지 100행씩 받습니다. 잘린 목록은 `truncated`가 표시하며 전체처럼 인용하면 안 됩니다. 활성값은 assay마다 다르니 같은 `standard_type`·단위끼리만 비교합니다. 못 찾음은 `found=false`, 입력 오류는 그 항목만 오류입니다. 응답 원본은 `answer.raw`로 `outputs/annotation/`의 결과 파일에 그대로 남고, 도구 응답은 요약만 줍니다. 요청 간격은 RCSB 0.2초·ChEMBL 0.5초이고 두 서비스 모두 공식 호출 한도를 공식 문서로 확인하지 못해 보수적으로 잡은 값입니다. PDB 답은 7일 캐시하고 ChEMBL 답은 DB 버전이 캐시 키에 들어 있어 버전이 바뀌면 새로 받습니다. 라이선스는 `source`에 이름과 URL로 남습니다. 비서실장은 문헌 도구만 받으므로 이 둘은 받지 않습니다.
- 추론 도구 `alphagenome`·`alphagenome_variant`는 키가 있을 때만 등록됩니다(Atlas의 환경변수 키와 별개). `labhq init`이 처음 설정할 때 한 번 "AlphaGenome API 키가 있으면 붙여 넣으세요, 없으면 Enter"라고 묻습니다(입력은 화면에 안 보임). Enter면 넘어가고, 나중에 `labhq init`을 다시 돌리면 다시 묻습니다. 키는 `annot.alphagenome_key_file`(기본 `~/.labhq/secrets/alphagenome_api_key`) 파일에만 저장됩니다. POSIX는 0600, Windows는 폴더와 파일의 ACL을 현재 계정 항목 하나로 새로 씁니다. 미리 있던 명시 항목도 지우고 SYSTEM·Administrators 항목도 두지 않습니다(SYSTEM 서비스는 백업 권한이 있어 항목이 없어도 됩니다). 설정 파일에는 위치만 둡니다. 환경변수를 쓰지 않는 까닭은 직원 프로세스가 runner의 환경을 물려받기 때문입니다. 키는 MCP 서버가 시작할 때 파일에서 읽고 명령줄·로그·이벤트·프롬프트에 넣지 않으며, doctor는 `AlphaGenome: 키 있음/없음`만 보입니다. 키 파일은 PI 개인 경로에도 들어갑니다. 접근 제한은 키 파일이 든 폴더에 걸리므로 그 폴더는 키 전용이어야 합니다. 다른 파일이 있는 폴더, 홈 폴더, labhq state·작업 폴더·설정을 품은 폴더면 저장을 거부합니다. runner를 별도 계정으로 돌리면 키는 runner 계정이 읽어야 하므로 `labhq init`을 runner 창에서 한 번 더 돌립니다([runner 전용 계정](../runner-account.md#alphagenome-키-선택)). client는 runner의 Python에 `pip install alphagenome`으로 설치하며, 키가 있어도 client가 없으면 도구가 등록되지 않고 doctor가 warn을 냅니다.
- 직원 명단의 AlphaGenome 표시(#1200): CSO가 계획에 쓰는 직원 명단은 AlphaGenome 도구를 그 도구를 실제로 쓸 수 있을 때만 올립니다. 쓸 수 있는지는 gateway가 자기 키 파일을 보고 정하지 않고, **각 runner가 등록할 때 `alphagenome_ready`로 보고합니다**(그 runner의 계정에 키 파일이 있고 그 Python에 `alphagenome` 클라이언트가 있는지, 키 값은 보내지 않고 사실만 보냅니다). 그래서 키를 가진 runner가 gateway와 다른 계정이나 PC에서 돌아도 명단에서 빠지지 않습니다. 명단의 직원 줄마다 **그 직원을 맡은 runner**의 보고를 따르므로(요청이 WSL runner로 고정되면 그 runner), 다른 runner가 준비돼 있어도 그 직원을 맡지 않은 runner의 상태는 보지 않습니다. 키를 넣거나 클라이언트를 설치한 뒤에는 runner를 다시 시작해 다시 등록해야 보고가 바뀝니다. 아직 `alphagenome_ready`를 보고하지 않는 이전 runner는 예전처럼 gateway 자체 확인으로 처리합니다.
- AlphaGenome 답은 track마다 요약입니다. 변이는 alt−ref 최대 절대 변화와 그 위치, 대립유전자별 합계, 구간은 요청한 구간 안의 track별 평균·최대 신호와 위치이고(예측 창 전체가 아님), 상위 10개 track이 답에, 전체 track 요약이 결과 파일에 남습니다. 답의 `window`는 예측 창, `summarised_over`는 요약한 범위입니다. 서열 길이는 16KB·100KB(기본)·500KB·1MB이고 변이·구간 가운데를 중심으로 잡습니다. 1 bp 해상도 출력(CHIP_HISTONE·CHIP_TF 외 전부)을 `ontology_terms` 없이 500KB·1MB로 부르면 변이 하나에 수 GB라 거부합니다. client 연결은 30초, 항목당 예측은 300초까지 기다리고, 연결 실패나 시간 초과가 나면 같은 호출의 남은 항목은 바로 실패로 돌립니다. client 호출은 매번 새 thread에서 돌아 다른 도구를 막지 않고, 멈춘 호출이 다음 호출을 붙잡지도 않습니다. 시간이 초과된 thread는 끊을 수 없어 끝날 때까지 세어 두며, 4개가 쌓이면 새 호출을 바로 거부합니다(잠시 뒤 다시 부르거나 labhq를 다시 시작). 비상업 연구용이며 임상 판단에 쓰지 않습니다([약관](https://alphagenome.google/terms)).

### 역할·엔진·도구를 나눈 기준

| 기준 | 어디에 적용했나 | 근거 |
|---|---|---|
| 1인 PI 바이오인포 랩의 직무로 나눈다 | 역할 목록 전체 | Virtual Biotech 구조를 옮기며 바꾼 점([배경](20-background.md#배경-the-virtual-biotech)) |
| 판단·조율·위험 관리가 큰 자리에 가장 높은 등급 모델 | CSO(opus) | PI 결정(2026-10-01): 막힌 직원의 질문에 CSO가 PI 대신 먼저 답하고, 위험한 것만 PI에게 올린다 |
| 판단 업무는 opus, 정형·반복 업무는 sonnet | biologist·analyst·recruiter / chief_of_staff·data_steward·qc_reviewer | 첫 설계 |
| 리뷰어는 다른 벤더 | sci_reviewer(Codex) | 같은 모델끼리 맹점을 공유하지 않게 |
| 도구는 그 일에 필요한 것만 | CSO는 읽기만, HPC는 계산하는 직원만, 웹은 조사·리뷰 직원만 | 표에서 드러나는 원칙. 따로 적어 둔 기록은 없다 |
| 엔진은 사정에 따라 바뀌었다 | engineer=Codex(PI 결정), lit_scout=Gemini CLI → Antigravity(개인 계정 차단) → Codex gpt-6-luna(Gemini 은퇴) | STATUS.md |

기록으로 확인된 기준은 직무 구분, 교차 벤더 리뷰, 판단은 opus·반복은 sonnet이라는 첫 설계입니다. 역할별 이유,
도구 배정, `max_turns`, 모델 등급은 아직 검증하지 않았으며 #40 bench에서 잽니다. 엔진은 MCP·resume·hook·예산 상한
지원 여부로 고릅니다(#39). 첫 실험(#27)에서는 bioinfo-agent가 배정되지 않고 읽기 전용 리뷰어에게 쓰기 작업이 갔습니다.

### bioinfo-agent 연결하기

표준 bioinformatics 분석은 bioinfo-agent가 지원하는 nf-core pipeline을 우선 씁니다. 단순 수집·가공·조회는 알맞은 직원에게 바로 맡기고,
지원 밖의 복잡한 신규 구현은 engineer나 analyst에게 배정합니다. 선택한 pipeline이나 다른 배정 사유는 계획에 남깁니다.

`policy.bioinfo_agent.pipeline_pr: true`는 후보별 승인 경로만 활성화합니다. 완성된 pipeline 후보가 manifest와 소스 파일을 명시하면
승인 카드가 열리고, 후보마다 PI가 별도로 `yes`라고 답한 뒤에만 기존 공개·비밀값·자료 검사를 거쳐 GitHub에 보냅니다.
승인 전이나 거절 뒤에는 GitHub를 호출하지 않습니다. 현재 fork 자동 생성은 지원하지 않아 승인 뒤에도 공개 upstream 쓰기 권한이 필요하며,
권한이 없으면 `pipeline PR 대기`로 남습니다.
러너 환경변수 `BIOINFO_AGENT_DIR`에 bioinfo Claude Code plugin 디렉터리를 지정합니다(`labhq init`이 찾아 `engines.claude_code.env`에 넣고, 못 찾으면 묻습니다). 실행 전에
plugin 이름(`bioinfo`)과 `required_skills`의 skill이 실제로 있는지 확인하고, 없으면 시작하지 않습니다(오류에는 경로를 싣지 않음).
CLI를 띄우기 전에 plugin 이름·version·내용 해시를 run manifest의 `plugins`에 남깁니다. 이 직원만 `--plugin-dir`로
plugin을 읽고 `--disable-slash-commands`를 빼서 `bioinfo:bioinfo-analyze` skill을 씁니다. 이때 setting source는
`local`만 둡니다(작업공간에 남은 `.claude/skills`가 끼어들지 않게). 다른 직원의 skill 차단과
`--setting-sources project,local`, 개인 `CLAUDE.md` 제외, auto memory 차단은 그대로입니다.
Nextflow를 돌리면 report·timeline·trace·DAG 네 파일을 `outputs/pipeline_info/`에 복사해 산출로 선언합니다. bioinfo-agent의 기본 Nextflow 설정이 네 기록을 모두 켭니다.

다른 자체 CLI 직원은 `engine: cli`와 `cli.command`를 사용할 수 있습니다 (`labhq/adapters/cli.py`).
