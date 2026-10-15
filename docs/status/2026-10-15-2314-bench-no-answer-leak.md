# PR #650 — 벤치 정답 식별자 누출 검사

Fixes #623.

## 결론

bench case YAML의 정답 식별자가 비벤치 코드로 새면 CI가 파일과 줄, 원본 사례를 밝혀 실패합니다.

## 바뀐 것

- `result_fields.equals`의 string 식별자를 자동 수집하며 0건이면 실패
- 추적 중인 `labhq/**/*.py`, `agents/`, `config/`만 검사하고 bench 코드·데이터·test 제외
- 임시 복사본 누출 탐지와 일반 예시 문구 비탐지를 회귀로 고정

## 확인

- 관련 pytest 8 passed
- `scripts/check_public.sh` 통과

## P1 후속

- 식별자 경계를 ASCII 영숫자로 판정해 `GKN1을`, `(COL1A1과)`, `6OIM은구조`도 누출로 잡습니다.
- 영문 단어 안의 부분 일치는 계속 제외합니다.

## 남은 것

리뷰 요청과 병합은 총괄이 이어받습니다.
