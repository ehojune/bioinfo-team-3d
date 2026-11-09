# 2026-11-09 claude-worker3 — #1336 r5·r6: HPC 요청의 선행 전문 거절 완화

## 결론
저장된 선행 전문(precedent texts)이 있어도 요청 전체 HPC를 거절하지 않는다. 선행 전문은 CSO 계획과 local 단계에만 붙고, HPC 단계 task에서는 빠진다. project·reference 입력의 HPC 거절은 그대로다.

## 바뀐 것
- `labhq/orchestrator/execution.py`: `input_errors`에서 `precedent_texts` 거절 제거, `plan_rule` 문구 수정
- `labhq/orchestrator/cso.py`: `run_step`이 HPC면 meta에서 `precedent_texts`를 빼고, `_run_dag`는 whole-HPC 단계에 붙이지 않음
- `labhq/runner/daemon.py`: whole-HPC 단계에서 거절하는 대신 meta에서 뺌
- `tests/test_hpc_whole_request.py`: 옛 거절 단언 3개를 새 동작으로 바꾸고 시험 3개를 추가. 배정의 새 파일 대신 이 파일에 둬서 Windows inventory 등록이 필요 없다
- manual 04 한·영

## 실행한 것
- `pytest tests/test_hpc_whole_request.py`: 93 passed, 5 skipped
- 선행·실행 정책 관련 시험 27개 파일: 이번 변경 관련 실패 0 (로컬 mcp 판본 때문에 `test_annot_regulatory` 1건 실패, 무관)

## 미해결
- 없음. 전체 suite는 CI
