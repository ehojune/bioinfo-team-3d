## 단계
PR #766 광역 종료 guard 후속 — `715e89e`, Refs #681.

## 수정
- P1 4321811293·4321811304: 이름 선택 expression의 Id와 process substitution의 PID를 종료 consumer에 연결합니다.
- P1 4322020342·4322020348: Invoke-Expression·watch의 literal 실행 payload를 검사합니다.
- P1 4322020355·4322020358: kill의 nonpositive PID와 이름 선택 객체의 Kill()을 거부합니다. 조회·signal operand·명시 PID·문자열 데이터의 음성 회귀를 유지합니다.
- 4318107934는 기존 [공식 계약의 stale 근거 답글](https://github.com/ehojune/bioinfo-team-3d/pull/766#discussion_r4320835135)로 제외합니다. [세부 근거](../reference/process-stop-681-followup.md). manual은 기존 정책 문단만 갱신했습니다.

## 검증
- 새 문자열/fake 회귀: 수정 전 **43 failed, 41 passed**. 수정 후 guard 8개 파일·text encoding: **587 passed**. 원래 test·assertion은 수정하지 않았습니다.
- policy와 flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. 사고 guard 로드·환경 변수 3개 유지, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff 검사 PASS. #800 merge의 ancestry를 확인했습니다. workflow·runner·공통 fixture는 변경하지 않았습니다.

## 한계
실제 process/proc_tree·shell·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2는 별도 범위입니다. 새 head CI는 조회·대기하지 않으며 controller가 CI·리뷰·병합·배포를 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
