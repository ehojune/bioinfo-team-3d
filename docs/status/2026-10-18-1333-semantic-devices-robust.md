## 결론

의미 장치 중단·오류를 `제안 0건`으로 숨기지 않고 요청 카드와 기록에 표시합니다. Fixes #738.

## 점검 결과

| 장치 | 경로 | 전에는 | 지금은 |
|---|---|---|---|
| semantics 시작 상태 | CP1 전 점검 | 설정만 보고 실제 중단·epoch·마지막 기록을 놓침 | 상태·epoch·마지막 기록과 복구 명령을 CP1·doctor에 표시 |
| 자동 중단 | breaker·`auto_off` | 로컬 파일·로그에만 남음 | CP1·CP2·웹·status·doctor·실행 기록에 같은 사유·시각·영향을 기록 |
| advisory snapshot | 계획 전 복사 | 예외를 `None`으로 삼켜 제안 0건처럼 보임 | 요청별 `error`로 남기고 다음 요청에서 재시도 |
| advisory projection | 후보 계산 | 예외를 빈 목록으로 삼킴 | 요청별 `error`로 남기며 정보 경계 위반은 계속 중단 |
| 단어장·EDAM·체크리스트·pack | 계획·부록 | 일부 실패가 기능 off나 빈 부록으로 축약됨 | hash·개수 또는 오류·영향을 카드와 실행 기록에 남김 |

## 확인

- 새 회귀 5건: main 기준 5 failed, 수정 뒤 5 passed.
- 핵심 회귀 7 passed, 웹 카드 16 passed.
- 관련 pytest 821 passed.
- `docs/manual.md`: 159,597자 → 159,857자(+260자).

## 남은 것

- push 뒤 GitHub CI 결과 확인. 리뷰 요청과 병합은 하지 않습니다.
