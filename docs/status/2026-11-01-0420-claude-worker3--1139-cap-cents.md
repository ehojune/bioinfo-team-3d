## 단계
#1139 rev12: [PR #1210](https://github.com/ehojune/bioinfo-team-3d/pull/1210)의 남은 지적. 센트로 내리는 것이 잘라낸 보류액에만 적용되어, 호출 상한이 센트 단위가 아니면(`per_task_usd = 0.015`) 같은 틈이 남았다.

## 바뀐 것
- `labhq/orchestrator/cso.py`: `_floor_cents`(Decimal 내림, 1e-9 오차 허용, 1e12 이상의 큰 금액은 그대로)를 만들고 `_clip_to_remaining`이 이를 쓴다. `_reserve_dispatch`가 `_turn_cap`의 결과(task·직원·`per_task_usd` 중 최솟값)를 어디서 왔든 센트로 내린다. 내린 값이 1센트 미만이면 `BudgetExceeded`로 보내지 않는다.
- `labhq/settings.py`: 예약이 켜져 있으면 `0 < per_task_usd < 0.01`을 로드 때 거절한다(0은 상한 없음, 끈 설정은 옛 값 유지).
- 매우 큰 금액(1e12 이상)은 내리지 않는다. #1210 리뷰 P2(1e26이 Decimal 28자리를 넘겨 `InvalidOperation`)도 이것으로 닫힌다.

## 시험
- `tests/test_reserve_default_on.py`에 12건 추가(총 45): `per_task` 0.015→0.01·1.239→1.23·2.675→2.67과 센트 단위 값은 그대로, task·직원 상한도 내림, 1센트 미만 상한 거절(아무것도 안 보냄), 설정 로드 거절, 1e26 오버플로 없음. 먼저 추가해 수정 전 8건이 실패함을 확인했다.
- 관련 `test_cso.py`·`test_budget_headroom_940.py`·연구·진단·예산 시험 등 680 passed.

## 하지 않은 것
- manual(범위 밖)의 센트 설명은 잘라낸 보류액만 다룬다. 모든 호출 상한이 센트로 내려간다는 문장은 manual 차례에 더한다.
- 실제 Claude CLI 호출 확인.
