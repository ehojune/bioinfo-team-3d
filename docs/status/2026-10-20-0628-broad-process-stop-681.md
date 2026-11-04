PR #766. test 커밋 `71e08b0`으로 [Windows 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39319738372/job/118290456835)를 보완했습니다. 앞선 guard·consult 수정과 남은 범위는 [이전 기록](https://github.com/ehojune/bioinfo-team-3d/blob/24e076a22473352bae72251eb09ae35cb384efad/docs/status/2026-10-20-0527-broad-process-stop-681.md)을 따릅니다.

- `test_late_selection_runs_editor_after_review_then_approval_and_mock_upload`에서 PDF 준비를 Event로 보류해 CI와 같은 `assert publish` 실패를 재현했습니다. PDF 생성·Git 판본 조회를 mock으로 격리한 뒤 **1 failed → 1 passed**, review/status 조합 **4 passed**입니다.
- 기존 검토 전 편집 금지·게시 승인 전 업로드 금지·문서·게시 결과 검증을 유지하고 PDF 호출 1회 검증을 추가했습니다. 제품 코드·timeout은 바꾸지 않았습니다.
- accident guard와 환경 변수 3개 유지, blocked **0건**. 전체 실패명·traceback은 로컬 artifact에 보존했습니다. Python 3.10 AST·public·diff·patch-note·notes 검사 PASS.

실제 PDF 생성·process/proc_tree integration·PI 운영·연구 실행은 미검증입니다. push 후 CI·봇·리뷰 대기와 병합·배포는 supervisor 담당입니다.
