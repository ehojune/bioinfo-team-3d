## 결론

PR #692에서 연구 보고서 그림을 SVG 원본 품질로 보존합니다. Fixes #683, Fixes #690.

## 바뀐 것

- 분석 단계는 핵심 그림 1~2개를 SVG로 선언하고, 보고서는 결과 절에 넣거나 이유를 한 줄로 적습니다.
- QC는 그림 누락, SVG 없는 PNG·JPEG, 한글 `<text>` 없는 SVG를 WARN으로 남깁니다.
- PDF 재인코딩·축소·`max-height`와 정리본 그림 크기 상한을 없앴습니다.
- 그림 파일을 만드는 화면·브라우저 캡처 경로는 코드에서 발견되지 않았습니다.

## 확인

- 새 회귀: main 8 failed → branch 8 passed.
- 관련 pytest: 600 passed, 2 skipped. `scripts/check_public.sh` 통과.
- `docs/manual.md`: 149,918자 → 151,448자(+1,530자).

## 남은 것

- CI 결과는 push 뒤 PR에 기록합니다. 리뷰 요청과 병합은 하지 않습니다.
