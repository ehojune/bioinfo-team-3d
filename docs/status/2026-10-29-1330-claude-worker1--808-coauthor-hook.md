## 단계
공동 작성자 훅의 본문·trailer 구분과 모델명 — Closes #808.

## fe745fac 누락 사례를 먼저 가른 결과 (이 훅의 범위 아님)
- `fe745fac`("Merge main with reviewed Windows ACK diagnostics from PR873")은 부모 2개의 **merge commit**이고 author·committer가 모두 사용자(`ehojune <ehojune@gmail.com>`)이며 trailer가 없습니다. 에이전트 환경(`LABHQ_COMMIT_AGENT`·`CLAUDECODE`·`CODEX_*`·`GEMINI_CLI`)이 없는 셸에서 만든 커밋이라, 훅이 "알 수 없는 환경은 막지 않고 그대로 둔다"(`[ -z "$AGENT" ] && exit 0`)는 설계대로 동작한 것입니다. 이 훅의 결함이 아니라 에이전트 표식이 없는 환경에서 만든 통합 commit입니다. 이번 PR은 그 경로를 바꾸지 않습니다(알 수 없는 환경에서 커밋을 막지 않는 동작은 유지).

## 한 일 (`.githooks/commit-msg`)
- trailer 판정을 메시지 전체 grep에서 `git interpret-trailers --parse`(git이 마지막 문단에서 인식하는 trailer)로 바꿨습니다. 본문에 적은 `Co-authored-by:` 예시가 실제 작성자 표시로 오인돼 작성자가 빠지던 경로를 닫습니다.
- Codex 옛 주소(`noreply@openai.com`) 치환도 마지막 문단의 줄에만 적용합니다. 이전 `sed`는 본문 예시의 주소까지 바꿨습니다.
- 모델명: `LABHQ_COMMIT_MODEL`이 있을 때만 이름에 붙이는 규칙은 그대로고, 이미 이름만 있는 에이전트 trailer(`Claude <주소>`)는 환경에 모델이 있을 때만 그 이름에 채웁니다. 이미 괄호나 다른 이름(예: `Claude Sonnet 5.5`)이 있으면 그대로 두고, 환경에 모델이 없으면 아무것도 추정해 넣지 않습니다. 같은 메시지로 다시 돌려도 결과가 같습니다(amend 멱등).
- `AGENTS.md`·`CLAUDE.md`의 해당 줄에 위 규칙을 한 문장 추가했습니다.

## wrapper의 모델명 전달 확인 (저장소 밖 파일, 읽기만 함)
- `~/bin/agyq`(Gemini)는 `LABHQ_COMMIT_AGENT=gemini LABHQ_COMMIT_MODEL="$M"`을 export합니다. `~/bin/gh-agent-env`·`gh-agent-exec.py`(PR·댓글)도 같은 변수를 읽습니다.
- Claude Code 세션은 `CLAUDECODE=1`만 있고 `LABHQ_COMMIT_MODEL`은 export되지 않으며, Codex 쪽 wrapper도 이 저장소에서는 확인되지 않습니다. 그래서 Claude·Codex 커밋은 모델이 trailer에 자동으로 들어가지 않고, 모델이 직접 적은 trailer(이름에 모델 포함)에 의존합니다. 이 변수를 Claude·Codex 실행 경로에서 export하는 일은 저장소 밖(사용자 wrapper) 변경이라 여기서 하지 않았습니다.

## 테스트 결과
- `tests/test_git_hooks.py`: 기존 46건 그대로 + 새 8건 = 54건 통과. 새 시험은 임시 메시지 파일에 훅을 직접 돌립니다: 본문 예시(에이전트 자신의 주소 포함)가 있어도 실제 trailer가 추가되고 본문은 그대로, 옛 Codex 주소는 trailer 문단에서만 치환, 정상 trailer·누락 trailer 추가의 amend 멱등, 이름만 있는 trailer는 모델이 있을 때만 채움(다른 모델명은 유지), 알 수 없는 환경·미지의 에이전트는 막지 않고 그대로, 끝의 `#` 주석 줄이 있어도 trailer 인식.
- 수정 전 훅으로 같은 시험을 돌리면 본문 예시·옛 주소·모델 채움 3건이 실패합니다(나머지는 회귀 방지용으로 양쪽 통과).
- 미검증: 전역 훅(`~/.git-template/hooks`)과 합쳐진 실제 커밋 흐름의 새 시나리오, Windows가 아닌 환경의 `awk` 구현 차이(CI ubuntu·macOS가 확인).

## 바꾼 파일
.githooks/commit-msg, tests/test_git_hooks.py, AGENTS.md, CLAUDE.md와 변경 기록.

## 막힌 점
없음.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
