## 결론

PR #753에서 리포팅 공통 결과 구조와 종료 검사를 통합했습니다. Fixes #747.

## 바뀐 것

- 원본·요청 묶음·GitHub 정리본·PDF·웹이 같은 여덟 항목을 같은 순서로 표시합니다.
- 공통 검사기가 누락 파일·생략 사유·내부 claim 표기·음성 결과 위치를 확인합니다.
- 연구·일반·실패 요청의 필수 항목을 구분했습니다.
- 웹 보고서를 안전한 Markdown HTML로 표시합니다.

## 확인

- 관련 pytest: 426 passed, 2 skipped.
- 웹 보고서 Node test: 3 passed.
- 새 검사는 main에서 실패하고 이 브랜치에서 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 무출력이라 중단했습니다.
- `docs/manual.md`: 160,345자 → 160,840자.

## 남은 것

- CI 확인 뒤 봇 리뷰와 병합은 개발 총괄이 이어받습니다.
