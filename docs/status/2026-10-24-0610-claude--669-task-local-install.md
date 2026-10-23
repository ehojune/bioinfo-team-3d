Closes #669

직원이 받은 자료를 읽으려고 작업 폴더 안에 패키지를 설치하는 것을 계획이 막지 않게 하고, 읽을 도구가 끝내 없으면 CP2에서 보이게 했습니다.

- 계획 prompt: 작업 폴더 안 설치(`./.pylib`, 작업 폴더 venv, `./.rlib`, 판본 고정)는 기본 허용이며 `resource_limits`에 "새 패키지 금지"를 쓰지 않습니다. 시스템·공유 환경 변경만 금지합니다.
- 단계 prompt·작업 폴더 규칙: 필요한 패키지를 승인 없이 `./.pylib`에 설치해 읽습니다. 끝내 읽을 도구가 없으면 `status_detail`이 `도구 없음:`으로 시작하는 `unavailable` 근거 행을 남기고, 그 줄이 CP2 경고에 올라옵니다(`tool_missing_lines`).
- 정책 게이트는 이미 `--target ./.pylib` 설치를 허용하고 공유 환경·작업 폴더 밖 설치를 거부합니다(기존 테스트와 새 테스트로 확인). 설치한 패키지·판본 기록 규칙(`outputs/env/<step id>.txt`)은 기존 그대로입니다.
- 테스트: `tests/test_task_local_install_669.py` 4건, 관련 묶음 590건과 352건 통과(로컬, Windows, Python 3.12).
- 미검증: 실제 요청에서 모델이 새 규칙을 따르는지, 웹 CP2 카드 표시.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
