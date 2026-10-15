# PR #630 — WSL 준비 판정·불확실 전송

| 항목 | 결과 |
|---|---|
| P1 4281071639 | 최초 runner·incarnation에 불확실 task 고정, 대상 변경 시 재전송 없이 uncertain |
| P1 4281071647 | 점검·활성화·gateway가 같은 준비 판정 사용, 미준비 상태는 native 선택 |
| 필수 준비 | bioinfo manifest·skill, Java·Nextflow, WSL Claude CLI·직원 로그인 |
| 검증 | 수정 전 회귀 실패 → 수정 후 통과, 관련 pytest 334 passed / 9 skipped, 전체 CJS 통과 |
| CI 보정 | Linux 임시 경로를 WSL 공유 경로로 잘못 사용한 새 test fixture를 수정 |
| 후속 인계 | push 뒤 새 P1 4281444658·P2 4281444670, 추가 수정 없음 |
| 범위 | 실제 WSL·PI 인스턴스 변경 없음, 리뷰 요청·병합 없음 |

근거: `tests/test_wsl_readiness.py`, `labhq/wsl_readiness.py`, `labhq/gateway/server.py`.
