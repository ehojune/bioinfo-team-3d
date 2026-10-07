# 2026-10-08 · PR #477

## 결론

세 번째이자 마지막 봇 리뷰의 P1·P2를 `ac20d6a`에서 고쳤다. 주 조회 결과와 CP2 당시 판정은 선택 보강·현재 재검증 실패 뒤에도 남는다.

## 바뀐 것

- DOI는 Crossref, PMID는 PubMed ESummary, PMCID는 PMC ESummary를 먼저 조회한다.
- alias·ID 변환·PubMed 보강 실패는 경고로 남기고, 확인한 존재·철회·정정 판정은 유지한다.
- 감사 묶음은 CP2의 `source_verification`·구조화 report와 현재 재검증 결과를 따로 보존한다.

## 실행한 것

- 관련 pytest: 174 passed, 2 skipped.
- Windows 전체 pytest: 4680 passed, 57 skipped.
- `scripts/check_public.sh`, 패치노트·목차 검사 통과.

## 미해결

- push 뒤 CI 전체 job 결과를 확인한다.

Closes #422

🤖 Generated with Codex (gpt-5) for the labhq dev lead
