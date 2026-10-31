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

## rev9: A안 — 보류액을 남은 요청 예산으로 자름
- 원인: 보류액이 `min(task, agent, per_task_usd)`라 요청 예산이 per_task(기본 $5)보다 작으면 첫 호출부터 예산 카드가 떴다(진단: [#1139 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1139#issuecomment-6544463979), 결정: A안).
- 구현(`orchestrator/cso.py`): `_clip_to_remaining`. 요청에 1센트 이상 남았으면 보류액 = min(호출 상한, 남은 예산), 그 미만이면 자르지 않고 카드. 카드가 올릴 상한은 호출 상한 전체(`needed = full`)를 담도록 그대로 둔다.
- 시험: `test_reserve_default_on.py` 13건(작은 예산 첫 호출 카드 없음, 병렬 둘이 합쳐 상한을 못 넘고 셋째는 거절, 1센트 미만은 묻는다, 카드는 호출 상한 전체를 요구). 클리핑을 끄면 5건 실패함을 확인.
- 고친 기존 시험(분류): (a) 예산이 주제가 아닌 것: `test_ask_review3/4/5`(13, 요청 레코드를 만든다), `test_plan_only_cso_model`·`test_branched_research`·`test_contract_slot_repair`의 병렬 보류액 예산, `test_bench`·`test_cp1_input_fit_796`·`test_request_lifecycle`·`test_resume_accounting`은 A안만으로 통과. (b) 사용자에게 보이는 동작이 바뀌는 것(PR 본문에 목록): 아래.
- (b) 목록: `test_research_notes_budget`(`..._would_pass_the_request_budget...` 카드 대신 남은 예산으로 보류, 정확히 상한을 쓴 요청은 다음 호출 전에 묻는다, 질문 대기 보고 turn은 올린 상한 안에서 끝나 final-turn 기록이 없다), `test_cso.py`(동시 turn은 합쳐 보류, 마지막 turn은 남은 만큼 보류, 선택 turn은 남은 만큼 보류하고 0이면 거절, 취소 turn의 보류), `test_facilities_diagnosis`(`budget`·`unknown_budget`은 옛 검사로 고정하고 `reserved_budget` 추가).
- 확인하지 못한 것: 전체 suite(총괄 지시로 CI에 맡긴다), `test_semantics_shadow_remove`(서브프로세스로 전체 suite를 돌려 로컬에서 90초 이상 걸림).
