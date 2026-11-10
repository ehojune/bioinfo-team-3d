# 2026-11-10 claude-worker3 — #1368: 원격 산출 목록의 범위

## 결론
원격 산출 목록은 잡이 작업 폴더에 만든 conda·mamba 환경(`conda-meta`), Python venv(`pyvenv.cfg`), `__pycache__`를 세지 않고 "environment folder"로만 적는다. 목록을 읽지 못한 잡(completed지만 manifest 없음)도 같은 이름으로 다시 낸 성공 잡이 있으면 단계를 막지 않는다. 선언 산출이 환경 폴더 안에 있거나 실제 산출이 상한을 넘으면 그대로 거절한다. #1331 6회차(잡 8960)가 이 경로로 실패했다.

## 바뀐 것
- `labhq/hpc_transfer.py` `REMOTE_DATA_HELPER`: scan에서 환경 폴더를 내려가지 않고 skipped에 `environment folder`로 남김
- `labhq/runner/daemon.py` `_remote_step_outputs`: 환경 폴더 skip은 선언 디렉터리를 incomplete로 만들지 않음(선언 산출이 그 안이면 여전히 거절), 짝짓기의 "성공"을 회수·manifest까지 된 잡으로 한정하고 그보다 먼저 낸 같은 이름의 쓸 수 없는 잡은 건너뜀
- `tests/test_hpc_whole_request.py`: 목록 못 읽은 잡 대체, 환경 폴더 skip과 선언 산출, 원격 scan(Linux CI)
- manual 15 한·영

## 실행한 것
- 원격 산출·회수 관련 시험: 220 passed, 35 skipped (POSIX helper 시험은 Windows에서 건너뜀, Linux CI에서 확인)

## 미해결
- 진행 중인 계획 세션에 승인한 예산 상향이 적용되지 않는 문제(#1368 후속 후보)는 범위 밖
