## 단계
#674 후속 1: venv interpreter symlink 정책과 통합 fixture.

## 한 일
1. `validate_runner_env`: Linux·WSL에서 `.venv/bin/python`이 링크이면 직접 따라가(최대 8단계) ① venv 안의 일반 실행 파일(hard link 아님), ② `/usr/bin`·`/usr/local/bin`·`/usr/lib`·`/bin` 아래의 root 소유·group/other 쓰기 불가 실행 파일(그 루트까지의 폴더도 같은 조건)일 때만 받는다. 그 밖(루트 밖 대상, 쓰기 가능, 비실행, 순환, 체인 초과)은 거부한다. Windows 런타임은 이전처럼 링크를 거부한다. `HeldDir.readlink` 추가.
2. 통합 fixture `tests/test_runner_env_integration_674.py` 추가(codex-worker3 작성분).
3. manual 한·영에 허용 규칙을 적었다.

## 테스트 결과
- Windows: `test_runner_env_inputs_674.py` 50 passed, POSIX 전용 8 skipped. 통합 fixture 통과.
- WSL Ubuntu-22.04(Python 3.10, 임시 venv): 같은 test 파일 58 passed(링크 test 8개 포함).
- 실측 smoke(기존 smoke 재실행): 표준 `python3 -m venv` 결과(`python → python3 → /usr/bin/python3.10`)가 **통과**, 사용자 쓰기 가능 대상 링크는 거부, 임시 폴더 삭제 확인. 스크립트: `D:\.labhq\dev\tmp\claude-worker3\wsl_smoke2.py`.
- 미검증: 다른 배포판·conda·pyenv(거부됨), 실행 중 링크 교체(TOCTOU: 검증 primitive는 호출자가 보호를 유지해야 함), 실제 엔진 쓰기 보호, daemon 입력 연결.

## 바꾼 파일
labhq/runner/step_inputs.py, labhq/adapters/held_dir.py, tests/test_runner_env_inputs_674.py, tests/test_runner_env_integration_674.py, docs/manual.md, docs/manual.en.md, 패치노트·이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
