## 결론

이 PR은 OpenAlex·Crossref·Unpaywall 문헌 경로와 접근 실패 원장을 추가합니다. Fixes #744.

## 바뀐 것

- `labhq_annot`이 OpenAlex 후보·인용 연결, Crossref 판본·license·철회, Unpaywall 합법 OA 위치를 기록합니다.
- Europe PMC 본문이 없으면 합법 OA PDF·HTML을 `.tmp/literature/`에서만 추출하고, 실패 사유 네 가지를 남깁니다.
- runner가 후보·선정·확보·결론 근거 분모와 못 읽은 핵심 논문을 CP2 카드·보고서 한계에 기록합니다.
- lit_scout는 OA 여부로 후보를 미리 거르지 않고 PubMed 검색을 OpenAlex로 넓힙니다.

## 확인

- 새 회귀는 구현 전 collection error, 구현 뒤 49 passed입니다. 관련 pytest는 166 passed·11 skipped입니다.
- OpenAlex·Crossref 실제 응답 모양을 공개 식별자로 각 1회 확인했습니다.
- integrations·`git diff --check`와 공개 금지 문자열·크기·데이터 검사는 통과했습니다.
- `check_public.sh`는 Windows Git Bash에서 60초 무출력이라 중단했습니다.
- `docs/manual.md`: 158,985자 → 160,256자(+1,271자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 하지 않습니다.
