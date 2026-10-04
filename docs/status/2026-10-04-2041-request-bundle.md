# 요청 묶음 4차 P1 구조 수정 (#373)

## 단계
P1 — 산출 폴더 순회를 없애고 runner 기록만 복사하도록 단순화했습니다.

## 한 일
- `outputs ∩ output_sha256` 파일만 no-follow handle로 열어 hash를 확인하고 복사합니다.
- 누락·변경·상한 제외를 모두 manifest에 적고 묶음과 이벤트를 `incomplete`로 표시합니다.
- workdir 치환은 경로 경계에서만 적용하고 POSIX 대소문자를 구분합니다.
- 공용 runner walker는 main 상태로 되돌렸습니다.

## 테스트 결과
- [x] 수정 전 회귀 5건 실패, 수정 후 통과
- [x] 관련 test 53 passed, 4 skipped
- [x] `pytest -q`: 3756 passed, 54 skipped
- [x] Node CJS 20개와 `scripts/check_public.sh` 통과

## 기존 테스트 변경
기존 runner test 기대값은 그대로 두고, 이 PR의 요청 묶음 test만 기록 산출 구조에 맞췄습니다.

## 막힌 점
없음.
