# 2026-10-08 · PR #477

## 결론

봇 리뷰 P1 4건과 P2 1건을 `804b796`에서 고쳤다. 새 live 결함은 CP2 승인과 감사 성공을 그대로 통과하지 않는다.

## 바뀐 것

- DOI·PMID 재인용은 중복 근거와 claim을 CP2에서 거부하고 `labhq verify` 문제로 센다.
- 미지원 ID·URI와 미완료 조회는 감사 exit 0이 아닌 `검사 미완료`로 보인다.
- 재개 시 live 요약이 바뀌면 CP2를 다시 열고, 같으면 기존 결정을 유지한다.
- Crossref 철회 판정은 NCBI 보강 실패 뒤에도 보존한다. 불완전한 PubMed 200 응답은 `invalid_response`다.

## 실행한 것

- 지적 회귀 테스트: 수정 전 6 failed, 수정 후 6 passed. 관련 pytest: 155 passed.
- Windows 전체 pytest: 4677 passed, 57 skipped.
- `scripts/check_public.sh`, 패치노트·목차 검사 통과.

## 미해결

- push 뒤 CI 전체 결과를 확인한다.

Closes #422

🤖 Generated with Codex (gpt-5) for the labhq dev lead
