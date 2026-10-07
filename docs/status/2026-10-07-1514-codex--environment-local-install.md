# 2026-10-07 · PR #460

## 결론

환경 단계도 자기 작업 폴더 안 환경에만 설치합니다. 공유 환경을 쓰는 모든 shell 호출은 승인 게이트를 거칩니다.

## 바뀐 것

- `.venv` interpreter, 로컬 `--target`, conda `-p`, uv `--python`은 허용합니다.
- bare 설치, 폴더 밖 경로·링크, 충돌 옵션, 설치 환경변수 우회를 거절합니다.
- shell wrapper·분기문·실행 치환을 판별하고, Linux에서도 Windows drive·UNC 경로를 거절합니다.
- 환경 계획은 Windows·POSIX의 `.venv` Python 경로를 지정합니다. 뒤 단계의 `./.pylib`·`./.rlib` 제한은 유지합니다.

## 실행한 것

- Windows 전체 pytest: 4424 passed·56 skipped. 단일 basetemp는 종료 뒤 삭제했습니다.
- WSL 관련 검사: 399 passed·2 skipped·9 failed. 실패는 DrvFS의 기존 POSIX 권한 검사이며 CI에서 다시 확인합니다.
- `scripts/check_public.sh` 통과. patch notes·status 검사도 통과했습니다. 프롬프트 SHA 두 값을 갱신했고 정확한 비교와 schema 기대값은 유지했습니다.
- 실제 설치·labhq 요청·직원 CLI 호출은 하지 않았습니다.

## 남은 지적

인용된 중첩 shell 본문은 후속 #461로 넘깁니다. 간접 script·상속된 installer 설정·검사 뒤 파일시스템 변경은 기존 직접 명령 가드의 범위 밖입니다.

Fixes #458

🤖 Generated with Codex for the labhq dev lead
