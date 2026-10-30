## 단계
#1089(② 6차 B2): 근거 표를 만드는 연구 단계 prompt에 규칙 한 줄. 근거: [#910 6차 분류](https://github.com/ehojune/bioinfo-team-3d/issues/910#issuecomment-6521687319).

## 한 일
1. `labhq/orchestrator/cso.py` `RESEARCH_RESULT_FIELD_RULES`에 같은 출처는 같은 independence_group이라는 규칙 한 줄(`RESULT_CONTRACT_FIELD_RULES`가 있는 `labhq/evidence/claims.py`는 범위 밖이라 건드리지 않고 cso.py 쪽 꼬리에 넣었다).
2. `tests/test_output_types_research.py`: 규칙 문구가 단계 prompt가 싣는 상수에 있는지 단언.

## 확인
- 거부 메시지는 이미 충돌을 알려 준다: `labhq/evidence/claims.py:527` "evidence A and B cite the same source X but declare independence groups G1 and G2; re-citation is not independent"(`tests/test_research_evidence.py`가 검증). 고칠 것 없음.
- 단계 prompt 전체를 고정 hash로 못 박은 시험은 없다(고정 hash는 계획 prompt뿐이라 영향 없음). 그래서 hash 시험 대신 규칙 존재 단언을 넣었다.
- `tests/test_output_types_research.py`·`tests/test_output_types.py` 55 passed, `tests/test_research_cp2.py` prompt 관련 15 passed.

## 하지 않은 것
- 실제 ② run에서 엔진이 이 규칙을 따르는지는 다음 run 전까지 못 본다.
