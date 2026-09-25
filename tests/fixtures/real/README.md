# 실제 CLI 스트림 fixture

- 캡처: 2026-09-25~26, Windows 11.
- 버전: Claude Code 2.1.282, codex-cli 0.155.0-alpha.16, gemini-cli 0.57.0, agy 1.2.11.
- `scripts/redact_stream.py`가 홈·임시·작업 경로, 사용자명, 이메일, ID, 키·토큰을 가립니다.
- JSONL은 줄마다 파싱 가능하게 유지합니다. `.stderr.txt`도 같은 규칙을 씁니다.
- 재캡처: `python scripts/probe_engines.py ENGINE --output-dir <저장소 밖 경로> --redact`.
- 새 원본은 로컬에 두고 가린 파일만 이 폴더에 복사한 뒤 `pytest -q`와 `scripts/check_public.sh`를 실행합니다.
- init 이벤트는 허용 목록 필드만 남긴다. 도구·스킬·에이전트 목록은 개수로, labhq_* 가 아닌 MCP 서버 이름은 `<external>`로 바꾼다. 캡처한 머신의 개인 도구 목록이 공개 저장소에 남지 않게 하려는 것이다.
