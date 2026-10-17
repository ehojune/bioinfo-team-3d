## 결론

PR #723의 P1 다섯 건을 게이트와 메모리 두 부류로 수정했습니다. Fixes #706. Fixes #709.

## 바뀐 것

- 내부 구역 읽기는 출력 모양과 무관하게 같은 승인을 받습니다. 카드는 `내부 자료 행 출력 — 거절 권고` 또는 `머리줄·집계로 보임 — 출력 범위 확인`으로만 구분합니다.
- Windows는 self-Job wrapper가 실제 CLI보다 먼저 Job에 들어갑니다. POSIX는 user `systemd-run --scope`의 `MemoryMax`를 우선 쓰고, 없으면 기본 40%의 프로세스별 `RLIMIT_AS`로 내립니다.

## 확인

- 기존 head에서 새 회귀 test 9건 실패를 확인했습니다.
- 게이트 관련 pytest: 328 passed, 12 skipped.
- 메모리·doctor pytest: 69 passed, 1 skipped. Windows 128 MiB 상한도 실제 강제했습니다.
- `scripts/check_public.sh` 통과. CI는 push 뒤 확인합니다.
- `docs/manual.md`: 156,110자 → 156,179자(+69).
