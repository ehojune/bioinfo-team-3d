## 단계
PR #766 이름 기반 광역 종료 guard — `73bb72b`, Refs #681.

## 수정
- P1 4321387908: find의 unquoted `{}` 뒤 후속 action을 이어 검사합니다. exec/execdir/ok/okdir·batch·wrapper 회귀를 추가했고 기존 prefix 거부와 shell/block lexer는 유지합니다.
- P1 4321387934: 이름 선택 pipeline의 `$_`/`PSItem`에서 나온 ID를 직접·괄호식·복사 변수로 전달해도 거부합니다. 명시 PID 조회와 이름 선택에서 유래하지 않은 PID는 기존 경계를 유지합니다.
- 4318107934는 공식 parameter 계약의 stale 근거 답글이 있어 제외했습니다. [지적별 근거·경계](../reference/process-stop-681-followup.md).

## 검증
- 새 회귀: 수정 전 **17 failed, 12 passed**. 수정 후 guard 6개 파일과 `tests/test_approval_closed_grammar.py`: **492 passed**.
- policy·flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·notes·diff 검사 PASS. 기존 정책의 누락 경로를 고쳤으며 상세 근거만 갱신했습니다.

## 한계
실제 shell/process/proc_tree·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2는 별도 범위입니다. CI·새 head 리뷰·병합·배포는 controller가 맡습니다.
