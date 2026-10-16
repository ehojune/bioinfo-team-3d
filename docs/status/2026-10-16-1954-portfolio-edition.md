## 결론

PR #699의 마지막 수정 라운드와 CI 실패 6건을 반영했습니다. Refs #686.

## 바뀐 것

- 파일을 배치하며 원본→정리본 경로 표를 만들고 inline·image·reference-style 링크의 재작성과 검사를 모두 이 표로 처리합니다.
- 동명 SVG 둘은 각 원본 경로에 맞는 서로 다른 정리본 파일을 가리킵니다. basename 추정은 제거했습니다.
- 이전 draft의 `report.md`는 재개할 때 `report/report.md`로 옮겨 `report_url`과 실제 게시 경로를 맞춥니다.
- 내부 자료 요청은 Markdown·SVG·Python·R·shell만 기본 허용하고 JSON·TXT·HTML·표는 집계 선언과 작은 칸 검사를 요구합니다.
- README 실패 4건은 동작 회귀였습니다. 진행 기록과 재현 방법을 `run/`에 보존하고 루트 README에서 새 경로로 잇습니다.
- shutdown 실패 2건은 낡은 fixture였습니다. 새 draft 계약에 맞춰 이전 `report.md`의 자동 이관과 게시 카드 문서를 직접 단언합니다.

## 확인

- CI 실패 6건은 수정 전 그대로 재현했고 수정 뒤 통과했습니다.
- 이름에 result_storage·portfolio·late_storage·shutdown·publish·edition·pdf가 든 pytest 16개 파일은 352 passed, Node 4개 파일은 28 passed입니다.
- `scripts/check_public.sh`, patch-notes, notes-index 검사는 최종 커밋 전에 다시 확인합니다.
- `docs/manual.md`는 152,528자에서 152,589자로 61자 늘었습니다.

## 남은 것

CI 결과는 최종 push 뒤 확인합니다. 리뷰 재요청과 병합은 하지 않습니다. 이번이 마지막 수정 라운드입니다.
