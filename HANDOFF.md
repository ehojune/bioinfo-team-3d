# labhq 인수인계 — 이어서 작업할 에이전트에게

저장소 https://github.com/ehojune/bioinfo-team-3d · 패키지와 CLI 이름은 `labhq`

이 파일이 작업 지시의 원본입니다. 감독은 PI와 Claude(chat), 보고는 PR과 `STATUS.md`로 합니다.

## 프롬프트 (그대로 붙여 넣어 시작)

```text
너는 labhq를 이어받아 개발하는 엔지니어 에이전트다.

저장소: https://github.com/ehojune/bioinfo-team-3d (public, 기본 브랜치 main, 인계 시점 커밋 db49ed4)
저장소 이름은 bioinfo-team-3d이고, 파이썬 패키지와 CLI 이름은 labhq다.

labhq는 PI 한 명이 Claude Code · Codex · Gemini CLI 에이전트를 바이오인포 연구소 직원처럼 운영하는 플랫폼이다.
CSO가 요청을 쪼개 정규직 11명(bioinfo-agent 포함)에게 맡기고, 팀에 없는 방법은 Paper2Agent로 논문을 파견직
에이전트로 만들어 채용한다. 러너는 SGE/PBS HPC에 작업을 내고, 기다리는 동안 에이전트를 재웠다가 끝나면 깨운다.
PI는 웹 사무실(폰 포함)에서 실시간 상태를 보고 승인한다. 결과는 프로젝트별 GitHub 저장소에 이슈와 보고서로 올라간다.

현재 상태: pytest 26개 통과. 웹 UI는 헤드리스 DOM 검사와 실제 게이트웨이 이벤트 재생으로만 검증했다.
실제 CLI(claude/codex/gemini), 실제 HPC, 실제 GitHub API 보고, 실제 Paper2Agent 변환, 실기기 화면은 아직 한 번도 돌려 보지 않았다.

[1] 시작
1. git clone https://github.com/ehojune/bioinfo-team-3d && cd bioinfo-team-3d
2. python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
3. pytest -q  → 26 passed 여야 한다
4. labhq demo --web → 출력된 http://127.0.0.1:8787/?token=change-me-client 에서 사무실이 움직이는지 본다
5. (완료, #1) 인수인계 문서 5개와 v0.2.1은 이미 main에 있다. 다시 만들지 말고 STATUS.md 맨 위 항목부터 읽는다.

[2] 작업 방식
- 단계마다 새 브랜치(예: p1-real-cli)에서 작업하고 PR을 연다. main에 직접 push하지 않는다.
- PR 본문에는 템플릿대로 한 일, 테스트 결과, 바꾼 파일, 막힌 점, 질문을 적고, 같은 내용을 STATUS.md 맨 위에 추가한다.
- ⛔에서는 멈추고 PI 확인을 받는다. 병합은 PI가 한다. PI 위임(2026-09-25): Codex 리뷰 봇과 합의된 PR은 Claude가 병합한다.

[3] 작업 순서
P0 완료: public 저장소 ehojune/bioinfo-team-3d
P0+ 완료(#2): .github/workflows/test.yml이 모든 브랜치 push와 PR마다 pytest를 돌리고, README 맨 위에 저장소 주소와 CI 배지가 있다.
P1 실제 CLI 연동: claude/codex/gemini --version 기록 → ⛔ 실제 계정으로 첫 실행 전 확인 → 임시 디렉터리에서 엔진별 direct 요청 1개씩.
   실제 stream 출력을 tests/fixtures/에 저장하고 파서 테스트를 추가한다. 반드시 확인: Claude --permission-prompt-tool과
   --settings deny 규칙, Codex exec의 MCP 호출 자동 취소 이슈(openai/codex#24135), Gemini stream-json 이벤트 이름과 승인 모드.
   fixture에는 토큰, 계정 이름, 내부 경로가 남지 않게 가린다.
P1+ 안전·복구 최소선 (PI 결정 2026-09-26, P2·P3 전에): ① 게이트웨이의 요청·승인과 러너의 HPC 잡을 디스크에 남기고
   재시작 때 복구한다 ② CSO는 선행 단계가 실패하면 하위 단계를 멈추고, 리뷰 파싱 실패를 통과로 치지 않으며, 재시도 규칙을 둔다
   ③ LLM·MCP 실행 계정은 통제 원본에 닿지 못하게 한다(전용 계정·파일 권한). 데이터 구역 가드가 동작하지 않는 OS(현재 Windows)에서는
     러너가 시작을 거부한다. 권장 구성: Linux 러너 전용 계정, 데이터 계정 소유 `0700` 구역, `hpc.submit_prefix`로 데이터 계정 잡 제출(`hpc.user`도 지정, `sudoers`는 `qsub`만 허용),
     공유 폴더에는 집계 결과만 둔다 ④ 직원 CLI가 PI 개인 설정(hook·서브에이전트·config·전역 지침)을 물려받지 않게 격리한다
   ⑤ 이벤트에 schema_version과 순번을 넣고 재연결 때 빠진 이벤트를 다시 보낸다. 근거: 세 자문(gpt-6-sol, gpt-6-astra, Gemini) 설계 검토.
P2 bioinfo-agent 연결: PI에게 실행 방식(CLI / 파이썬 패키지 / Claude Code 스킬)을 묻고 agents/core/bioinfo-agent.yaml의
   cli.command를 맞춘다. 가능하면 bioinfo-agent가 labhq JSONL 이벤트(status/log/tool/result)를 내보내게 한다.
P3 실제 HPC: config/labhq.yaml(커밋 금지)에 scheduler, PE 이름, 메모리 리소스, 큐를 채운다 → ⛔ 첫 제출 전 확인 →
   hello-world 잡으로 승인 → 수면 → 기상 흐름을 확인한다. 실제 qstat/qacct(또는 PBS qstat -f) 출력을 가려서 fixture로 추가한다.
P4 웹 사무실 실사용 점검 + 3D 도입: iPhone Safari와 데스크톱에서 확인하고 고친다. 3D 사무실(labhq/web/lab3d, #3)을 `/3d`로 연결해
   2.5D와 함께 둔다. 먼저 index.html의 이벤트 처리를 상태 reducer로 떼어 두 렌더러가 같이 쓰게 한다. 승인 UI는 DOM에 둔다.
P5 프로젝트 GitHub 보고: private 테스트 저장소로 --project 요청 → 이슈, 코멘트, 보고서 커밋, 가림 처리를 확인한다.
P6 Paper2Agent 실채용: labhq setup-paper2agent → 컨테이너나 VM에서 scanpy 채용 → 수습 통과, 비용과 시간을 기록한다.
보류(PI 확인 전에는 만들지 않음): iOS 네이티브 앱, 거버넌스(정부) 층.

[4] 규칙
- 이 저장소는 public이다. 커밋 전에 scripts/check_public.sh를 돌리고, 연구 데이터·코호트 정보·내부 서버 경로·토큰을 커밋하지 않는다.
- 비밀값은 환경변수로만 다룬다. config/labhq.yaml은 커밋하지 않는다.
- 통제접근(DUA) 데이터의 원본을 클러스터 밖이나 LLM 대화로 가져오지 않는다.
- 테스트를 지우거나 약하게 만들지 않는다. 바꿀 때마다 pytest -q, UI를 건드렸으면 브라우저로도 확인한다.
- 구조를 크게 바꾸거나 의존성을 추가할 때는 먼저 PR 설명으로 제안하고 확인받는다.
- GitHub PR에서 Codex에게 말할 때는 Codex 코멘트에 바로 답글을 달더라도 항상 @codex를 붙인다.
- 보고는 한국어로, 기술 용어·명령·유전자 이름은 영어 그대로 쓴다.

[5] 감독
PR과 STATUS.md가 공식 보고 채널이다. Claude(chat)가 public 저장소의 PR과 STATUS.md를 읽고 리뷰하며,
PI가 그 리뷰를 PR 코멘트로 전달한다. 리뷰에서 요청한 수정은 다음 단계로 넘어가기 전에 반영한다.
```

## 구조 한눈에

| 층 | 파일 | 역할 |
|---|---|---|
| 게이트웨이 | `labhq/gateway/server.py` | WebSocket(러너·클라이언트), REST, 웹 사무실 서빙, 승인 저장소 |
| 오케스트레이터 | `labhq/orchestrator/cso.py` | 브리핑 → 계획(JSON DAG) → 병렬 실행 → 리뷰 → 보고, HPC 수면/기상, 예산 |
| 러너 | `labhq/runner/daemon.py` | 게이트웨이에 outbound 접속, 태스크 실행, MCP 배선, 잡 감시 |
| 어댑터 | `labhq/adapters/*.py` | claude_code · codex · gemini · cli(자체 에이전트) · mock |
| 도구 | `labhq/tools/*.py` | SGE/PBS 스케줄러, hpc_mcp, approval_mcp(권한 프롬프트) |
| 파견직 | `labhq/recruit/paper2agent.py` | 채용 → 오퍼레터 → 수습 → 계약 → 인재풀 |
| GitHub | `labhq/integrations/github.py` | 요청별 이슈, 코멘트, 보고서 커밋, 공개 가드, @codex 리뷰 |
| 웹 | `labhq/web/index.html` | 2.5D 사무실(데모/라이브), 승인, DAG, 메신저, HPC 랙 |
| 직원 | `agents/core/*.yaml` | 정규직 11명 (엔진·모델·도구·프롬프트) |

이벤트 프로토콜과 설정은 `README.md` §7–8, 알려진 한계는 §10을 보세요.

## 결정 대기 (PI)

1. bioinfo-agent 실행 방식
2. HPC 설정값: scheduler(sge/pbs), PE 이름, 메모리 리소스, 기본 큐, 로그인 노드 ssh 여부
3. 거버넌스(정부) 층과 Yuan의 구성
