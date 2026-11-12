# labhq 매뉴얼

처음이라면 [README](../README.md)의 "웹으로 시작하기"부터 보세요. 이 문서는 그다음입니다: 명령줄, 설정, 안전 장치, 연구 lane, 알려진 한계.
PI의 질문과 결정은 [PI Q&A](pi-qa.md)에, 개발 규칙과 작업 큐는 [AGENTS.md](../AGENTS.md)·[HANDOFF.md](../HANDOFF.md)에 있습니다.
영문판: [manual.en.md](manual.en.md), [research_protocol.en.md](research_protocol.en.md), [pi-qa.en.md](pi-qa.en.md), [vocabulary.en.md](vocabulary.en.md). 이 문서를 고치면 영문판도 같은 PR에서 고칩니다.
개발 checkout의 author·agent 표식 보존 규칙은 [Git hooks](reference/git-hook-authorship.md)를 참고하세요.

이 문서는 목차입니다. 본문은 장마다 `docs/manual/` 아래 파일에 있고, 아래 제목 밑의 링크가 그 절로 갑니다. 옛 `manual.md#…` 링크는 그대로 이 목차의 제목으로 연결됩니다.

| 장 | 무엇 |
|---|---|
| [PI에게 보이는 글](manual/01-pi-text.md#pi에게-보이는-글) | 평문 변환(`pi_language.py`)과 한국어 문체 점검(`korean_qc.py`) |
| [핵심 개념](manual/02-concepts.md#핵심-개념) | 자주 나오는 말 |
| [둘러보기: mock 데모](manual/03-demo.md#둘러보기-mock-데모) | API 키 없이 사무실 보기 |
| [설치와 첫 설정](manual/04-install.md#설치와-첫-설정) | `labhq init`, 인스턴스, HPC 상담, doctor |
| [띄우기와 접속](manual/05-run.md#띄우기와-접속) | gateway·runner, 브라우저·폰 |
| [웹 사무실](manual/06-web-office.md#웹-사무실) | 2.5D·3D 화면과 기능 |
| [명령줄(CLI)](manual/07-cli.md#명령줄cli) | `send`·`approve`·`verify`·`recruit` 등 |
| [구조와 이벤트](manual/08-structure.md#구조와-이벤트) | 요청 흐름, 아키텍처, 이벤트, REST, 환경 실패 |
| [직원과 도구](manual/09-staff-tools.md#직원과-도구) | 조직도, 연결된 도구, bioinfo-agent |
| [파견직 제도](manual/10-contract-staff.md#파견직-제도-paper2agent) | Paper2Agent 채용·계약 |
| [안전 장치](manual/11-safety.md#안전-장치) | 승인 게이트, 데이터 구역, PI 개인 경로, 예산 |
| [프로젝트별 GitHub 보고](manual/12-github-reports.md#프로젝트별-github-보고) | 이슈·보고서 커밋, 공개 가드 |
| [연구 lane과 검증](manual/13-research-lane.md#연구-lane과-검증) | CP1·CP2, claim 앵커, `labhq verify` |
| [의미 모델과 온톨로지](manual/14-semantic-model.md#의미-모델과-온톨로지) | 어휘·EDAM, 분야 점검표, CP1 입력 적합성, #1388 삭제안 |
| [설정 포인트](manual/15-config.md#설정-포인트) | 상태, HPC, 직원, 엔진, 참고 자료, 의미 모델 |
| [비교 bench](manual/16-bench.md#비교-bench) | labhq와 단일 세션 비교 |
| [알려진 한계](manual/17-known-limits.md#알려진-한계) | 첫 실행 때 확인할 것 |
| [구성요소와 상태](manual/18-components.md#구성요소와-상태) · [로드맵](manual/19-roadmap.md#로드맵) | 된 것과 남은 것 |
| [배경](manual/20-background.md#배경-the-virtual-biotech) · [파일 구조](manual/22-file-layout.md#파일-구조) · [라이선스](manual/23-license.md#라이선스) | |

## PI에게 보이는 글

[→ 본문 보기](manual/01-pi-text.md)

## 핵심 개념

[→ 본문 보기](manual/02-concepts.md)

## 둘러보기: mock 데모

[→ 본문 보기](manual/03-demo.md)

## 설치와 첫 설정

[→ 본문 보기](manual/04-install.md)

### 이 PC의 WSL에서 분석하기

[→ 본문 보기](manual/04-install.md#이-pc의-wsl에서-분석하기)

## 띄우기와 접속

[→ 본문 보기](manual/05-run.md)

### 웹에서 종료하기

[→ 본문 보기](manual/05-run.md#웹에서-종료하기)

## 웹 사무실

[→ 본문 보기](manual/06-web-office.md)

### 직원 제어

[→ 본문 보기](manual/06-web-office.md#직원-제어)

## 명령줄(CLI)

[→ 본문 보기](manual/07-cli.md)

### 과거 요청 검색

[→ 본문 보기](manual/07-cli.md#과거-요청-검색)

### 요청 수명과 PI 검토

[→ 본문 보기](manual/07-cli.md#요청-수명과-pi-검토)

### 단독 처리

[→ 본문 보기](manual/07-cli.md#단독-처리)

## 구조와 이벤트

[→ 본문 보기](manual/08-structure.md)

### 디스크 쓰기와 느린 디스크

[→ 본문 보기](manual/08-structure.md#디스크-쓰기와-느린-디스크)

### topic 점검표와 선행 연구 기준

[→ 본문 보기](manual/08-structure.md#topic-점검표와-선행-연구-기준)

### 보고서 공통 결과 계약

[→ 본문 보기](manual/08-structure.md#보고서-공통-결과-계약)

### 요청 묶음

[→ 본문 보기](manual/08-structure.md#요청-묶음)

### 전체 계층 구조와 모듈 아키텍처

[→ 본문 보기](manual/08-structure.md#전체-계층-구조와-모듈-아키텍처)

### 보고서 PDF

[→ 본문 보기](manual/08-structure.md#보고서-pdf)

### 로그인 대기

[→ 본문 보기](manual/08-structure.md#로그인-대기)

### 단계 실패와 누락된 증거 칸

[→ 본문 보기](manual/08-structure.md#단계-실패와-누락된-증거-칸)

### 환경 실패와 오류 서명

[→ 본문 보기](manual/08-structure.md#환경-실패와-오류-서명)

## 직원과 도구

[→ 본문 보기](manual/09-staff-tools.md)

### 연결된 도구

[→ 본문 보기](manual/09-staff-tools.md#연결된-도구)

### 공개 자원 조회 도구

[→ 본문 보기](manual/09-staff-tools.md#공개-자원-조회-도구)

### 역할·엔진·도구를 나눈 기준

[→ 본문 보기](manual/09-staff-tools.md#역할엔진도구를-나눈-기준)

### bioinfo-agent 연결하기

[→ 본문 보기](manual/09-staff-tools.md#bioinfo-agent-연결하기)

## 파견직 제도 (Paper2Agent)

[→ 본문 보기](manual/10-contract-staff.md)

### 세 가지 유형

[→ 본문 보기](manual/10-contract-staff.md#세-가지-유형)

### 작동 방식

[→ 본문 보기](manual/10-contract-staff.md#작동-방식)

### 주의

[→ 본문 보기](manual/10-contract-staff.md#주의)

## 안전 장치

[→ 본문 보기](manual/11-safety.md)

### 폭주 감지 (그림자)

[→ 본문 보기](manual/11-safety.md#폭주-감지-그림자)

### 승인 게이트와 쓰기 범위

[→ 본문 보기](manual/11-safety.md#승인-게이트와-쓰기-범위)

### 데이터 구역

[→ 본문 보기](manual/11-safety.md#데이터-구역)

### PI 개인 경로

[→ 본문 보기](manual/11-safety.md#pi-개인-경로)

### 전환 잡 작업공간

[→ 본문 보기](manual/11-safety.md#전환-잡-작업공간)

### 중단 조건 한눈에

[→ 본문 보기](manual/11-safety.md#중단-조건-한눈에)

### 승인·예산

[→ 본문 보기](manual/11-safety.md#승인예산)

## 프로젝트별 GitHub 보고

[→ 본문 보기](manual/12-github-reports.md)

### 결과 저장 위치

[→ 본문 보기](manual/12-github-reports.md#결과-저장-위치)

### 편집자와 정리본 확인

[→ 본문 보기](manual/12-github-reports.md#편집자와-정리본-확인)

### 등록한 프로젝트의 진행 보고

[→ 본문 보기](manual/12-github-reports.md#등록한-프로젝트의-진행-보고)

## 연구 lane과 검증

[→ 본문 보기](manual/13-research-lane.md)

### 켜기

[→ 본문 보기](manual/13-research-lane.md#켜기)

### CP1

[→ 본문 보기](manual/13-research-lane.md#cp1)

### 단계와 CP2

[→ 본문 보기](manual/13-research-lane.md#단계와-cp2)

### 리뷰·이어 가기

[→ 본문 보기](manual/13-research-lane.md#리뷰이어-가기)

### 보고서·verify

[→ 본문 보기](manual/13-research-lane.md#보고서verify)

### 최종 초안 수용(CP4)

[→ 본문 보기](manual/13-research-lane.md#최종-초안-수용cp4)

## 의미 모델과 온톨로지

[→ 본문 보기](manual/14-semantic-model.md)

## 설정 포인트

[→ 본문 보기](manual/15-config.md)

### 내 LLM-wiki 연결 (#827)

[→ 본문 보기](manual/15-config.md#내-llm-wiki-연결-827)

## 비교 bench

[→ 본문 보기](manual/16-bench.md)

## 알려진 한계

[→ 본문 보기](manual/17-known-limits.md)

## 구성요소와 상태

[→ 본문 보기](manual/18-components.md)

## 로드맵

[→ 본문 보기](manual/19-roadmap.md)

## 배경: The Virtual Biotech

[→ 본문 보기](manual/20-background.md)

### 우리 연구소로 옮기며 바꾼 점

[→ 본문 보기](manual/20-background.md#우리-연구소로-옮기며-바꾼-점)

## 내 저장소로 올리기

[→ 본문 보기](manual/21-publish.md)

## 파일 구조

[→ 본문 보기](manual/22-file-layout.md)

## 라이선스

[→ 본문 보기](manual/23-license.md)

## 통역 예약 회계

[→ 본문 보기](manual/24-interpreter-accounting.md)
