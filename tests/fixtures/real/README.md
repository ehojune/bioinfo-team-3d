# 실제 CLI 스트림 fixture

- 캡처: 2026-09-25~26, Windows 11.
- 버전: Claude Code 2.1.282, codex-cli 0.155.0-alpha.16, gemini-cli 0.57.0, agy 1.2.11.
- resume fixture: `claude_resume_cost.jsonl`은 Claude Code 2.1.282, `codex_resume_usage.jsonl`은 codex-cli 0.159.2의 첫 호출과 같은 세션 재개 두 번을 캡처했습니다.
- `scripts/redact_stream.py`가 홈·임시·작업 경로, 사용자명, 이메일, ID, 키·토큰을 가립니다.
- JSONL은 줄마다 파싱 가능하게 유지합니다. `.stderr.txt`도 같은 규칙을 씁니다.
- 재캡처: `python scripts/probe_engines.py ENGINE --output-dir <저장소 밖 경로> --redact`.
- 새 원본은 로컬에 두고 가린 파일만 이 폴더에 복사한 뒤 `pytest -q`와 `scripts/check_public.sh`를 실행합니다.
- init 이벤트는 허용 목록 필드만 남긴다. 도구·스킬·에이전트 목록은 개수로, labhq_* 가 아닌 MCP 서버 이름은 `<external>`로 바꾼다. 캡처한 머신의 개인 도구 목록이 공개 저장소에 남지 않게 하려는 것이다.
- 읽기 전용 hook probe(2026-10-01, Claude Code 2.1.282): 작업 폴더 `.claude/settings.json`과 `--plugin-dir` plugin에 SessionStart·Stop hook을 두고 `--include-hook-events`를 붙여 돌렸다. `claude_read_only_hooks_control.jsonl`은 수정 전 읽기 전용 명령(plan, `Read,Glob,Grep`, `--setting-sources project,local`, plugin 전달)으로 hook 4개가 모두 돌았고, `claude_read_only_profile.jsonl`은 어댑터의 읽기 전용 명령으로 하나도 돌지 않았다. 두 번 다 `--settings`의 `Grep` 거부가 적용됐다.
- Windows 쓰기 경로 probe(#219, 2026-10-01, Claude Code 2.1.282, sonnet): TEMP 아래 작업 폴더에서 Git Bash `pwd`(`/tmp/...`)를 Write에 넣은 실행이다. `claude_windows_write_paths.json`은 probe마다 모델이 쓴 경로, 게이트가 받은 경로, 파일이 생긴 곳을 자리표시자로 적는다. `claude_write_tmp_respelled.jsonl`·`claude_write_tmp_outside_asks.jsonl`은 수정 뒤 게이트로 돈 실제 스트림이며, 작업 폴더의 `/tmp` 아래 부분을 `<WORKDIR_BELOW_TMP>`로 바꾼 뒤 `scripts/redact_stream.py`로 가렸다.
