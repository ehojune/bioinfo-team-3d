## 결론

PR #735에서 CP2 결론 근거 논문의 읽은 수준을 논문 식별자 별칭까지 맞춰 셉니다. Fixes #697.

## 바뀐 것

- evidence의 `uri`·`locator`와 구조화된 `id_scheme/id_value`에서 PMID·PMCID·DOI를 읽습니다.
- 한 fulltext 응답의 세 식별자를 같은 논문의 별칭으로 보존하고, 하나라도 겹치면 접근 수준을 맞춥니다.
- 본문·초록·서지만·조회 기록 없음으로 세어 CP2 카드와 보고서 `## 한계`에 넣습니다.
- 이 분모가 기존 `fulltext로 조회한 논문 전체` 집계와 다르다고 함께 밝힙니다.

## 확인

- 별칭 회귀 2건은 head `89a7fbb4`에서 2 failed, 수정 뒤 통과했습니다.
- 관련 pytest는 227 passed입니다.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 159,566자 → 159,597자(+31자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 재요청과 병합은 하지 않습니다.
