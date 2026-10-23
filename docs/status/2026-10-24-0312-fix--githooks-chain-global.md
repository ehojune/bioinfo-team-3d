# 전역 Git 훅 연결과 원저자 보존 (#902)

- 전역 commit-msg·post-commit이 있으면 새 에이전트 커밋을 위임해 봇 author·committer와 모델명을 남긴다. 없으면 기존 trailer 처리를 유지한다.
- P1 4362054678 수정: rebase·cherry-pick·충돌 후 continue는 원저자와 메시지를 보존한다. linked worktree의 상태 경로도 검사하며, 상태 파일이 지워진 continue는 reflog로 구분한다. reflog를 끈 저장소는 자체 trailer 처리만 쓴다.
- 임시 Git 저장소·가짜 전역 훅 검사: 수정 전 continue 원저자 변경 1 failed·3 passed → 수정 후 12 passed. checkout/worktree, rebase merge/apply, 신규 커밋·전역 훅 부재·reflog 비활성화를 확인했다.
- 수정 커밋 79579618의 author·committer는 Codex (gpt-6.1-sol) 봇이며 같은 에이전트 trailer는 제거됐다. 기존 커밋은 재작성하지 않았다.
- Windows CI의 새 test inventory 거부를 해결해 훅 검사 전체를 필수 cohort로 등록했다. selection 회귀 8 passed. patch-notes·notes·public·diff·Python 3.10 AST 검사 통과.
- 최종 head CI·같은 head 봇 P1=0 확인 뒤 병합한다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
