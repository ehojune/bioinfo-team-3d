## 결론

#549 D: Paper2Agent를 직원 설정에 설치하고 독립 `doctor`의 필수 검사로 바꿉니다.

## 바뀐 것

- Claude: 직원 `CLAUDE_CONFIG_DIR/labhq-paper2agent/skills/paper2agent`에 설치하고 recruiter에만 plugin을 연결합니다. 미설정 시 `~/.labhq/claude-staff`를 쓰며 개인 설정 격리를 유지합니다.
- Codex: recruiter의 실제 엔진과 직원 `CODEX_HOME/skills/paper2agent`를 따릅니다. 계약할 직원의 엔진과 구분합니다.
- 미설치·가시성 오류는 fail. `labhq doctor --fix`는 setup과 같은 함수로 설치 후 재검사하며 다운로드 실패 이유를 표시합니다. commit 고정 규칙은 유지합니다.
- `init`은 설치 검사를 미룹니다. mock·데모 doctor는 skip하므로 clean-install의 초기화 순서를 유지합니다.
- 옛 개인 경로의 사본은 `.labhq-source.json`에 source·유효한 commit이 있을 때만 정리합니다. 사용자 사본과 링크는 보존합니다.

## 검증

- 새 회귀 테스트 11개: origin/main `e219c68`에서 실패, branch에서 통과. init 예외 테스트 1개 추가.
- Windows 전체 pytest: **5629 passed, 66 skipped**. `%TEMP%/pt-p2asetup` 삭제 확인.
- 관련 테스트 436 passed·4 skipped, init 수정 후 관련 테스트 120 passed.
- `scripts/check_public.sh`, `integrations.py --check`, `notes_index.py --validate` 통과. PR 패치노트 검사 후 push.
- CI는 PR checks에서 확인합니다. PI·trial 실행·설정과 실제 사용자 skill 폴더는 변경하지 않았습니다.

## 남은 지적

- Codex는 기존 로그인·Windows sandbox의 CODEX_HOME을 공유하므로 같은 홈의 다른 Codex 직원도 skill을 봅니다.
- 실제 직원 CLI의 skill 로딩은 실행하지 않았습니다. 임시 홈 설치·plugin preflight·실행 명령으로 검증했습니다.

Refs #549

🤖 Generated with Codex (model slug unavailable) for the labhq dev lead
