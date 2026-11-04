## 결론

PR #759에서 PI 화면에 남은 Markdown 강조·내부 기호를 위치와 수정 안내로 알립니다. Fixes #757.

## 바뀐 것

- CommonMark 파서 하나로 코드 영역을 나눠 asset·근거 수집과 claim 번호 치환에서 건너뜁니다.
- README·report.md·RESULT.md 산출물과 웹 보고를 렌더 검사합니다. 산출 검사 결과는 종료 계약의 FAIL로 전달하고 GitHub 정리본은 게시 전에 차단합니다.
- 직원 공통 강조 지시와 결과 절 하위 audio 허용을 반영했습니다.

## 확인

- main 원본 모듈: 새 회귀 19 failed / 1 passed → 수정 후 통과.
- 관련 pytest: 266 passed / 2 skipped. 최종 Markdown 검사: 56 passed.
- 공개 저장소 검사와 `git diff --check` 통과.
- 매뉴얼: 160,046자 → 160,299자.

## 남은 지적

- 최종 push 뒤 CI 상태를 확인합니다. 리뷰 요청과 병합은 총괄이 이어받습니다.
