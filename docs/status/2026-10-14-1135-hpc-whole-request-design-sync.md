## 결론

PR #578에 최신 main을 병합해 2D 디자인 토큰·결정 카드 규칙을 맞췄다. 실행 위치 질문은 기존 `clarify` 카드와 공통 부품을 사용한다. Refs #430.

## 바뀐 것

- legacy `<style>` 뒤에 `shutdown.css`, `tokens.css`, `components.css`를 순서대로 읽는다.
- `e9f56a9`에는 토큰·부품 CSS 링크가 없어 CI가 실패했다. 별도 HPC 카드 CSS는 추가하지 않았다.
- merge commit `3090632`을 브랜치 패치노트에 기록했다.

## 확인

- `node --test tests/web_design_tokens.cjs`: 3 passed.
- Node 전체 55개 파일 통과.
- 관련 pytest: 152 passed, 2 skipped.
- Windows 전체 pytest: 6,298 passed, 67 skipped.

## 남은 지적

- push 뒤 CI 결과 확인이 남았다.
- 실제 HPC·PI/trial 실행, 고정 포트 사용, 리뷰 요청, 병합은 하지 않았다.

🤖 Generated with Codex (gpt-5.6-sol) for the labhq dev lead
