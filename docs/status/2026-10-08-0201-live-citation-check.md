# 2026-10-08 · PR #477

## 결론

네 번째 봇 리뷰의 P1 두 건을 `3626ebd`에서 고쳤다. 미등록 출처와 현재 artifact hash를 `labhq verify`가 다시 정확히 판정한다.

## 바뀐 것

- Crossref와 doi.org가 모두 404인 DOI는 PMID·PMCID 부재와 같이 `not_found` 결함이 된다.
- 감사 재검증은 단계별 output scan의 `path → sha256`을 artifact 출처 검증에 넘긴다.
- 관찰 hash가 인용 hash와 같으면 exit 0, 다르면 conflict와 exit 1이다.

## 실행한 것

- 관련 pytest: 32 passed, 2 skipped.
- Windows 전체 pytest: 4683 passed, 57 skipped.
- `scripts/check_public.sh`, 패치노트·목차 검사 통과.

## 미해결

- push 뒤 CI 전체 job 결과를 확인한다.

Closes #422

🤖 Generated with Codex (gpt-5) for the labhq dev lead
