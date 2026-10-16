## 결론

PR #699의 마지막 수정 라운드에서 4차 봇 지적 세 건을 반영했습니다. 코드 commit `efddcee`. Refs #686.

## 바뀐 것

- 파일을 배치하며 원본→정리본 경로 표를 만들고 inline·image·reference-style 링크의 재작성과 검사를 모두 이 표로 처리합니다.
- 동명 SVG 둘은 각 원본 경로에 맞는 서로 다른 정리본 파일을 가리킵니다. basename 추정은 제거했습니다.
- 이전 draft의 `report.md`는 재개할 때 `report/report.md`로 옮겨 `report_url`과 실제 게시 경로를 맞춥니다.
- 내부 자료 요청은 Markdown·SVG·Python·R·shell만 기본 허용하고 JSON·TXT·HTML·표는 집계 선언과 작은 칸 검사를 요구합니다.

## 확인

- 새 회귀는 직전 head에서 4 failed/7 passed, 수정 뒤 관련 pytest 45 passed입니다.
- `scripts/check_public.sh`가 통과했습니다.
- `docs/manual.md`는 152,528자에서 152,589자로 61자 늘었습니다.

## 남은 것

CI 결과는 최종 push 뒤 확인합니다. 리뷰 재요청과 병합은 하지 않습니다. 이번이 마지막 수정 라운드입니다.
