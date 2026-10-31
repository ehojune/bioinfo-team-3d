## 단계
#676 B4: CSO 계획이 직원에게 labhq 소유 파일 `outputs/external_lookup_status.jsonl`에 쓰라고 지시해, 직원이 쓴 줄 8개가 CP2 경고 "읽지 않은 잘못된 기록 8줄"로 나온 문제.

## 한 일
- `labhq/output_checks.py`: `RESERVED_OUTPUTS`에 labhq가 직접 쓰고 읽는 기록 이름 세 개(`external_lookup_status.jsonl`, `annotation_queries.jsonl`, `literature_access.jsonl`)를 한 곳에 모았다.
- `labhq/orchestrator/cso.py`: `_reserved_names`가 계획의 단계가 이 이름을 `outputs`·`optional_outputs`로 선언하거나 instruction에서 단계 자신의 파일로 쓰게 하면 `PlanOutputsError`로 거절한다. 앞선 단계의 `inputs/<단계>/outputs/<이름>`은 그 단계 기록을 읽는 것이라 허용한다.
- `output_checks.external_lookups`: `tool` 키가 없는 JSON 객체 줄은 직원이 쓴 줄로 보고 읽지도 세지도 않는다. 객체가 아닌 줄, JSON이 아닌 줄, `tool`을 쓰는데 labhq 규칙에 어긋난 줄은 이전처럼 잘못된 기록으로 센다(`test_external_lookup_record_676.py`는 그대로 통과).
- 시험 `tests/test_reserved_outputs.py`(Windows inventory `known_tests`에 추가): 세 이름 × 네 가지 표기 × 두 필드 거절, instruction의 쓰기 지시 거절, 읽기·일반 출력 허용, 직원이 쓴 줄 8개가 경고에 안 섞임, labhq 형식이 어긋난 줄은 계속 셈.

## 확인
- `test_reserved_outputs`·`test_output_types*`·`test_external_lookup_record_676`·`test_output_checks` 198 passed, 1 skipped. cso·plan·output·lookup 계열 918 passed(프롬프트 해시 시험 둘은 아래 이유로 프롬프트를 바꾸지 않았다).
- `validate_profile` 통과(known_tests 한 줄).

## 하지 않은 것
- 계획 프롬프트에 "이 이름을 쓰지 마라"는 문구를 넣지 않았다. `test_off_keeps_the_plan_schema_and_prompt_of_main`과 `test_engine_schemas_and_prompt_off_are_those_of_main`이 프롬프트 해시를 고정하고, 둘째는 이 claim 밖이다. 검사가 거절하면 계획 재시도 경로로 간다. 프롬프트에 넣는 것은 해시 시험을 같이 고쳐야 하는 별도 일이다.
- CSO가 이 이름을 어디서 배웠는지(상류 단계 지시를 베낀 것으로 보임)는 확인하지 않았다.
