## 결론

PR #735에서 CP2 결론 근거 논문의 읽은 수준을 실제 claim–evidence 연결 기준으로 셉니다. Fixes #697.

## 바뀐 것

- 인용 가능한 claim과 `supports`·`contradicts`로 연결된 evidence의 PMID·PMCID·DOI만 모읍니다.
- 단계별 `literature_access`와 맞춰 본문·초록·서지만·조회 기록 없음으로 세고, 본문이 아닌 논문이 있으면 CP2 카드와 보고서 `## 한계`에 한 줄을 넣습니다.
- 이 분모가 기존 `fulltext로 조회한 논문 전체` 집계와 다르다고 함께 밝힙니다.

## 확인

- 새 회귀는 구현 전 collection error, 구현 뒤 통과했습니다.
- 관련 pytest는 208 passed입니다.
- `docs/manual.md`: 159,415자 → 159,566자(+151자).
- `scripts/check_public.sh`는 Windows Git Bash에서 90초 동안 출력 없이 멈춰 중단했습니다.

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 총괄이 이어갑니다.
