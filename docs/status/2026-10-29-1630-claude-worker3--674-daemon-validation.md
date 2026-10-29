## 단계
#674 후속 6·2: daemon 입력 연결과 manifest 내용·binding 대조.

## 한 일
1. `validate_runner_env(..., request_id)`: runner 자신의 `manifest.json` run 기록(요청·step·ok)을 확인한다.
2. daemon `_environment_refusal_reason`: 구조 검사 뒤 binding마다 위 검증을 호출하고(thread), 하나라도 어긋나면 엔진 시작 전 거부한다. 통과하면 시작하되 interpreter 별칭은 만들지 않는다. 이전에는 항상 거부했다.
3. manifest 내용과 binding 대조(후속 2): 같은 검증이 수집된 `runner-manifest.json`의 내용을 승인 계획의 binding과 정확히 대조한다.
4. manual 영·한 반영.

## 테스트 결과
- Windows: `test_runner_env_inputs_674.py` 등 89 passed, POSIX 전용 skipped. WSL Ubuntu-22.04: 같은 파일 60 passed.
- WSL 실측(daemon 경로, 표준 venv): 정상 환경은 거부 사유 None, 실패한 producer 기록은 `environment validation failed`로 거부, 임시 폴더 삭제 확인(`wsl_smoke3.py`).
- 미검증: 게이트웨이를 거친 전체 dispatch와 저장소 재시작 end-to-end(별도 단계), 실제 엔진 쓰기 보호, 환경 단계 완료 시점의 검증(지금은 소비 단계 시작 시점).

## 바꾼 파일
labhq/runner/step_inputs.py, labhq/runner/daemon.py, tests/test_runner_env_inputs_674.py, docs/manual.md, docs/manual.en.md, 패치노트·이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
