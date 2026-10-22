# 참고 자료 (References)

[English](references.en.md) · [README](../README.md#더-읽기)

설계·구현·도구 연결에 참고한 자료입니다. 마지막 열은 저장소의 근거로 연결되며, 검토만 한 프로젝트는 따로 표시했습니다.
도구 연결은 설정에 등록됐다는 뜻이며 실제 접속 성공을 보증하지 않습니다.

| 이름 | 무엇 | labhq에서 어떻게 썼나 |
|---|---|---|
| [The Virtual Biotech](https://github.com/harrisongzhang/TheVirtualBiotech) | 가상 바이오텍 연구팀 | CSO·전문 직원·과학 리뷰 구조를 본뜨고 claim·근거 검증을 참고했습니다([배경](manual.md#배경-the-virtual-biotech), [검증](../README.md#labhq가-공들인-네-가지)). |
| [Paper2Agent](https://github.com/jmiao24/Paper2Agent) | 논문·코드를 연구 에이전트로 바꾸는 프로젝트 | 외부 변환 skill로 얻은 도구를 파견직으로 채용·수습·계약합니다([제도](manual.md#파견직-제도-paper2agent)). |
| [Munder Difflin](https://github.com/HarnessMD/munder-difflin) | CLI 에이전트 사무실 앱 | 사무실·오른쪽 탭·직원 카드 배치와 직원 제어의 운영 교훈을 참고했습니다([#64](https://github.com/ehojune/bioinfo-team-3d/issues/64), [#57](https://github.com/ehojune/bioinfo-team-3d/issues/57)). |
| [한컴 노마디안(Nomadian)](https://www.hancom.com/ai/aiOrchestrator/nomadian) | AI 직원이 협업하는 3D 업무 공간 | 3D 사무실의 공간 구성과 디자인을 참고했습니다([화면](../README.md)). |
| [EDAM](https://doi.org/10.7490/f1000research.1118900.1) | 데이터·방법·분야 온톨로지 | 용어 ID·라벨·조상을 추려 로컬 어휘에 연결했습니다([출처·판본](../labhq/vocab/NOTICE.md)). |
| [RO-Crate](https://w3id.org/ro/crate/1.1) · [Process Run Crate](https://w3id.org/ro/wfrun/process/0.5) | 연구 산출·실행 기록 규격 | 요청 묶음의 JSON-LD metadata에 RO-Crate 1.1과 Process Run Crate 0.5를 적용했습니다([매뉴얼](manual.md#요청-묶음)). |
| [nf-core](https://nf-co.re/) | Nextflow 분석 파이프라인 모음 | bioinfo-agent의 정형 분석과 워크플로 엔진 우선 정책에 씁니다([소개](../README.md#어떻게-일하나), [정책](manual.md#구성요소와-상태)). |
| [bioinfo-agent](https://github.com/ehojune/bioinfo-agent) | 바이오인포 분석용 Claude Code 에이전트 | 파이프라인 선택·실행·QC를 맡는 직원으로 연결했습니다([연결](manual.md#bioinfo-agent-연결하기)). |
| [open-science](https://github.com/aipoch/open-science) | 로컬 AI 연구 workbench | 권한 게이트·채용 출처·인용 확인·재현 기록 개선을 검토하고 후속 작업에 반영했습니다([조사·후속](https://github.com/ehojune/bioinfo-team-3d/issues/316)). |
| [BioMCP](https://github.com/genomoncology/biomcp) | 생의학 조회 CLI·MCP | 필요한 정보만 단계별로 조회하는 설계를 검토했습니다([#41](https://github.com/ehojune/bioinfo-team-3d/issues/41)). |
| [ToolUniverse](https://github.com/mims-harvard/ToolUniverse) | 과학 도구 검색·실행 프레임워크 | 도구 검색과 역할별 도구 묶음을 검토했으며 기본 연결에는 넣지 않았습니다([#64](https://github.com/ehojune/bioinfo-team-3d/issues/64)). |
| [OKF Loremaster](https://github.com/PFreda-Lab/okf-loremaster) | PubMed·PMC 문헌에서 근거를 뽑아 마크다운 코퍼스로 내보내는 멀티 에이전트 | 인용문·수치가 원문에 있는지 대조하는 코드를 옮겼습니다. 본문 조회 연결은 후속 작업입니다([조사](https://github.com/ehojune/bioinfo-team-3d/issues/620), [이식](https://github.com/ehojune/bioinfo-team-3d/issues/622)). |
| [PubMed](https://pubmed.ncbi.nlm.nih.gov/) | 생의학 문헌 DB | 문헌 검색과 과학 리뷰의 인용 대조에 연결했습니다([도구](manual.md#연결된-도구)). |
| [bioRxiv](https://www.biorxiv.org/) · [medRxiv](https://www.medrxiv.org/) | 생명·의학 preprint 서버 | 출판 전 연구를 찾는 MCP를 연결했습니다([도구](manual.md#연결된-도구)). |
| [ChEMBL](https://www.ebi.ac.uk/chembl/) | 화합물·생물활성 DB | 화합물 검색 MCP를 연결했습니다([도구](manual.md#연결된-도구)). |
| [ClinicalTrials.gov](https://clinicaltrials.gov/) | 임상시험 등록 DB | 임상시험 검색 MCP를 연결했습니다([도구](manual.md#연결된-도구)). |
| [Open Targets](https://platform.opentargets.org/) | 표적·질병 근거 플랫폼 | 표적 검색 MCP를 연결했습니다([도구](manual.md#연결된-도구)). |
| [Crossref](https://www.crossref.org/) | DOI·출판 metadata 서비스 | 선택형 live 출처 검사에서 DOI·NCBI 조회와 함께 씁니다([규약](research_protocol.md)). |
| [topic별 근거 논문](reference/topic_papers.md) | 43 topic의 논문 1,286편 | topic 점검표의 분석 방법·표준·벤치마크 근거를 모았으며 개별 인용은 기존 목록에 둡니다([어휘·점검표](vocabulary.md), [근거 기록](reference/README.md)). |
