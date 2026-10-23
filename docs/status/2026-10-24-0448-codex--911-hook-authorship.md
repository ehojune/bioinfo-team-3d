Closes #911

- 같은 configured identity의 amend·`-C/-c`에서도 Git author·timestamp와 현재 agent trailer를 보존합니다. `--reset-author`는 configured identity·새 timestamp를 유지합니다.
- 전역 commit-msg의 trailer 정리는 post-commit이 일반 commit/revert를 확인한 뒤 bot amend 안에서만 실행합니다. prepare-commit-msg의 source는 worktree별·Git process별로 기록하고 소비합니다.
- 수정 전 회귀 **10 failed·11 passed**, 수정 후 hook 회귀 **41 passed**. `--reset-author` timestamp 추가 검사 **3 passed**, Windows selection **7 passed**. 기존 replay·merge·revert·전역 hook 부재와 중단된 commit 뒤 후속 작업도 확인했습니다.
- public·diff·shell syntax·Python 3.10 AST·notes-index 검사 PASS. Windows 필수 hook cohort와 기존 검사는 유지했습니다.
- 머신 전역 hook·운영 설정은 변경하지 않았습니다. 최종 head CI·봇 리뷰 확인 뒤 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
