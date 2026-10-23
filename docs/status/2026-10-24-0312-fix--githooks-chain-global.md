# 전역 Git 훅 연결과 원저자 보존 (#902)

- 전역 commit-msg·post-commit이 있으면 일반 새 커밋·revert를 위임해 봇 author·committer와 모델명을 남긴다. 전역 훅 부재·merge 커밋·reflog 비활성화는 자체 trailer 규칙을 쓴다.
- P1 4362054678 수정: rebase·cherry-pick·충돌 후 continue는 원저자와 메시지를 보존한다. linked worktree의 상태 경로도 검사하며, 상태 파일이 지워진 continue는 reflog로 구분한다. reflog를 끈 저장소는 자체 trailer 처리만 쓴다.
- 임시 Git 저장소·가짜 전역 훅 검사: continue 원저자 변경 1 failed·3 passed, merge·revert 각 2 failed, amend·명시 author 3 failed를 수정 전 재현했다. 수정 후 20 passed: checkout/worktree, rebase merge/apply, 신규 커밋·전역 훅 부재·reflog 비활성화·merge·revert·연속 cherry-pick·amend·명시 author를 확인했다.
- P2 4362840932 수정: 자동 merge에는 post-commit이 호출되지 않아 전역 commit-msg만 실행하면 표식이 사라졌다. merge는 두 훅 모두 전역 위임을 생략해 trailer를 보존한다.
- P2 4362909633 수정: 충돌 없는 revert는 commit-msg를 건너뛴다. 새 inverse commit의 post-commit에서 전역 author 처리를 실행해 봇 작성자를 남긴다.
- P1 4363000119 수정: amend는 기존 author를 유지한다. 명시한 다른 author도 보존하며 author와 committer가 다른 메시지는 자체 trailer 규칙을 쓴다.
- 수정 커밋 79579618의 author·committer는 Codex (gpt-6.1-sol) 봇이며 같은 에이전트 trailer는 제거됐다. 기존 커밋은 재작성하지 않았다.
- Windows CI의 새 test inventory 거부를 해결해 훅 검사 전체를 필수 cohort로 등록했다. selection 회귀 8 passed. patch-notes·notes·public·diff·Python 3.10 AST 검사 통과.
- 최종 head CI·같은 head 봇 P1=0 확인 뒤 병합한다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
