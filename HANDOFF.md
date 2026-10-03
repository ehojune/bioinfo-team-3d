# labhq 인수인계 — 개발 총괄에게

저장소 https://github.com/ehojune/bioinfo-team-3d (public) · 패키지와 CLI 이름은 `labhq`

개발 총괄은 Claude와 Codex가 번갈아 맡습니다(PI 결정 2026-10-01). 한쪽의 주간 사용량이 차면 다른 쪽이 이어받습니다.
이 파일은 공개해도 되는 인수인계입니다. PI PC의 로컬 경로·진행 중 작업·보조 스크립트는 저장소 밖 노트에 있고,
`CLAUDE.local.md`(Claude)와 PI의 Codex 전역 지침이 그 노트를 가리킵니다. 결정은 GitHub issue에, 진행 보고는 PR과 `docs/status/`에 남깁니다.

## 시작할 때

1. `STATUS.md` 맨 위 몇 항목, 열린 PR(`gh pr list`), 고정 issue #69
2. 아래 작업 큐와 PI 결정
3. 설정이 있는 PC면 `labhq doctor`. test는 바꾼 곳과 관련된 파일만 돌리고 전체 suite는 CI에 맡긴다(AGENTS.md)
4. 끝낼 때: 진행 중인 것을 로컬 노트의 "지금 진행 중"에 적고, 새로 안 것은 `HARVEST.md`(gitignore)에 적는다

## 작업 방식 — 규칙(CLAUDE.md·AGENTS.md)에 더해 겪어서 안 것

| 무엇 | 어떻게 |
|---|---|
| PR 한 개의 흐름 | 브랜치 → 코드 커밋 → push·PR 열기(패치노트 링크에 PR 번호가 필요) → `patch_notes/entries/<branch>.yaml`·`docs/status/` 기록 커밋 → CI(pytest 3.10·3.12·Windows, patch-notes) → squash 병합 |
| 패치노트 | 줄에 커밋 해시가 들어가서 코드 커밋 뒤에 브랜치 전용 YAML을 쓴다. `python scripts/patch_notes.py rows --pr N`이 초안을 만든다. 공유 `patch_notes/README.md`는 main에서 자동 생성한다 |
| README 주기 | 생성 목차만 갱신한 main 커밋은 빼고, main 커밋 3개 안에 README나 `docs/manual.md`를 한 번 고쳐야 CI가 통과한다 |
| 리뷰 봇 깊이 | 고정 상한은 없다. 새 라운드는 직전 수정 확인이나 다른 부류의 결함이 있을 때만. 같은 부류가 더 좁게 반복되면 그 부류를 구조로 한 번 닫고 병합한다. 보통 2~3회에 끝나고, 5회를 넘기면 계속할지 판단한 이유를 PR에 적는다 |
| Codex에 통째 위임 | Codex sandbox는 `.git`에 쓸 수 없다. 지시서에 "commit하지 말고 `.pr-drafts/commits.json`에 [{message, files}]"를 넣고, 받는 쪽이 커밋한다. 커밋 제목을 `#`로 시작하지 않는다(rebase가 주석으로 지운다) |
| 병렬 PR | 같은 설정을 두 PR이 다른 규칙으로 넣으면 충돌 없이 한쪽이 덮인다(#76의 MCP timeout). 병합 뒤 겹친 규칙을 테스트로 확인한다 |
| Python 3.10 | 여러 줄 f-string 치환식, `fromisoformat("...Z")`는 3.10에서 깨진다. 3.12 CI만 보면 놓친다 |
| Windows 직원 CLI | 표준 npm `.cmd` shim은 JS entrypoint와 Node로 풀어서 실행하고, 인식하지 못한 batch만 거부한다. npm이 아닌 Codex 앱 exe는 업데이트마다 폴더가 바뀐다(#73) |
| Codex 직원 | 개인 `~/.codex/AGENTS.md`가 있는 PC는 직원 전용 `CODEX_HOME`에 로그인해야 preflight를 통과한다. 개인 skill 폴더는 여전히 읽히는데 PI는 괜찮다고 했다(#55) |
| 문서 나눔 | `README.md`는 처음 써 보는 사람(웹으로 시작, 10KB 안팎), `docs/manual.md`는 깊이 보려는 사람, `docs/pi-qa.md`는 PI 질문·결정이다. 동작을 바꾸면 manual의 해당 절을 고치고 README에는 쌓지 않는다 |
| 연결된 도구 표·배지 | 직원 설정을 바꾸면 `python scripts/integrations.py --write`로 README 배지와 manual의 표를 갱신하고 `--check`로 확인한다. 배지는 같은 설정과 `pyproject.toml`·`labhq/settings.py`·`.github/workflows`에서 만든다 |
| 느린 test | 90초 MCP 실측은 `LABHQ_SLOW_TESTS=1 pytest -q tests/test_long_mcp_call.py`. CI에서는 별도 job(`pytest-slow`)이 돌린다 |
| 실측 fixture | Windows 11 실측 스트림은 `tests/fixtures/real/`에 있다. 재캡처: `python scripts/probe_engines.py antigravity --output-dir <저장소 밖 경로> --redact` |
| PI에게 물을 때 | 지금 무엇이 일어나고, 각 선택이 PI에게 무엇을 바꾸는지를 먼저 한 줄씩 쓴다. 비슷한 말(개발 기록 저장소 vs 프로젝트별 저장소)은 구분해 쓴다 |

## 작업 큐 (2026-10-03 갱신)

[매뉴얼 로드맵](docs/manual.md#로드맵)의 버전 순서를 따릅니다. 병합된 PR은 뺐습니다.

| 버전 | 상태 | issue / PR | 내용 |
|---|---|---|---|
| v0.25 | 완료 | #298 | 개발 총괄의 모의 시운전 3~11차(2026-10-03). 결함은 그날 고쳐 병합(#350–#361) |
| v0.25 | PI 대기 | #298 | PI 시운전. 그 전에 PI가 Claude 직원 폴더에 로그인(#335) |
| v0.25 | 운영 | #298 | PI 소통 창구: 결정 대기와 진행 보고 |
| v0.25 | 실측 | #276 #37 | 실제 Codex 장시간 MCP probe, 로그의 token 노출(폰 원격 접속은 보류) |
| v0.25 | 릴리즈 때 | draft PR | README를 읽는 사람별로 나눔(처음 쓰는 사람·manual·PI 문답·개발), 개념 그림, 온톨로지 짧은 소개 |
| v0.5 | 1/2 | #90 #58 | 실제 CLI 연구 lane 완주: 10차 `research_reported`(앵커 58·문제 0, `labhq verify` exit 0). 두 번째 완주가 남음(11차 GSE19804는 리뷰가 문헌 추출 실수를 잡아 revise). #58 ①~⑥ 완료. 남은 것: CP3·CP4, revise 뒤 이어 가기(새 CP1에 리뷰 지적·완료 단계 재사용) |
| v0.5 | 다음 | #221 #150 #151 #149 | 산출 종류 선언을 켜기 전 probe, 의미 모델 그림자·A/B 판단 창 |
| v0.5 | 다음 | #57 #300 | 웹 Command Center 6–8, 새 pipeline 기여를 PI에게 묻기 |
| v0.75 | 결정 대기 | P3 · #273 | HPC 접근 방식. #273은 지금 기존 HPC 유지(A) |
| v0.75 | 다음 | #59 | 실행 중 직원 제어와 폭주 차단기 |
| v0.9 | 실측 | #178 | project 링크 거부 규칙의 다른 경로 표기 |
| v0.9 | 다음 | — | 러너 계정 분리, 통제 데이터 시험 |
| v0.95 | 다음 | #300 | 설치·온보딩 문서와 계정 분리 자동화, 기여 여부 묻기 |
| v1 | 나중 | #35 #60 | 시작 전 점검·시설팀, 요청 간 기억 |
| 버전 밖 | 장기 | #62 #61 #79 #70 #121 #275 #316 #69 | fan-out, 트리거, 클라우드 에이전트, 보고 채널, ontology 재평가, 신약 사업부(#90·#58 뒤), 참고 조사, 기록 저장소 운영 |

## PI 결정 (최근)

| 날짜 | 결정 | 기록 |
|---|---|---|
| 2026-10-04 | v0.25 선언. PI 본인 시운전 전에 총괄 리허설(PI 요청 세트 5건 중 4건 accept)을 근거로 하고, PI 질문(자세한·짧은 요청, 단일 Astra, CSO 질문, 실행 중 메시지)은 P1 #373에서 다룬다 | #298, #373 |
| 2026-10-04 | 벤치마킹 안 A: v0.25 뒤 큰 과제 1개를 labhq와 GPT-6-Astra 단독에 같은 문장으로 주고 결과·비용·시간·카드를 비교 | #298 |
| 2026-10-04 | 산출 데이터 종류 어휘는 PI 검토판 41키(상한 45). 넓히기는 bioinfo-agent 파이프라인 기준 후보→PI 검토→실제 요청 순서(#375) | #374, #375 |
| 2026-10-03 | README를 읽는 사람별로 나눔(README·manual·pi-qa·개발 문서). 그래서 10-01의 README 갱신 주기는 README나 `docs/manual.md` 갱신으로 센다 | #298 |
| 2026-10-03 | 로드맵 ⑥ 나: 버전마다 통과 기준, v0.75(HPC 공개 데이터)·v0.9(통제 데이터)로 나눔, v1 앞에 v0.95(다른 사람의 설치·온보딩) | manual 로드맵, #298 |
| 2026-10-03 | 버전 정의 v0.25·v0.5·v0.75·v1·v1.25. 작업 큐는 이 순서를 따른다 | manual 로드맵 |
| 2026-10-03 | 실행 계정: 별도 계정 대신 PI와 같은 계정 + 개인 경로 차단(`policy.private_paths`) | #298 ③, PR #324 |
| 2026-10-02 | 계산 backend: 지금은 기존 HPC 유지(A). Colab(E)은 나중. GBox·Bio-Express(C·D)는 API 계획이 없어 안 할 수 있다. Scheduler 일반화(B)는 두 번째 backend가 생길 때 | #273 |
| 2026-10-02 | 신약개발 사업부는 지금 만들지 않는다(A). #90·#58을 끝낸 뒤 다시 본다 | #275 |
| 2026-10-02 | 한 PC 여러 인스턴스(`--instance`)와 구독 한도 대기·자동 재개 | #37, #298 |
| 2026-10-01 | 개발 총괄은 Claude·Codex 교대. Codex(Pro)에 생산 작업을 적극 위임, 모델·effort는 총괄이 고른다 | 이 파일 |
| 2026-10-01 | Gemini/Antigravity 은퇴, lit_scout는 Codex gpt-6-luna | #49 |
| 2026-10-01 | 커밋마다 패치노트, README는 main 커밋 3개 안에 갱신, README에 동작 화면 | #52, #63 |
| 2026-10-01 | 직원 OS 격리는 하지 않는다. 하네싱·승인 게이트는 나중에 '과업' 단위로 | #55 댓글 |
| 2026-10-01 | 개발 기록은 private `labhq-rounds`, 종료 조건은 #69. 프로젝트별 보고 저장소는 유지 | #43, #69, #70 |
| 2026-10-01 | labhq_ask: CSO가 먼저 답하고 위험한 것만 PI. 그래서 CSO에 가장 높은 등급 모델 | #39 |
| 2026-10-01 | bench 기준선: Opus 5.5·gpt-6-astra 단일 세션. Virtual Biotech 실행은 안 함 | #40 |
| 2026-10-01 | 클라우드 상주 에이전트는 장기 과제. 공개 데이터 + PI 명시 동의 job부터 | #79 |
| 2026-09-28 | 병합은 총괄이 판단, 고정 라운드 상한 없음 | CLAUDE.md |

## 결정 대기 (PI)

1. P3 실제 HPC: 러너 전용 계정은 받을 수 없다(2026-09-28). 권고안은 에이전트를 클러스터 밖에 두고 기관 게이트웨이 broker로만 접근하는 것이다. 확정 전에는 실제 제출을 하지 않는다. 어떤 안이든 통제 데이터 원본은 클러스터 밖·LLM 대화로 나오지 않는다(설계와 검증 중에도).
2. 거버넌스(정부) 층과 Yuan 구성(보류)
3. ~~실제 bench 실행 시점과 한도~~ → 2026-10-04 안 A로 결정(위 표)

## 구조 한눈에

| 층 | 파일 | 역할 |
|---|---|---|
| 게이트웨이 | `labhq/gateway/server.py` | WebSocket(러너·클라이언트), REST, 웹 사무실 서빙, 승인·결정 이력 |
| 오케스트레이터 | `labhq/orchestrator/cso.py` | 브리핑 → 계획(JSON DAG) → 병렬 실행 → 리뷰 → 보고, HPC 수면/기상, 예산, 질의 라우팅 |
| 러너 | `labhq/runner/daemon.py`, `versions.py` | 게이트웨이에 outbound 접속, 태스크 실행, MCP 배선, 잡 감시, CLI 버전 보고 |
| 어댑터 | `labhq/adapters/*.py` | claude_code · codex · cli(자체 에이전트) · antigravity · gemini · mock |
| 도구 | `labhq/tools/*.py` | SGE/PBS/Slurm 스케줄러, hpc_mcp, approval_mcp(권한 프롬프트) |
| 파견직 | `labhq/recruit/paper2agent.py` | 채용 → 오퍼레터 → 수습 → 계약 → 인재풀 |
| GitHub·기록 | `labhq/integrations/github.py`, `rounds.py` | 프로젝트 보고, 공개 가드, 개발 라운드 기록 |
| 점검 | `labhq/doctor.py` | 실행 전 점검과 capability manifest |
| 웹 | `labhq/web/index.html`, `state.js`, `ui/`, `lab3d/` | 공유 reducer, 2.5D·3D 사무실, 결정·작업판·메신저 탭 |
| 직원 | `agents/core/*.yaml` | 정규직 11명(엔진·모델·도구·프롬프트), 역할 기준은 manual '직원과 도구' |
| 패치노트 | `patch_notes/entries/`, `docs/status/`, `scripts/notes_index.py` | PR별 기록과 공유 목차 자동 생성 |

이벤트 프로토콜과 설정은 `docs/manual.md`의 '구조와 이벤트'·'설정 포인트', 알려진 한계는 '알려진 한계'를 보세요.

## 개발 라운드 기록 (#69)

`dev_log.repo`에 private 기록 저장소를, `dev_log.source_repo`에 이 labhq 저장소의 `owner/name`을 적고
`github.token_env`의 환경변수로 토큰을 줍니다. YAML에는 토큰을 넣지 않습니다.

다음 중 하나면 기록 저장소를 끝냅니다: labhq v1.0, #40 bench 확정 뒤 20 rounds 동안 새 교훈 없음,
또는 별도 보고 채널로 이전. `dev_log.enabled: false` → 남은 교훈 Yuan 수확 → 저장소 archive → #69 종료 순서입니다.
기록 저장소는 공개로 바꾸지 않습니다. 이미 올린 기록은 되돌릴 수 없습니다(#69 댓글).
