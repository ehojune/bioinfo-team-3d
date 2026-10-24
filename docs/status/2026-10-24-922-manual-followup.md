#922 설치 검사기 manual의 보존 옵션 안내를 보충합니다. #925는 이미 병합됐으나 이 단락은 예전 문구가 남아 있어 후속 수정합니다.

- 기본 호출의 자동 삭제, `--work-dir`·`--receipt`, 설치판·분리 로그·JSON의 로컬 전용 증거 경계를 한 단락에 반영했습니다. 나머지 manual은 변경하지 않았습니다.
- 검증: `git diff --check`, `python scripts/patch_notes.py check`, Git Bash `scripts/check_public.sh` 통과. 실행 코드 변경이 없어 설치 재실행은 하지 않았습니다.
- manual LF 기준 187,844→188,073자(+229). 최종 head CI·봇 검토 후 병합합니다.

Refs #922, #925.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
