# 평가 묶음 fixture (#1383)

설계는 `docs/reference/eval-suite.md`. 모두 합성 데이터이고 실제 조회 · 모델 호출로 만든 것이 없습니다.

- `citation_audit/`: 인용 10건, 기록된 조회 응답(`records.json`, DOI 예시 접두사 10.5555), 원문 텍스트. `expected`가 등급 정답.
- `review_calibration/clean/`: 내부가 맞는 작은 결과 묶음. `seeded/`: 오류 9종을 심은 것. `seeded_contrast/`: 대조 방향만 뒤집은 것(방향 오류와 서로 상쇄하지 않게 분리). 각 `ERRORS.json`이 정답 키이고 리뷰어에게 보여 주지 않습니다. 묶음마다 `records.json`(조회 기록)이 있습니다.
- `falsification/cases.json`: 반증 반영 사례 6건의 틀. 정답 칸은 PI가 실행 전에 채웁니다.
