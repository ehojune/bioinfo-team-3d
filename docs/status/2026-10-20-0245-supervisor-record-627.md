## 결론

PR #768. 감독 정리 실패 시 PID 기록 보존은 기능 커밋 `4934d50`에 있습니다. P1 [4316951093](https://github.com/ehojune/bioinfo-team-3d/pull/768#discussion_r4316951093)의 patch-note SHA 불일치는 검토 대상 head `7c3f439e`에서 재현되지 않았습니다. 이번 변경은 공개 기록뿐입니다. Fixes #627.

## 근거·검증

- 공개 PR commits는 `4934d50`·`7c3f439e`이며, 기존 patch-note 행은 기능 커밋 `4934d50`과 일치합니다. 댓글의 `deacc2e`는 로컬 revision과 공개 commit API에서 찾지 못했습니다.
- `patch_notes.py check`: `origin/main..7c3f439e`, `4934d50^..7c3f439e`, `7c3f439e^..7c3f439e` 모두 PASS. `notes_index.py --validate` PASS.
- 선택한 mock-only pytest 11 passed. 최초 collection은 `markdown_it` 부재로 실패했고, 기존 임시 의존성을 읽기 전용 PYTHONPATH로 사용해 재실행했습니다. 설치·공용 venv 변경은 없습니다.
- stale 지적으로 분류해 새 실패 test와 기능 수정을 추가하지 않았습니다. 기존 test와 안전 gate는 유지합니다. patch-note는 중첩 디렉터리를 금지하는 notes gate에 맞춰 기존 `patch_notes/entries/supervisor-record-627.yaml`에 둡니다.

## 남은 것

P1 stale 근거를 인라인 답글로 남깁니다. push 뒤 CI snapshot만 확인하고 CI 대기·새 head 리뷰·P1=0 판정·병합·배포는 supervisor에게 넘깁니다. P2는 collector가 issue로 추적합니다. 실제 process/proc_tree integration·PI 운영·HPC·원자료·분석은 실행하지 않았습니다.
