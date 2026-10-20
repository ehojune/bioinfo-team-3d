## 단계
PR #766 이름 기반 광역 종료 guard — `d83da2b`, Refs #681.

## 수정
- P1 4320847541: `else` 뒤의 literal 실행 명령과 wrapper를 검사합니다.
- P1 4320847550: 공백·줄바꿈을 구분해 literal 변수의 이름 선택·복사·pipeline·InputObject/Id 소비를 연결합니다. 덮어쓰기만으로 선택 상태를 지우지 않습니다.
- P1 4321129633: 중첩·double quote를 구분해 `$()` payload를 검사하고 이름 선택 PID를 외부 종료 명령에 연결합니다. 기존 backquote 거부와 PID·단순 조회·데이터 음성 사례를 유지합니다.
- 4318107934는 공식 parameter 계약의 stale 근거 답글이 이미 있어 제외했습니다. [지적별 근거·경계](../reference/process-stop-681-followup.md).

## 검증
- 원래 head `fea7042` 코드로 최종 새 회귀 52건을 재현: **26 failed, 26 passed**. 수정 뒤 guard 5개 파일과 `tests/test_approval_closed_grammar.py`: **463 passed**.
- policy·flat/nested fake broker가 card/event/submit·pending 없이 deny합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback을 로컬에 보존했습니다.
- Python 3.10 AST·integrations·public·patch-note·notes·diff 검사 PASS. manual은 해당 정책 문단만 갱신했습니다.

## 한계
실제 shell/process/proc_tree·PI·연구·HPC는 실행하지 않았습니다. 동적 명령 OS 격리·PID tracker #675·기존 P2와 이전 Hub 통합 검증은 별도 범위입니다. CI·새 head 리뷰·병합·배포는 controller가 맡습니다.
