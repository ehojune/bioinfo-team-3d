# Git hook authorship (#911)

Lookup-only: 저장소 `.githooks`와 전역 hook 연결의 판단 근거입니다.

| 작업 | 작성자·표식 |
|---|---|
| 일반 새 commit·revert | 전역 post-commit이 있으면 bot author·committer로 기록 |
| amend·`-C/-c` | Git이 정한 author·timestamp 보존, 현재 agent trailer 추가 |
| `--reset-author` | Git이 정한 configured identity·새 timestamp 보존, agent trailer 추가 |
| merge·전역 hook 부재·reflog 비활성화 | 자체 trailer 규칙 적용 |
| rebase·cherry-pick·continue | 원저자·기존 메시지 보존 |

- commit-msg는 우선 trailer를 남깁니다. post-commit은 reflog와 prepare-commit-msg의 message source로 전역 위임 여부를 정합니다. identity equality만으로 reuse를 판정하지 않습니다.
- 전역 commit-msg는 새 commit/revert가 확인된 뒤 private message copy를 정리하고 모델명을 먼저 전달합니다. 위임한 bot amend의 prepare-commit-msg가 그 copy를 반영합니다. [Git 문서](https://git-scm.com/docs/githooks#_prepare_commit_msg)대로 이 hook은 `--no-verify`에도 실행됩니다.
- source 기록은 `git rev-parse --git-path`로 worktree별로 두고 Git parent process에 묶습니다. 매 시도에 덮어쓰고 post-commit에서 소비합니다. 중단된 시도의 기록은 다음 작업에 적용하지 않습니다.
- 검사: `tests/test_git_hooks.py`의 isolated history·가짜 전역 hooks. 머신 전역 hook·운영 설정은 바꾸지 않습니다.
