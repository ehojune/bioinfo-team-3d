## 단계
#674 후속 3·4(orchestrator·gateway 쪽) — Refs #674 (닫지 않음).

## 한 일
- **3. `max_steps`에 닿은 계획의 clone 확장:** 환경 단계를 runner별 clone으로 나누면 계획이 `max_steps`를 넘어 확장 뒤 검증이 거부하던 것을, scheduler가 만든 clone을 한도에서 빼도록 바꿨습니다. `scheduler_clone_extra(steps)`는 논리 환경 단계마다 `clone 수 − 1`을 셉니다. 일반 계획은 `validate_steps(…, stored_environment=True)`, 연구 계획은 `validate_research_plan(…, scheduler_steps=…)`(총 단계·가지당·합성 단계·가지 합계 한도 모두)가 그만큼 더 허용합니다. 확장 직후 검증과 저장된 계획 재개 검증 모두에 적용됩니다. 계획자 자신의 단계는 여전히 `max_steps`로 제한되고(초과하면 "maximum is 3 plus 1 runner environment steps"), binding이 없는 계획은 이전과 똑같이 판정합니다.
- **4. dispatch 직전 runtime 재대조:** `Hub._session_pin_refusal(task, target, runtime)`가 대상 runner의 현재 `runtime_env`를 받아 같은 runner의 환경 binding(`environment_binding`·`environment_bindings`)과 비교하고, 다르거나 runtime을 알리지 않으면 보내기 전에 $0으로 거부합니다. 같은 runner ID가 native↔WSL로 재설정되거나 다른 OS 호스트에서 재등록돼도 옛 interpreter 경로가 전달되지 않습니다. 첫 전송과 runner 상실 뒤 재전송 두 호출 지점이 모두 `runner_capabilities[target]["runtime_env"]`를 넘깁니다. 인수를 주지 않는 기존 호출(시험)은 runner ID만 비교합니다.
- 매뉴얼(한·영)에 두 동작을 적었습니다.

## 테스트 결과
- `tests/test_runner_env_plan_674.py` 22건 통과(새 5건: clone이 한도에서 제외되고 계획자 단계 초과는 여전히 거부, 연구 한도(`scheduler_steps`), runtime 재대조(일치·불일치·미보고·인수 없음·producer·다른 runner binding), `Hub.dispatch`가 runtime 불일치를 전송 전에 거부). 코드를 되돌리면 새 시험이 실패합니다.
- cso·orchestrator·hub·dispatch·research·plan·resume·environment·runner·gateway·session 키워드 3354건 통과, 1건 실패는 이 세션 환경 때문에 깨지는 `test_doctor...parent_session_markers`(변경과 무관).
- 미검증: 실제 gateway·runner 두 대에서의 dispatch 경로 실측, 실제 WSL.

## 한계·남은 것
- 연구 계획의 가지당·합성 한도 완화(`scheduler_steps`)는 clone이 어느 가지에 속하든 같은 값만큼 허용하는 단순한 방식입니다. 가지별로 정확히 세지는 않습니다.
- 재계획(re-plan) 병합(`kept + raw`)의 한도는 건드리지 않았습니다. 그 경로는 `validate_steps`를 `stored_environment` 없이 불러 binding을 지우므로 별도 점검이 필요합니다(관찰만, 이 PR 범위 밖).
- 계획 수정 후속: manifest 내용·binding 대조(W3), 실제 엔진 쓰기 보호·OS identity(#666).

## 바꾼 파일
labhq/orchestrator/cso.py, labhq/research/contract.py, labhq/gateway/server.py, docs/manual.md, docs/manual.en.md, tests/test_runner_env_plan_674.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
