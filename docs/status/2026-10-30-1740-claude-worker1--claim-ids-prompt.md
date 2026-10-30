# #1066 결과 교정이 단계 claim_ids 밖 claim을 만들게 유도함

- 결론: 효과 큰 순서대로 1~4를 모두 반영했다. #910 5차 s3는 교정 문구 `link it as context or record it as a claim`을 보고 계획에 없던 `c_mechanistic_hypotheses`를 새 claim으로 만들어 binding 오류가 났다(교정 두 번에 $3.65, W3 조사 #1054).
- 바꾼 것: (1) `result_correction`이 단계 `claim_ids`를 받아 "이 목록 밖 claim id를 만들지 말고 hypothesis 행은 context로 연결하거나 링크에서 뺀다"를 항상 덧붙인다(호출처는 단계의 `claim_ids`를 넘김, 인자 없이 부르면 옛 문구). (2) 연구 단계 prompt에 같은 규칙. (3) `evidence/claims.py`의 오류 문구는 "계획이 이 단계에 선언한 claim id일 때만 claim으로 기록"으로 범위를 밝힘. (4) 연구 계획 prompt에 "가설·기전·등급별 정리를 결과로 요구하면 그 몫의 claim을 `claim_ids`에 선언"을 추가.
- 시험: `tests/test_cso.py`에 2건(교정 문구에 허용 목록·context 규칙·빈 목록, 계획 prompt 규칙). 관련 시험 860여 건 로컬 통과.
- 미검증: 실제 모델이 새 문구를 따르는지(② 6차에서 확인). 단계의 `claim_ids`가 비어 있는 계획에서 교정이 어떻게 풀리는지는 시험하지 않았다.
