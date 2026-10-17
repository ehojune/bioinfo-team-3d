## 결론

PR #727에서 주 산출이 있는 단계는 선택 산출이 빠져도 진행하고 경고를 남기게 했습니다. 필수 산출 누락은 기존처럼 incomplete입니다. Fixes #716.

## 바뀐 것

- 기존 `outputs`는 필수로 유지하고 로그·변환 기록·미리보기용 `optional_outputs`를 추가했습니다.
- 64자리 0 `plan_sha256` placeholder는 실행 중인 승인 계획 hash로 보정하고 경고합니다. 다른 불일치는 계속 거부합니다.
- 글자 치환·형식 변환은 직원이 자기 작업 폴더에서 Python 스크립트 하나로 처리하도록 계획 규칙을 고쳤습니다.

## 확인

- 새 회귀는 구현 전 실패하고 구현 뒤 7건 통과했습니다.
- 관련 pytest: 508 passed, 1 skipped.
- `scripts/check_public.sh` 통과.
- `docs/manual.md`: 157,625자 → 157,816자(+191).

## 남은 것

- push 뒤 CI 결과 확인.
