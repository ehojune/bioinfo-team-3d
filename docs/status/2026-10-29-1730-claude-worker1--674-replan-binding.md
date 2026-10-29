# #1026 재계획 병합과 환경 binding

- 결론: 재현되지 않음. `attempt_replan`은 계획에 `environment_binding(s)`가 하나라도 있으면 CSO 호출 전에 `"disabled"`를 돌려주므로(`labhq/orchestrator/cso.py`의 첫 가드) `validate_steps(kept + raw)` 병합이 binding 계획에 닿지 않는다.
- 바꾼 것: `tests/test_replan_binding_1026.py` 추가. binding 계획에서 단계가 실패해도 replan 호출·이력이 없고 binding이 그대로임을 확인한다.
- 실행한 것: 새 시험 통과. 가드를 임시로 끄면 시험이 실패함(재계획 호출 발생)을 확인한 뒤 되돌림.
- 미해결: binding 계획은 실패 시 재계획 없이 실패 보고로 끝난다(기존 동작). 이를 재계획 가능하게 만드는 일은 별도 설계다.
- 미검증: 실제 WSL·runner 종단 실행.
