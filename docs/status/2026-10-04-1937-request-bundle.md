# 요청 묶음 2차 P1 보강 (#373)

## 단계
P1 — runner와 묶음의 순회 판정을 합치고 재실행 명령·요청별 상한을 보강했습니다.

## 한 일
- 묶음이 runner의 held-descriptor walker를 써서 link·junction·mount·device 변경·통제 구역을 제외합니다.
- 안전한 이름의 script만 DAG 위상 순서로 README 명령에 싣습니다.
- 요청별 2,048MB·5,000파일 상한을 두고 첫 제외 파일을 manifest와 부록에 기록합니다.

## 테스트 결과
- [x] 수정 전 회귀 5건 실패, 수정 후 통과
- [x] 관련 test 85 passed, 21 skipped
- [x] `pytest -q`: 3749 passed, 53 skipped
- [x] Node CJS 20개와 `scripts/check_public.sh` 통과

## 기존 테스트 변경
없음.

## 막힌 점
없음.
