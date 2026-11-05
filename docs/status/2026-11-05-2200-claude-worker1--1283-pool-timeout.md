# #1283 — #578 Windows pool timeout 진단과 helper 분리

- 결론: 13분 timeout은 hang도 제품 회귀도 아니고 Windows 시험 선택이 커진 탓입니다. #578이 공용 helper `tests/semantics_shadow_lab.py`를 고쳐, 그 helper를 쓰는 무거운 shadow 시험 8개 파일이 풀에 들어왔습니다. 근거와 수치는 `docs/reference/ci-1283-windows-pool-timeout.md`.
- 바뀐 것: `tests/semantics_shadow_lab.py` — 실행 위치 게이트 응답을 #578 모듈 import 대신 상수 `"1: b"`로(main에는 그 게이트가 없어 영향 없음). 진단 문서 추가.
- 실행: shadow 시험 8개 파일 main에서 208 passed 1 skipped, #578 head에 helper만 얹어 208 passed 1 skipped(로컬 Windows).
- 다음: 이 PR 병합 뒤 #578이 helper 변경을 빼고 main을 합쳐 최종 CI·리뷰를 받습니다. 시간 한계 증액·시험 제외는 하지 않습니다.
- 미확인: 분리 뒤 #578의 실제 pool 시간은 예측입니다. 이 PR 자신의 pool 시간은 CI 결과로 기록합니다.
