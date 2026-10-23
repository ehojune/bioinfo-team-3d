Closes #911

- 같은 configured identity의 amend·`-C/-c`에서도 Git author·timestamp와 현재 agent trailer를 보존합니다. `--reset-author`는 configured identity·새 timestamp를 유지합니다.
- 전역 commit-msg는 post-commit이 일반 commit/revert를 확인한 뒤 private message copy를 정리합니다. 모델명을 먼저 전달하고 bot amend에 copy를 반영합니다. source·임시 상태는 worktree별로 격리합니다.
- 수정 전 reuse 회귀 **10 failed·11 passed**, 모델 전달 회귀 **2 failed**. 수정 후 hook **43 passed**, rewrite 거절·실패·직원 세션 **3 passed**(총 46건), Windows selection **7 passed**. author·timestamp·agent 표식, replay·merge·revert·전역 hook 부재와 중단 뒤 후속 작업을 확인했습니다.
- public·diff·shell syntax·Python 3.10 AST·notes-index 검사 PASS. Windows 필수 hook cohort와 기존 검사는 유지했습니다.
- 머신 전역 hook·운영 설정은 변경하지 않았습니다. 최종 head CI·봇 리뷰 확인 뒤 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
