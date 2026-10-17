## 결론

PR #729에서 선택한 요청의 공개 진행본을 단계마다 별도 GitHub branch에 올리게 했습니다. Fixes #704.

## 바뀐 것

- 처음 GitHub 저장 선택에 기본 꺼짐인 단계별 진행 게시를 추가했습니다.
- `github.progress_branch`의 공개 Markdown·SVG·코드와 `progress/README.md` 상태표를 단계마다 갱신합니다.
- 내부 단계와 게시 전 검사 실패 파일은 빼고, 일시 오류는 다음 단계에서 재시도합니다. 토큰·권한 오류는 한 번 알린 뒤 끕니다.
- 최종 정리본은 기존처럼 검토·승인 뒤 최종 branch에 게시합니다.

## 확인

- 새 회귀는 구현 전 5 failed, 구현 뒤 7 passed입니다.
- 관련 pytest 395 passed, Node CJS 19 passed입니다.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 158,005자 → 158,402자(+397자).

## 남은 것

- push 뒤 CI 결과 확인. 리뷰와 병합은 총괄이 이어갑니다.
