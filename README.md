# labhq — 혼자 운영하는 바이오인포 연구소 HQ (v0.2)

<!-- badges:start -->
[![Claude Code: 직원 8명 · opus/sonnet](https://img.shields.io/static/v1?label=Claude%20Code&message=%EC%A7%81%EC%9B%90%208%EB%AA%85%20%C2%B7%20opus%2Fsonnet&color=D97757&logo=claude)](https://code.claude.com/docs/en/overview)
[![Codex: 직원 3명 · gpt-6-astra/gpt-6-luna/gpt-6.1-sol](https://img.shields.io/static/v1?label=Codex&message=%EC%A7%81%EC%9B%90%203%EB%AA%85%20%C2%B7%20gpt-6-astra%2Fgpt-6-luna%2Fgpt-6.1-sol&color=10A37F)](https://github.com/openai/codex)
[![SGE: HPC scheduler](https://img.shields.io/static/v1?label=SGE&message=HPC%20scheduler&color=2F6F9F)](docs/manual.md#설정-포인트)
[![PBS: HPC scheduler](https://img.shields.io/static/v1?label=PBS&message=HPC%20scheduler&color=2F6F9F)](docs/manual.md#설정-포인트)
[![SLURM: HPC scheduler](https://img.shields.io/static/v1?label=SLURM&message=HPC%20scheduler&color=2F6F9F)](docs/manual.md#설정-포인트)
[![labhq MCP: approval · ask · hpc](https://img.shields.io/static/v1?label=labhq%20MCP&message=approval%20%C2%B7%20ask%20%C2%B7%20hpc&color=5B5BD6&logo=modelcontextprotocol)](docs/manual.md#연결된-도구)
[![ChEMBL: MCP · 화합물 검색](https://img.shields.io/static/v1?label=ChEMBL&message=MCP%20%C2%B7%20%ED%99%94%ED%95%A9%EB%AC%BC%20%EA%B2%80%EC%83%89&color=007EC6)](https://www.ebi.ac.uk/chembl/)
[![ClinicalTrials.gov: MCP · 임상시험 검색](https://img.shields.io/static/v1?label=ClinicalTrials.gov&message=MCP%20%C2%B7%20%EC%9E%84%EC%83%81%EC%8B%9C%ED%97%98%20%EA%B2%80%EC%83%89&color=007EC6)](https://clinicaltrials.gov/)
[![Open Targets: MCP · 표적 검색](https://img.shields.io/static/v1?label=Open%20Targets&message=MCP%20%C2%B7%20%ED%91%9C%EC%A0%81%20%EA%B2%80%EC%83%89&color=007EC6)](https://platform.opentargets.org/)
[![PubMed: MCP · 논문 검색](https://img.shields.io/static/v1?label=PubMed&message=MCP%20%C2%B7%20%EB%85%BC%EB%AC%B8%20%EA%B2%80%EC%83%89&color=007EC6&logo=pubmed)](https://pubmed.ncbi.nlm.nih.gov/)
[![bioRxiv / medRxiv: MCP · preprint 검색](https://img.shields.io/static/v1?label=bioRxiv%20%2F%20medRxiv&message=MCP%20%C2%B7%20preprint%20%EA%B2%80%EC%83%89&color=007EC6)](https://www.biorxiv.org/)
[![bioinfo-agent: Claude Code plugin](https://img.shields.io/static/v1?label=bioinfo-agent&message=Claude%20Code%20plugin&color=8A2BE2)](https://github.com/ehojune/bioinfo-agent)
[![Paper2Agent: skill · 파견직 채용](https://img.shields.io/static/v1?label=Paper2Agent&message=skill%20%C2%B7%20%ED%8C%8C%EA%B2%AC%EC%A7%81%20%EC%B1%84%EC%9A%A9&color=228B22)](https://github.com/jmiao24/Paper2Agent)

[![test: GitHub Actions](https://github.com/ehojune/bioinfo-team-3d/actions/workflows/test.yml/badge.svg)](https://github.com/ehojune/bioinfo-team-3d/actions/workflows/test.yml)
[![Python: 3.10+](https://img.shields.io/static/v1?label=Python&message=3.10%2B&color=3776AB&logo=python)](https://www.python.org/)
[![코드: GPL-3.0](https://img.shields.io/static/v1?label=%EC%BD%94%EB%93%9C&message=GPL-3.0&color=555555)](LICENSE)
[![문서·데이터: CC BY-SA 4.0](https://img.shields.io/static/v1?label=%EB%AC%B8%EC%84%9C%C2%B7%EB%8D%B0%EC%9D%B4%ED%84%B0&message=CC%20BY-SA%204.0&color=555555)](LICENSE-CC-BY-SA-4.0.txt)
[![패치노트: changelog](https://img.shields.io/static/v1?label=%ED%8C%A8%EC%B9%98%EB%85%B8%ED%8A%B8&message=changelog&color=5B5BD6)](patch_notes/README.md)
<!-- badges:end -->

Claude Code와 Codex를 **연구소 직원**처럼 부리는 바이오인포 플랫폼입니다.
웹 사무실에 요청을 적으면 CSO가 계획을 세우고, 직원들이 나눠 실행하고, 다른 회사 모델이 리뷰한 뒤 보고서가 나옵니다.
위험한 일과 돈이 드는 일은 PI가 웹이나 폰에서 승인합니다.

| 2.5D 사무실 (`/`) | 3D 사무실 (`/3d`) |
|---|---|
| ![2.5D 사무실: 요청 진행 그래프, 사내 메신저, 직원 책상](docs/media/office-25d.webp) | ![3D 종이숲 사무실: 직원 책상과 진행 중인 요청 패널](docs/media/office-3d.webp) |

mock 데모(`labhq demo --web`)를 15초 동안 녹화한 화면입니다. 요청 하나가 CSO 계획, 승인, 단계 진행을 거쳐 끝납니다.
정지 화면: [2.5D](docs/media/office-25d.png) · [3D](docs/media/office-3d.png)

## 어떻게 일하나

![요청이 흐르는 길: PI 요청 → 브리핑 → CSO 계획 → 직원 단계 → 과학 리뷰 → 최종 보고서. 아래는 PI 결정함 카드가 끼는 자리](docs/media/request-flow.svg)

- 직원 11명이 역할마다 다른 엔진·모델·도구를 씁니다. 과학 리뷰어는 일부러 다른 회사 모델입니다.
- 팀에 없는 방법이 필요하면 논문·코드를 [Paper2Agent](https://github.com/jmiao24/Paper2Agent)로 바꿔 **파견직**으로 채용합니다.
- HPC(SGE·PBS·Slurm)에 잡을 내면 직원 세션은 잡이 끝날 때까지 쉬었다가 같은 세션으로 이어 갑니다.
- 단계마다 작업 폴더에 지시문·산출·엔진·비용 기록이 남습니다.

## 예시: 공개 폐선암 데이터 분석

> GEO 공개 폐선암 데이터 GSE10072를 내려받아 종양 대 정상 차등발현, GSEA, STRING 허브, 군집을 분석해 줘. 같은 환자 짝이 있으면 그 구조를 반영해 줘.

- 1시간 17분, 12단계. PI는 카드 세 장(설치 확인, 계획 승인, 근거 승인)에 답했습니다.
- 짝 33쌍을 반영한 모형으로 DE 유전자 554개를 찾았고, 종양에서 세포 증식 경로가 높았습니다. 원 논문의 표지 유전자도 재현됐습니다.
- 보고서의 수치마다 근거 표시가 달려 있고, 다른 회사 모델의 과학 리뷰를 통과했습니다.

[요청 원문, 카드, 그림, 보고서 보기](docs/examples/gse10072/README.md)

## 의미 모델과 온톨로지

![의미 모델과 온톨로지: 끝난 요청을 그림자 계산이 출처 모델과 객체 뷰로 묶고, 산출 종류 이름표(labhq 어휘·EDAM)로 단계 입력이 맞는지 센다. A/B는 연구 요청 절반에만 재사용 후보를 준다](docs/media/semantics-model.svg)

요청이 쌓이면 지난 결과를 다시 쓰고 맞지 않는 입력을 일찍 알아채고 싶어집니다. 그 준비로 두 가지를 둡니다.

- **의미 모델**: 끝난 요청의 기록을 묶은 지도입니다. 출처 모델은 "이 결과를 누가 만들었고 다시 써도 되나"에, 객체 뷰는 "누가 무엇을 맡았고 무엇이 승인을 기다리나"에 답합니다.
- **온톨로지**: 지도에 쓰는 낱말 사전입니다. 산출 데이터 종류를 labhq 어휘 38개로 적고, 그중 32개는 생물정보학 공용 사전 [EDAM](https://github.com/edamontology/edamontology) 용어에 잇습니다. 이 이름표로 단계 입력이 방법에 맞는지 맞음·틀림·모름으로 셉니다.

기본은 꺼져 있고 켜도 기록만 합니다(그림자). `semantics: ab`는 연구 요청 절반에만 재사용 후보를 CSO 계획에 참고로 줘서 효과를 견줍니다. 자세한 설명은 [매뉴얼](docs/manual.md#의미-모델과-온톨로지)에 있습니다.

## 웹으로 시작하기

### 1. 먼저 둘러보기 (API 키·클러스터 없이)

```bash
git clone https://github.com/ehojune/bioinfo-team-3d.git
cd bioinfo-team-3d
pip install -e ".[dev]"
labhq demo --web
```

출력된 `http://127.0.0.1:8787/?token=…`을 열면 mock 팀이 일하는 사무실이 뜹니다. 폰으로 보려면 PC와 폰을 같은 Wi-Fi에 두고 `labhq demo --web --phone`을 실행한 뒤 출력된 `/3d` URL을 엽니다.

### 2. 설치와 설정

필요한 것: Python 3.10 이상, Git, 직원으로 쓸 CLI(Claude Code·Codex)의 설치와 로그인. npm으로 설치한 CLI는 Node.js도 필요합니다.

```bash
labhq init                                   # 설정 파일과 gateway token을 만들고 점검
export LABHQ_CONFIG=$PWD/config/labhq.yaml   # PowerShell: $env:LABHQ_CONFIG = "$PWD\config\labhq.yaml"
labhq doctor                                 # 설정·엔진·직원·계산 도구 점검
```

`labhq init`은 HPC와 bioinfo-agent 경로를 묻습니다. 직원 전용 로그인 폴더가 필요하면 사람이 칠 로그인 명령도 알려 줍니다.

### 3. 띄우기

터미널 두 개에서 실행합니다. 2단계의 `export`는 그 터미널에만 걸리므로 새 터미널에서도 `LABHQ_CONFIG`를 다시 지정합니다(또는 `labhq --config config/labhq.yaml runner`처럼 명령 앞에 붙입니다). 빠뜨리면 그쪽이 기본 token으로 떠서 gateway와 runner가 서로 붙지 못합니다.

```bash
labhq gateway   # 요청·승인·이벤트를 저장하고 웹 사무실을 연다
labhq runner    # 직원 CLI를 실제로 돌린다. gateway로 나가는 연결만 쓴다
```

브라우저에서 `http://127.0.0.1:8787/?token=<client_token>`을 엽니다. `client_token`은 설정 파일의 `gateway.client_token` 값이고, 한 번 열면 그 기기에 저장됩니다.

### 4. 첫 요청

1. 아래 입력창에 요청을 적습니다. 처음엔 작은 공개 데이터 요청이 좋습니다(예: "공개 펭귄 데이터 QC 요약").
2. CSO가 확인 질문을 하면 선택지를 고르고 **답하고 진행**을 누릅니다.
3. 승인할 일은 오른쪽 **결정** 탭(결정할 일)에 카드로 옵니다. 승인하거나 거절하면 이어 갑니다.
4. **작업판**에서 단계 진행과 산출을 보고, 끝나면 **최종 보고서**를 펼칩니다. 보고서를 두고 더 물을 땐 **이어 묻기**를 씁니다.
5. 실행 중 요청을 고르면 입력창 기본값은 **이 요청에 메모**입니다. 메모는 이미 도는 단계가 아니라 다음 단계부터 전달됩니다.

한 직원에게 바로 맡기려면 입력창에서 그 직원을 고르거나 노란 메모를 책상에 끌어다 놓습니다.
GitHub URL·DOI·데이터 경로는 **참고** 버튼으로 붙입니다.
폰 연결은 매뉴얼의 [띄우기와 접속](docs/manual.md#띄우기와-접속)(Tailscale)과 [웹 사무실](docs/manual.md#웹-사무실)(홈 화면에 추가)에 있습니다.

## 안전하게 쓰려면

- HPC 제출, 위험한 셸 명령, 작업 폴더 밖 쓰기, 예산 초과, 파견직 채용은 PI가 정합니다.
- 통제(DUA) 데이터 원본은 파일 도구로 열지 않고 HPC 잡 안에서만 다룹니다.
- 직원은 PI 계정으로 돌지만 `~/.ssh`·브라우저 프로필 같은 개인 경로는 가립니다. 이 차단은 Claude 직원에게 걸리고, Codex 직원(기본 11명 중 3명)은 지침만 받습니다. 경로 가드는 sandbox가 아니어서 작정한 직원까지 막지는 못합니다.

자세한 기준은 [안전 장치](docs/manual.md#안전-장치)에 있습니다.

## 로드맵

| 버전 | 목표 |
|---|---|
| v0.25 | PI가 공개 데이터로 labhq를 시험한다 |
| v0.5 | PI가 자기 공개 데이터로 로컬에서 연구한다 |
| v0.75 | PI가 HPC에서 공개 데이터로 연구한다 |
| v0.9 | PI가 통제 데이터로 연구한다(HPC 위) |
| v0.95 | 다른 연구자가 문서만 보고 설치해 공개 데이터로 한 건을 끝낸다 |
| v1 | 다른 연구자가 자기 통제 데이터로 연구 문제를 두 개까지 푼다 |
| v1.25 | 연구를 넘어 논문을 쓴다 |

버전마다 통과 기준이 있고, 채우면 다음 버전으로 넘어갑니다. 기준과 지금 상태, 남은 일은 [매뉴얼의 로드맵](docs/manual.md#로드맵)에 있습니다.

## 더 읽기

| 문서 | 읽는 사람 |
|---|---|
| [매뉴얼](docs/manual.md) | 명령줄, 설정, 안전 장치, 연구 lane, 알려진 한계까지 깊이 보려는 사람 |
| [PI Q&A](docs/pi-qa.md) | PI가 물은 것과 답, 내린 결정 |
| [연구 수행 규약](docs/research_protocol.md) | 연구 lane의 계약과 checkpoint |
| [패치노트](patch_notes/README.md) | 바뀐 내용 |
| [AGENTS.md](AGENTS.md) · [HANDOFF.md](HANDOFF.md) | 이 저장소를 개발하는 에이전트 |

코드는 [GPL-3.0-or-later](LICENSE), 문서·그림·데이터 표는 [CC BY-SA 4.0](LICENSE-CC-BY-SA-4.0.txt)입니다. 예외는 [매뉴얼의 라이선스](docs/manual.md#라이선스)에 있습니다.
