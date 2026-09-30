# Claude 기준선 권한 (PR #77)

- `cwd`는 해당 arm, `--permission-mode acceptEdits`, `Edit(//<arm>/**)`로 파일 편집·저장을 허용한다. 추가 작업 폴더는 없다.
- 모든 arm의 공통 prompt는 `answer.md` 저장을 요청한다. Claude가 쓴 보고서는 최종 채팅의 “저장 완료” 요약으로 덮어쓰지 않는다. 파일이 없거나 비었으면 채팅 본문으로 저장한다.
- 전용 `PreToolUse` hook은 Write/Edit/NotebookEdit 경로를 resolve해 arm 밖·symlink escape·`.claude`/`.git` 쓰기를 거부한다. 개인 설정 제외·Agent/Task/팀 도구 금지는 유지한다.
- Bash는 `pwd`, `wc -l <arm 안 파일>`, `echo`와 arm 안 `>`/`>>`, `mkdir [-p]`, `touch`만 자동 승인한다. 복합 명령·치환·wrapper·PowerShell·임의 interpreter/script는 거부한다.
- Native Windows는 Claude OS sandbox가 없어 범용 Bash를 자동 허용하지 않는다. **파일 쓰기와 단순 명령은 허용하지만 Codex workspace-write의 임의 script 실행과 완전히 같지는 않다.** hook은 OS sandbox가 아니다.
- 테스트는 dry-run argv와 실제 hook의 안/밖 판정·실행 cwd를 확인한다. 모델 CLI 재실행·OS 차단 실험은 하지 않았다. symlink 테스트는 생성 권한이 없으면 skip한다.

근거: [Claude 권한 규칙](https://code.claude.com/docs/en/permissions)과 [sandbox 지원 플랫폼](https://code.claude.com/docs/en/sandboxing). 구현은 `labhq/bench_permissions.py`, 회귀 검사는 `tests/test_bench_permissions.py`.
