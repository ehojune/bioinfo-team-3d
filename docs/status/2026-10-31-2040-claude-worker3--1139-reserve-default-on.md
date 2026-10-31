## 단계
#1139(PI 승인 5번): `policy.budget.reserve_before_dispatch` 기본값을 `true`로.

## 한 일
- `labhq/settings.py`: 기본값 `True`(주석도 "기본 켬"으로). 켜져 있으면 `per_task_usd`·`per_request_usd`가 유한한 0 이상이어야 한다는 검증이 기본으로 걸린다.
- 예시 설정 두 사본(`config/labhq.example.yaml`, `labhq/config/labhq.example.yaml`): `reserve_before_dispatch: true`와 설명.
- manual 한·영: 비용 표와 `보내기 전 예약` 단락을 기본 켬에 맞췄다(끄는 법과 병렬 단계 설명 포함).
- `tests/test_reserve_default_on.py` 7건(Windows inventory `known_tests`에 추가): 기본값이 켜짐, 두 예시 설정이 기본값과 같음, `false`로 끌 수 있음, 비유한 상한 거절, **병렬 두 단계(호출 상한 1.5, 요청 상한 2.0)에서 기본으로 두 번째 단계가 `BudgetExceeded`**, 꺼 두면 두 단계가 모두 시작함.
- 기존 시험: `tests/test_cso.py`의 시험 11건이 옛 검사를 기대해 깨졌다. `FakeHub`가 옛 검사(`reserve_before_dispatch=False`)를 명시해 그 시험들이 원래 보던 것을 유지하고, 기본값을 확인하던 시험은 "꺼 두면 task가 그대로"로 바꿨다. 예약은 `_reserving_hub`와 새 시험 파일이 따로 본다. `test_budget_headroom_940.py`는 이미 플래그를 명시해 바꾸지 않았다.

## 확인
- `test_reserve_default_on.py`·`test_cso.py`·`test_budget_headroom_940.py` 통과(239 passed 이상). `validate_profile` 통과.
- 처음 전체 suite가 96%에서 멈춘 원인은 새 시험의 "기본값" 시험이 `FakeHub`의 옛 설정을 받아 두 번째 단계가 풀리지 않고 기다린 것이었다. `FakeHub`가 아니라 제품 기본값(`BudgetSettings()`)을 쓰도록 고쳤다.

## 하지 않은 것
- 전체 suite를 로컬에서 끝까지 돌리지 못했다(`-n 4`가 시간 제한에 걸림). 다른 시험이 더 깨지는지는 CI 결과로 확인한다.
