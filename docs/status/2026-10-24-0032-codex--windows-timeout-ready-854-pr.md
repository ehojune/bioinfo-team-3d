## 단계
P1 — Windows 종료 테스트의 fixture 준비 대기. Refs #854.

## 한 일
- 부모·손자의 준비를 최대 10초 handshake로 확인한 뒤 기존 1초 timeout과 두 프로세스 종료를 검사합니다.
- startup_delay 0·1.5초를 검사하고 준비 실패 때도 fixture tree를 정리합니다. 제품 코드·timeout·종료 단언·skip/retry·receipt 조건은 그대로입니다.

## 테스트 결과
- 독립 Windows 검증: 패치 전 3 PASS, 패치 후 5회 × 2조건 = 10 PASS. 보고상 잔류 프로세스 0, WinError 5 없음. 같은 검증은 반복하지 않았습니다.
- [main CI 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39905683733/job/120257047265)는 timeout 단언 뒤 PID 파일이 없는 경우입니다. 원래 간헐 실패는 로컬에서 재현되지 않아 해결 확정 근거는 아닙니다.
- 준비를 마친 트리의 종료를 검사합니다. 기동 지연을 포함한 제품 timeout은 이 테스트의 검증 범위 밖입니다.
- Python 3.10 문법·diff·public·기록 검사는 아래 확인란에 실제 결과를 반영합니다.

## 바꾼 파일
- `tests/test_runner_safety.py`
- `patch_notes/entries/codex--windows-timeout-ready-854-pr.yaml`
- 이 단계 보고

## 막힌 점
- 2026-10-24 02:17 KST 확인: head `58a41aeb`의 [CI 39979410026](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39979410026) attempt 1은 Windows R parse timeout 1건으로 실패했습니다(3545 passed, 30 skipped, 51 subtests passed). coverage receipt 오류는 pytest 실패 뒤 발생했습니다. 실패 job 재실행은 아직 쓰지 않았습니다.
- 같은 head의 [봇 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/885#discussion_r4362288677)는 완료됐으며 이 보고의 오래된 CI 상태를 P1로 지적했습니다. 해당 문장을 고쳤습니다. 수정 head의 CI·리뷰·병합 결과는 [PR #885](https://github.com/ehojune/bioinfo-team-3d/pull/885)에 기록합니다. #854 전수/receipt 완료 조건은 남습니다.

## PI·Claude에게 물을 것
- 없음.

## 확인
- [x] Python 3.10 문법·diff 검사
- [x] `scripts/check_public.sh`
- [x] `scripts/patch_notes.py check --base origin/main`
- [x] `scripts/notes_index.py --validate`

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
