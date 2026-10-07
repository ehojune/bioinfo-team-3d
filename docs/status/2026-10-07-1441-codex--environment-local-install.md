# 2026-10-07 · PR #460

## 결론

환경 단계도 자기 작업 폴더 안 환경에만 설치합니다. Claude의 사전 허용 규칙과 승인 게이트에 있던 환경 단계 예외를 함께 좁혔습니다.

## 바뀐 것

- `.venv` interpreter, 작업 폴더의 `--target`, conda prefix, uv `--python`은 허용합니다.
- bare 설치, 폴더 밖 경로, 경로 이탈·링크·충돌 옵션·같은 명령의 설치 환경변수 우회는 거절합니다.
- 환경 계획은 Windows·POSIX의 `.venv` Python 경로를 직접 지정합니다. 뒤 단계의 `./.pylib`·`./.rlib` 제한은 유지합니다.

## 실행한 것

- 관련 pytest 319 passed·12 skipped, CSO pytest 196 passed, 마지막 직접 판정 67 passed·1 skipped.
- Windows 전체 pytest: 4351 passed·56 skipped. 매번 단일 basetemp를 쓰고 종료 후 지웠습니다.
- `scripts/check_public.sh`, `python scripts/patch_notes.py check`, `python scripts/notes_index.py --validate` 통과.
- 프롬프트 변경에 맞춘 SHA 두 값만 갱신했으며 정확한 SHA 비교와 schema 기대값은 유지했습니다. 실패 회귀 묶음은 133 passed·1 skipped.
- 실제 패키지 설치·labhq 요청·직원 CLI 호출은 하지 않았습니다.

## 미해결

기존 직접 명령 가드의 한계는 유지됩니다. 간접 script, 상속된 installer 설정, 검사 뒤 파일시스템 변경은 검증 범위 밖입니다.

Fixes #458

🤖 Generated with Codex for the labhq dev lead
