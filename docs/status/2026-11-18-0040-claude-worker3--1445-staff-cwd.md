# #1445 r21 직원 셸 현재 폴더 (claude-worker3)

결론: Claude 직원 Bash가 명령마다 작업 폴더로 돌아온다. 승인 판정의 가정(명령은 작업 폴더에서 시작)과 실제 셸이 맞는다.

바뀐 것
- `labhq/adapters/claude_code.py`: `engine_env`에 `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1`. PI `engines.claude_code.env` 값보다 뒤라 끌 수 없다.
- `tests/test_claude_code_adapter_cwd_1445.py`: 기본값과 PI가 0을 넣은 경우.
- `docs/manual/11-safety(.en).md` 한 줄, Windows 경계 목록에 새 시험 파일.

실행한 것: 배포 venv에서 어댑터·staff env 관련 시험(1,568 통과, 무관한 `test_annot_alphagenome_atlas` SDK 시험 1건은 로컬 환경 실패), `tests/test_ci_*.py`(`filelock` 없는 로컬 환경 실패 5건 외 통과).

미해결: 실제 Claude CLI가 이 변수로 cwd를 되돌리는지는 배포 뒤 점검 요청에서 확인한다.
