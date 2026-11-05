## 단계
#1282(#1281 후속): live source 확인이 `host_paused`를 조회 상한 소진(`lookup_limit`)으로 기록하던 문제.

## 한 일
- `labhq/evidence/verify.py`: 실패 종류 `host_paused`를 더했다. `LiveSourceResolver._admit()`이 `lookup_admission.admit`의 `reason: host_paused`를 따로 받아 호스트와 `retry_after_s`를 `LookupFailed`에 남기고, 상세에 "조회 몫은 남아 있고 약 N초 뒤 다시 보낼 수 있다"를 적는다. 소진은 그대로 `lookup_limit`이다.
- 시험 2건(`tests/test_lookup_host_breaker_1279.py`): 쉬는 호스트는 `host_paused`(보낸 것·센 것 없음), 소진은 `lookup_limit`. 수정 전 코드에서 앞의 시험이 실패한다.

## 확인
- Windows: `test_lookup_host_breaker_1279.py`·`test_live_source_limit_891.py`·`test_evidence_verify.py` 122 passed.

## 하지 않은 것
- CP1 계획 경고에 차단 사실을 싣는 일은 `labhq/orchestrator/cso.py` 등 claim 밖이라 하지 않았다.
- manual 09장의 `lookup_limit` 설명에 `host_paused`를 더하는 일도 claim 밖이다.
