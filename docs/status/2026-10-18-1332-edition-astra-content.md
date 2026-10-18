## 결론

PR #740에서 정리본 저장소의 표·첫 화면·보고서 근거·갈래 안내·자료 이용 조건을 보강했습니다. Refs #736.

## 바뀐 것

- 공개 표를 루트·갈래 `tables/`에 두고, 본문이 링크한 표의 존재와 열·행 shape를 게시 전에 검사합니다.
- README 첫 화면 순서를 고정하고, 뺀 표는 이름·사유·행 수·주요 열로 설명합니다.
- 보고서 claim anchor를 근거 각주로 바꾸고 수정 이력은 `run/`으로 옮깁니다.
- 갈래 연결표와 `docs/reference/`를 만들고, 자료별 출처·접근·재배포·인용을 채웁니다.
- 늦은 저장 전환 뒤에도 README의 진행·재현·참고문헌 링크를 유지하고, 정규화 결과가 같은 claim anchor도 서로 다른 근거 위치를 가리킵니다.

## 확인

- 새 회귀 test는 수정 전 3 failed, 수정 뒤 3 passed입니다.
- CI run `39098307497`에서 찾은 README 링크 회귀와 P2 anchor 충돌 회귀를 고쳤습니다.
- late storage·edition·portfolio·result storage 관련 pytest는 214 passed입니다.
- `git diff --check`와 `scripts/check_public.sh`는 통과했습니다.
- `docs/manual.md`: 159,597자 → 159,391자(-206자).

## 남은 것

- push 뒤 GitHub CI 결과를 확인합니다. 리뷰 요청과 병합은 하지 않습니다.
