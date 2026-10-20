## 단계
PR #766 이름 기반 광역 종료 guard — `d16dd8e`, Refs #681.

## 수정
- P1 4321588842: skill의 이름/광역 선택 종료를 거부합니다. short/long option·붙인 operand·약어를 읽고 no-action·help/version·signal listing과 명시 PID를 구분합니다. operand로 쓰인 조회 표식은 예외가 아닙니다.
- P1 4321588847: pidof를 이름 선택 PID producer로 추적해 command substitution·pipeline·xargs와 연결합니다. skill no-action의 이름 선택 PID를 종료에 넘기는 경로도 거부합니다.
- 4318107934는 기존 공식 parameter 계약의 stale 근거 답글이 있어 제외했습니다. [근거·회귀·경계](../reference/process-stop-681-followup.md). manual은 해당 정책 문단만 갱신했습니다.

## 검증
- 새 회귀: 수정 전 **22 failed, 30 passed**. 수정 후 guard 7개 파일과 `tests/test_approval_closed_grammar.py`: **544 passed**.
- policy·flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·notes·diff 검사 PASS.

## 한계
실제 skill/pidof·process/proc_tree·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2는 별도 범위입니다. CI·새 head 리뷰·병합·배포는 controller가 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
