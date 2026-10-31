## 단계
#666 rev3 — 기본 off producer/observer 연결. Draft/hold이며 병합 판단 대기.

## 한 일
- Runner·adapter에 opt-in 사전 거부를 연결했습니다. 기본 동작과 기존 승인 gate는 유지합니다.
- producer 오류·성공 모양 receipt도 일반 CLI로 넘어가지 않습니다. observer 결과는 synthetic_only입니다.
- 실제 보호·인증 cleanup·engine/provider 호출은 미검증입니다.

## 테스트 결과
- `python -m pytest tests/test_boundary_wiring_666.py tests/test_recruit_disabled_static.py tests/test_boundary_observer.py tests/test_process_boundary.py tests/test_cli_exit_evidence.py tests/test_runner_env_inputs_674.py -q`: 215 passed, 10 skipped, 1 xfailed, 7 subtests passed. 기존 경계 설정이 없는 runner double도 기존 gate를 유지합니다.
- `scripts.ci_selection.validate_profile()`: 새 테스트 inventory 미등록으로 실패.
- `scripts/check_public.sh` 통과. `test_ci_boundary_preview.py`: 59 passed, 1 failed(같은 inventory 미등록).
- `notes_index.py --validate`: 배정된 status 파일명에 HHMM이 없어 실패. 최종 head CI·리뷰는 PR 댓글에서 추적합니다.

## 바꾼 파일
- `labhq/adapters/base.py`, `labhq/runner/daemon.py`, `tests/test_boundary_wiring_666.py`
- [연결 계약](../reference/sandbox-666-phase2.md), 이 기록, 전용 patch entry

## 막힌 점
- `config/ci/windows-boundary-v1.json`: 새 테스트 등록. 현재 claim 밖입니다.
- `docs/manual.md`, `docs/manual.en.md`: 기본 off/unsupported 상태와 embedding 연결 한계 반영. main에서 README/manual 변경 뒤 코드 커밋 2개가 쌓여 갱신 gate가 적용됩니다. 두 경로도 claim 밖입니다.
- status를 `docs/status/2026-11-01-0801-codex-boundary-wiring.md`로 옮길 새 경로도 필요합니다. 배정 경로에는 필수 HHMM이 없습니다.
- 위 경로의 claim 조정 후 최종 CI를 완료해야 합니다. 실제 보호·인증 cleanup은 별도 검증 대상입니다.

## 확인
- 실제 engine/provider·credential·provisioning·연구/HPC·배포 없음.
