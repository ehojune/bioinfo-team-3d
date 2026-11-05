## 단계
StructuredOutput 재시도의 요청 예산 초과 — Closes #673 (이어받음: Codex W3 → claude-worker1, PR #996).

## 원인
- `runner/structured_output.py`의 `run_with_json_retries`가 한 dispatch 안에서 형식 오류를 최대 2번 스스로 다시 호출했습니다. 병렬 단계들은 같은 `request_budget_remaining_usd` snapshot을 받으므로, 잔여 $0.20에서 두 단계가 각각 $0.10을 쓰고 각자 재시도하면 $0.40이 나갔습니다(W3 회귀: 4번 호출, 합계 0.4 > 0.2).

## 한 일
- runner: 오케스트레이터가 보낸 dispatch(`meta.format_retry="gateway"`)에서는 한 번만 실행하고 `error_kind=structured_output_format`과 `TaskResult.format_retry`("plain"|"compact", 세션이 있을 때만)를 돌려줍니다. 마커가 없는 task(직접 runner 사용)는 기존 runner 내부 재시도를 그대로 둡니다. W3의 `admit_retry` callback(daemon·gateway에 연결되지 않은 초안)은 이 구조로 대체해 제거했습니다.
- 오케스트레이터(`dispatch_turn`): 그 결과를 받으면 같은 세션을 새 dispatch(새 task id, `parent_task`, 같은 workdir)로 다시 보냅니다. 따라서 매 repair가 `_check_budget`과 `_reserve_dispatch`를 포함한 기존 예산 확인을 지납니다.
- 승인 규칙: 요청의 남은 예산이 없거나, 같은 요청에서 아직 실행 중인 다른 dispatch의 최대 지출 몫(`inflight_caps`, 예약이 이미 잡은 것은 제외)을 빼면 남지 않으면 repair를 보내지 않고 형식 오류로 멈춥니다. repair 때문에 PI에게 예산을 더 묻지 않습니다. 한 턴의 상한(task·agent·per_task cap)에서 이미 쓴 몫을 뺀 만큼만 repair의 `budget_usd`로 줍니다. 비용·usage는 이전과 같이 한 결과에 합산해 돌려줍니다(각 dispatch는 따로 집계됨).
- `TaskResult.format_retry` 필드 추가, 매뉴얼 해당 절, Windows 경계 목록에 `test_673_retry_budget.py` 등록.

## 테스트 결과
- 병렬 예산 회귀 `test_parallel_format_retries_cannot_exceed_request_budget`: 수정 전 합계 0.4 > 0.2로 실패, 수정 후 통과.
- 새 시험(`test_673_retry_budget.py`): 마커 dispatch의 단일 시도·힌트(plain/compact/세션 없음), 마커 없는 task의 기존 동작, repair가 같은 세션의 새 dispatch이며 task cap에서 쓴 몫을 뺀 예산을 받음, repair가 예산 카드를 만들지 않음.
- 기존 `test_structured_output_retry.py` 21건 중 20건은 그대로 통과합니다. 1건(`test_retry_limit_yields_only_format_error_card`)은 "dispatch가 1번"이라는 단언(`len(hub.calls) == 1`)이 곧 이 이슈가 없애려는 runner 내부 반복을 못 박고 있어 `len(hub.calls) == 3`(repair마다 dispatch)으로 고쳤습니다. 나머지 단언(adapter 3회 호출, 형식 오류 카드 1장, 오류 원문)은 그대로입니다.
- 로컬: structured·cso·budget·retry·step·request·runner·hub·gateway·models·cost 키워드 2621건 통과. 전체 suite는 CI에 맡깁니다.
- 미검증: 실제 gateway·실모델에서의 병렬 실행. 예약(`reserve_before_dispatch`)을 켠 경우의 동작은 기존 예약 시험이 덮는 범위 밖입니다(repair는 예약 경로를 그대로 지나며, 맞지 않으면 기존 예산 카드가 뜰 수 있음).

## 한계·범위
- 이 흐름은 단계 turn(`run_step`)만 대상입니다. 계획·상담·CSO 등 다른 dispatch는 마커가 없어 이전처럼 runner 안에서 재시도합니다.
- 병렬 단계가 모두 실행 중인 동안 repair는 보수적으로 거절될 수 있습니다(몫을 빼면 남지 않을 때). 그 경우 단계는 형식 오류 카드로 멈춥니다.

## 바꾼 파일
labhq/runner/structured_output.py, labhq/models.py, labhq/orchestrator/cso.py, docs/manual.md, tests/test_673_retry_budget.py, tests/test_structured_output_retry.py, config/ci/windows-boundary-v1.json와 변경 기록.

## 막힌 점
없음.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
