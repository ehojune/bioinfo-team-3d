# 구독 한도 문구 fixture

- `claude.txt`: Claude Code 공개 재현의 `session limit · resets <time>` 형식.
- `claude-5hour.txt`: 2026-11-12 trial에서 실제로 받은 Claude Code의 `5-hour limit · resets <time> (<zone>)` 형식(#1419).
- `codex.txt`: 2026-10-02 PI PC의 Codex CLI 출력 형식(로컬 경로는 싣지 않음).
- `antigravity.txt`: Google 계정 CLI의 `capacity … quota will reset after <duration>` 형식.

실제 CLI를 호출하지 않고 parser 회귀에만 씁니다.
