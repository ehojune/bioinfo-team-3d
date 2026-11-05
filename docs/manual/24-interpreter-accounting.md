[← 매뉴얼 목차](../manual.md)

## 통역 예약 회계

통역은 dispatch 전에 task·attempt·ceiling·출처를 별도 원장에 남깁니다. 요청 bound는 연구 hold와 같은 task를 한 번만 합산하고 확정 비용으로 대체합니다. task 등록 전 중단된 예약도 보존하며 `/task-attempts`의 `translation_reservations_v1` projection에 누락 task로 표시합니다. 기존 aggregate 예약에서 task별 근거를 추정하지 않습니다.
