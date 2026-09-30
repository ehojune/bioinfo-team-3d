# labhq 인수인계 — 개발 총괄에게

저장소 https://github.com/ehojune/bioinfo-team-3d (public) · 패키지와 CLI 이름은 `labhq`

개발 총괄은 Claude와 Codex가 번갈아 맡습니다(PI 결정 2026-10-01). 한쪽의 주간 사용량이 차면 다른 쪽이 이어받습니다.
이 파일은 공개해도 되는 인수인계입니다. PI PC의 로컬 경로·진행 중 작업·보조 스크립트는 저장소 밖 노트에 있고,
`CLAUDE.local.md`(Claude)와 PI의 Codex 전역 지침이 그 노트를 가리킵니다. 결정은 GitHub issue에, 진행 보고는 PR과 `STATUS.md`에 남깁니다.

## 시작할 때

1. `STATUS.md` 맨 위 몇 항목, 열린 PR(`gh pr list`), 고정 issue #69
2. 아래 작업 큐와 PI 결정
3. `pytest -q`, 설정이 있는 PC면 `labhq doctor`
4. 끝낼 때: 진행 중인 것을 로컬 노트의 "지금 진행 중"에 적고, 새로 안 것은 `HARVEST.md`(gitignore)에 적는다

## 작업 방식 — 규칙(CLAUDE.md·AGENTS.md)에 더해 겪어서 안 것

| 무엇 | 어떻게 |
|---|---|
| PR 한 개의 흐름 | 브랜치 → 코드 커밋 → 패치노트만 고친 커밋 → PR → 상단에 리뷰 요청 댓글 한 번 → P1은 그 PR에서, P2는 `PR #N follow-up:` issue → CI(pytest 3.10·3.12·Windows, patch-notes) → squash 병합 |
| 패치노트 | 줄에 커밋 해시가 들어가서 코드 커밋 뒤에 따로 쓴다. 쓴 뒤에는 rebase하지 말고 main을 merge한다(해시가 바뀐다). `python scripts/patch_notes.py rows --pr N`이 초안을 만든다 |
| README 주기 | main 커밋 3개 안에 README를 한 번 고쳐야 CI가 통과한다. 병렬 PR의 병합 순서가 바뀌면 다음 PR이 README 차례가 된다 |
| 리뷰 봇 깊이 | 같은 부류가 더 좁게 반복되면 사례를 막지 말고 그 부류를 구조로 한 번 닫고 병합한다. 5회를 넘기면 멈추고 PR에 이유를 적는다 |
| Codex에 통째 위임 | Codex sandbox는 `.git`에 쓸 수 없다. 지시서에 "commit하지 말고 `.pr-drafts/commits.json`에 [{message, files}]"를 넣고, 받는 쪽이 커밋한다. 커밋 제목을 `#`로 시작하지 않는다(rebase가 주석으로 지운다) |
| 병렬 PR | 같은 설정을 두 PR이 다른 규칙으로 넣으면 충돌 없이 한쪽이 덮인다(#76의 MCP timeout). 병합 뒤 겹친 규칙을 테스트로 확인한다 |
| Python 3.10 | 여러 줄 f-string 치환식, `fromisoformat("...Z")`는 3.10에서 깨진다. 3.12 CI만 보면 놓친다 |
| Windows 직원 CLI | `.cmd` shim은 거부하므로 실제 exe 경로를 쓴다. Codex 앱 exe는 업데이트마다 폴더가 바뀐다(#73) |
| Codex 직원 | 개인 `~/.codex/AGENTS.md`가 있는 PC는 직원 전용 `CODEX_HOME`에 로그인해야 preflight를 통과한다. 개인 skill 폴더는 여전히 읽히는데 PI는 괜찮다고 했다(#55) |
| PI에게 물을 때 | 지금 무엇이 일어나고, 각 선택이 PI에게 무엇을 바꾸는지를 먼저 한 줄씩 쓴다. 비슷한 말(개발 기록 저장소 vs 프로젝트별 저장소)은 구분해 쓴다 |

## 작업 큐 (2026-10-01)

| 상태 | issue / PR | 내용 |
|---|---|---|
| 리뷰 중 | PR #74 (#57) | 웹 Command Center 1–5. 봇 P1(결정 카드에 승인 상세 전체 표시)을 고치는 중 |
| 리뷰 중 | PR #76 (#39, #55 6번) | labhq_ask 질의 경로, task별 broker token |
| 리뷰 중 | PR #77 (#40) | bench·테스트 에이전트(mock 5/5). 실제 비교는 PI 구독으로 PI 머신에서 |
| 작업 중 | #63 | README 연결 목록·배지를 직원 설정에서 자동 생성 |
| 다음 | #80 #81 #82 | HPC 도구 실패 표시, 막힌 단계 같은 세션 재개, `--resume` 비용 합산 확인 |
| 다음 | #35 #36 #58 #73 | 시설팀, 접수·참고 자료, 증거 계층(VB 이식), 첫 설정 마법사 |
| 나중 | #59 #60 #61 #62 #75 #83 #84 | 실행 중 제어, 요청 간 기억, 트리거, fan-out, 폰 UX, #71 후속, 프롬프트 보강 |
| 장기 | #70 #79 | 보고 채널(GitHub·Notion·웹), 클라우드 상주 에이전트와의 공생 |
| 후속 P2 | #51 #53 #54 #67 #68 | 작은 결함 |
| 막힘 | P3 실제 HPC | 아래 결정 대기 1 |

조사 요약은 #64(1차), #78(코드 흐름 리뷰), 구조 판단은 #39 댓글(일회성+재개 유지, 상주형은 bench로 판정)에 있습니다.

## PI 결정 (최근)

| 날짜 | 결정 | 기록 |
|---|---|---|
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

1. P3 실제 HPC: 러너 전용 계정은 받을 수 없다(2026-09-28). 권고안은 에이전트를 클러스터 밖에 두고 기관 게이트웨이 broker로만 접근하는 것이다. 확정 전에는 실제 제출을 하지 않는다.
2. 거버넌스(정부) 층과 Yuan 구성(보류)
3. 실제 bench 실행 시점과 한도: Opus 5.5·gpt-6-astra·labhq를 실제 CLI로 돌리면 PI 구독 사용량을 쓴다

## 구조 한눈에

| 층 | 파일 | 역할 |
|---|---|---|
| 게이트웨이 | `labhq/gateway/server.py` | WebSocket(러너·클라이언트), REST, 웹 사무실 서빙, 승인·결정 이력 |
| 오케스트레이터 | `labhq/orchestrator/cso.py` | 브리핑 → 계획(JSON DAG) → 병렬 실행 → 리뷰 → 보고, HPC 수면/기상, 예산, 질의 라우팅 |
| 러너 | `labhq/runner/daemon.py`, `versions.py` | 게이트웨이에 outbound 접속, 태스크 실행, MCP 배선, 잡 감시, CLI 버전 보고 |
| 어댑터 | `labhq/adapters/*.py` | claude_code · codex · cli(자체 에이전트) · antigravity · gemini · mock |
| 도구 | `labhq/tools/*.py` | SGE/PBS 스케줄러, hpc_mcp, approval_mcp(권한 프롬프트) |
| 파견직 | `labhq/recruit/paper2agent.py` | 채용 → 오퍼레터 → 수습 → 계약 → 인재풀 |
| GitHub·기록 | `labhq/integrations/github.py`, `rounds.py` | 프로젝트 보고, 공개 가드, 개발 라운드 기록 |
| 점검 | `labhq/doctor.py` | 실행 전 점검과 capability manifest |
| 웹 | `labhq/web/index.html`, `state.js`, `ui/`, `lab3d/` | 공유 reducer, 2.5D·3D 사무실, 결정·작업판·메신저 탭 |
| 직원 | `agents/core/*.yaml` | 정규직 11명(엔진·모델·도구·프롬프트), 역할 기준은 README §2 |
| 패치노트 | `patch_notes/README.md`, `scripts/patch_notes.py` | 커밋별 변경 이력과 CI 검사 |

이벤트 프로토콜과 설정은 `README.md` §7–8, 알려진 한계는 §10을 보세요.

## 개발 라운드 기록 (#69)

`dev_log.repo`에 private 기록 저장소를, `dev_log.source_repo`에 이 labhq 저장소의 `owner/name`을 적고
`github.token_env`의 환경변수로 토큰을 줍니다. YAML에는 토큰을 넣지 않습니다.

다음 중 하나면 기록 저장소를 끝냅니다: labhq v1.0, #40 bench 확정 뒤 20 rounds 동안 새 교훈 없음,
또는 별도 보고 채널로 이전. `dev_log.enabled: false` → 남은 교훈 Yuan 수확 → 저장소 archive → #69 종료 순서입니다.
기록 저장소는 공개로 바꾸지 않습니다. 이미 올린 기록은 되돌릴 수 없습니다(#69 댓글).
