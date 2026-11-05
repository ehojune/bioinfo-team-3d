## 단계
#1282(#1281 후속): live source 확인이 `host_paused`를 조회 상한 소진(`lookup_limit`)으로 기록하던 문제.

## 한 일
- `labhq/evidence/verify.py`: 실패 종류 `host_paused`를 더했다. `LiveSourceResolver._admit()`이 `lookup_admission.admit`의 `reason: host_paused`를 따로 받아 호스트와 `retry_after_s`를 `LookupFailed`에 남기고, 상세에 "조회 몫은 남아 있고 약 N초 뒤 다시 보낼 수 있다"를 적는다. 소진은 그대로 `lookup_limit`이다.
- 시험 2건(`tests/test_lookup_host_breaker_1279.py`): 쉬는 호스트는 `host_paused`(보낸 것·센 것 없음), 소진은 `lookup_limit`. 수정 전 코드에서 앞의 시험이 실패한다.

## 확인
- Windows: `test_lookup_host_breaker_1279.py`·`test_live_source_limit_891.py`·`test_evidence_verify.py` 122 passed.

## manual과 CP1 계획 경고 (rev2)
- `labhq/orchestrator/cso.py`: `with_paused_host_warning`과 `_paused_lookup_hosts`를 더했다. CP1 계획이 확정될 때(일반 계획과 연구 계획 모두, 연구 계획은 검증·동결 전) 조회 카운터에서 쉬는 호스트를 읽어 계획 경고에 호스트·남은 초·"보내지 않은 시도는 조회 몫을 쓰지 않는다"를 싣는다. 카운터가 없거나 읽지 못하면 경고만 빠지고 계획은 막히지 않는다.
- manual 09장 한·영: `host_paused`와 `lookup_limit`의 차이, 재시도 시점, CP1 계획 경고 설명을 적었다(옛 "CP1 경고에 오르지 않는다" 문장은 고쳤다).
- 시험(`tests/test_lookup_host_breaker_1279.py`): 계획 경고 helper, 요청별 읽기, 연구 요청 CP1 흐름. CP1·live source 시험 포함 75 passed 이상.

## 하지 않은 것
- 쉬는 호스트의 재시도 시점을 CP1 카드에서 갱신하지는 않는다(계획 확정 시점의 값이다).
