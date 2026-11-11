# 2026-11-11 claude-worker3 — #1373: HPC 세션 시험의 시간 경쟁

## 결론
`tests/test_hpc_session_1299.py`의 두 시험이 번갈아 실패한 원인은 시험용 fake gateway였다. "이른 입력"을 타이머 스레드와 읽기 루프의 경쟁으로 판정해, 부하가 큰 Windows runner에서는 ① 이른 입력을 읽기 전에 타이머가 끝나거나 ② 인사말을 쓴 직후 `ready`를 세우기 전에 정상 입력이 도착했다. 이제 인사말 전에 stdin에 아직 읽지 않은 입력이 있는지(PeekNamedPipe·select) 보고 판정하며, 인사말은 그 확인 뒤에만 쓴다. 제품 코드(`hpc_session.py`)는 원인이 아니어서 바꾸지 않았고, 시험을 약하게 하거나 건너뛰지 않았다.

## 바뀐 것
- `tests/fake_hpc_gateway.py`: `--kick-early-input` 판정을 명시적 신호(파이프 대기 입력)로, `ready` 이벤트 제거

## 실행한 것
- fake gateway를 쓰는 시험 3개 파일: 145 passed, 12 skipped
- CPU 부하 프로세스 6개를 띄운 채 두 시험을 동시에 12번 실행: 24/24 통과

## 미해결
- 없음
