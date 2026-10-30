# #1066 manual 후속

- 결론: #1067 리뷰가 지적한 manual 한·영 설명을 더했다. 코드는 바꾸지 않았다.
- 바꾼 것: `docs/manual.md`·`docs/manual.en.md`의 연구 단계 결과 교정 문단에 (1) 결과와 교정 요청은 계획이 선언한 단계 `claim_ids` 안의 주장만 허용, (2) 목록 밖 claim id를 만들지 않고 hypothesis row는 claim이 아니라 context로 연결하거나 링크에서 뺌, (3) 가설·기전·등급별 정리를 결과로 요구하면 계획이 그 몫의 claim을 `claim_ids`에 미리 선언.
- 실행한 것: `tests/test_integrations.py` 통과.
- 미검증: 실제 모델이 이 규칙을 따르는지는 ② 6차 이후 결과로 확인한다.
