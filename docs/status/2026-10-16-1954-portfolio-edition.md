## 결론

PR #699의 5차 봇 지적 다섯 건을 고쳤습니다. 이번이 마지막 수정 라운드입니다. Refs #686.

## 바뀐 것

- 새 포트폴리오에서 게시하지 않는 PNG·JPG·WebP 링크를 README와 과학 보고서의 생략 표시로 바꿉니다.
- PDF는 review 뒤 고른 canonical 과학 원고를 쓰며 그림 링크는 요청 묶음의 `steps/...` 경로로 되돌립니다.
- 동명 Nextflow 산출물은 단계별 경로에 따로 보존합니다.
- 이전 draft 이관 때 README의 `report.md` 링크를 `report/report.md`로 고칩니다.
- 새 포트폴리오의 긴 보고서 안내도 `report/report.md`를 가리킵니다.

## 확인

- 다섯 회귀는 직전 head에서 5 failed, 수정 뒤 5 passed였습니다.
- 지정된 pytest 18개 파일은 356 passed, Node 4개 파일은 28 passed입니다.
- `scripts/check_public.sh` 통과. patch-notes·notes-index는 기록 커밋 전에 다시 검사합니다.

## 남은 것

최종 push 뒤 CI 결과만 확인합니다. 리뷰 요청과 병합은 하지 않습니다.
