## 결론

PR #733에서 Europe PMC 양방향 인용 추적과 bioRxiv·medRxiv preprint 검색을 추가했습니다. Fixes #720.

## 바뀐 것

- `citation_chase`가 PMID·PMCID·DOI 한 편의 참고문헌과 인용 논문을 서지·출처·`access_level_candidate`와 함께 돌려줍니다.
- `preprints`는 Europe PMC `SRC:PPR`에서 bioRxiv·medRxiv를 찾고 모든 결과에 `심사 전`을 표시합니다.
- lit_scout·biologist는 핵심 논문마다 한 번 인용 추적을 하고, 방향별 발견 수·새로 넣은 수를 검색 기록에 남깁니다.
- 조회 기록·30일 cache·요청 간격은 기존 `labhq_annot` 규칙을 따릅니다.

## 확인

- 새 회귀는 구현 전 5 failed, 구현 뒤 관련 pytest 79 passed입니다.
- `scripts/integrations.py --check`, `git diff --check` 통과.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 동안 출력 없이 멈춰 timeout으로 끝났습니다.
- `docs/manual.md`: 158,685자 → 159,186자(+501자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 총괄이 이어갑니다.
