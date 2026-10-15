## 결론

Refs #589. PR #643에서 Windows의 `labhq down`이 supervisor·role과 자식 프로세스의 실제 종료를 기다리게 했습니다. 두 CI 실패의 `_rust.pyd` 잠금은 종료 요청 직후 임시 checkout을 지운 경합이었습니다.

## 바뀐 것

- Windows process tree의 각 process와 기록된 root PID 종료를 최대 10초 확인합니다. timeout이면 role·PID를 알리고 exit 2로 끝납니다.
- clean-install은 `down` 뒤 임시 checkout `.venv`의 Python process 잔존을 role·PID 이름으로 검사합니다.
- clean-install 임의 포트에서 PI 포트 8787·55921을 제외합니다.

## 확인

- 새 종료 test 2건: 수정 전 2 failed, 수정 뒤 2 passed.
- 관련 pytest: 119 passed, 1 skipped.
- 로컬 `python scripts/clean_install_check.py`, `scripts/check_public.sh` 통과.

## 남은 것

- PR #643 CI 확인 전입니다.
