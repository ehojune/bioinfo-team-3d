## 결론

semantics와 그림자 요청의 꺼짐·미평가·오류·정상 평가를 분리했습니다. Fixes #559.

## 바뀐 것

- 정상 평가의 후보 0건만 `정상인데 후보 0건`으로 기록합니다.
- latched·꺼짐은 `미평가(사유)`, 실패·시간 초과는 `오류(code)`로 남깁니다.
- CP1·CP2 카드와 `semantic_report`가 같은 상태를 표시합니다.
- 미평가·오류는 A/B 분모에서 빼고 각각 셉니다.

## 확인

- 새 회귀는 수정 전 10 failed, 수정 후 핵심 328 passed입니다.
- 관련 pytest 413 passed, CP1 subprocess 2 passed입니다.
- `py_compile`과 `git diff --check`는 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 60초 무출력이라 중단했습니다.
- `docs/manual.md`: 280,933 bytes → 280,932 bytes.

## 남은 것

- CI 확인은 push 뒤 진행합니다. 리뷰 요청과 병합은 하지 않습니다.
