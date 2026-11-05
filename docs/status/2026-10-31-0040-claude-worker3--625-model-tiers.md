## 단계
#625(Freda·Moore 조사) 1단계: 단계별 모델 등급. 설계: [#625 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/625#issuecomment-6524050761), 승인: 명시한 model 우선·기본 표 빈 값.

## 한 일
1. `settings.model_tiers`(fast·balanced·reasoning × 엔진 → 모델, 기본 빈 값)와 `models_match`(별칭·정식명 비교).
2. 러너 `_resolve_agent`: 직원에 `model`이 없고 표에 그 엔진 항목이 있을 때만 등급으로 모델을 채운다. 채우는 때는 `agent_overrides`·`agent_identity`·`runner.force_engine`을 모두 적용한 **뒤**라서 직원이 실제로 도는 엔진의 모델을 쓴다. 명시한 model·override·identity가 이긴다.
3. `send --dry-run`: 행마다 `(등급·모델)`, 모르면 `모델 미정`. 러너와 같은 엔진(`runner.force_engine`이 있으면 그 엔진)으로 모델을 찾는다.
4. 비용 항목이 선언 등급(`model_tier`)을 남기고, 표가 있을 때 실제 모델이 다르면 비용 요약 `warnings`에 `model_tier_mismatch:<직원>:<등급>:<실제 모델>`을 남기며 비용 줄(`format_warnings`)에도 보인다. 실제로 돌았는지 관측하지 못한 결과(`outcome_unknown`)는 뺀다.
5. gateway의 동기·비동기 두 비용 기록 경로가 `model_tiers`를 `request_cost_summary`에 넘긴다(`gateway/server.py`).
6. `setup-wsl`이 `model_tiers`를 WSL runner 설정에 보내고 `wsl_bootstrap`이 설정에 쓴다(`wsl_setup.py`, `wsl_bootstrap.py`).
7. manual 한·영에 우선순위(직원 `model`·`agent_overrides`·`cso_model`이 등급보다 앞섬), 빈 표의 의미, `model_tier_mismatch`와 비용 줄, dry-run의 `모델 미정`을 적었다.
8. 예시 YAML 두 사본에 빈 `model_tiers`와 설명.

## Codex 어댑터 읽은 결과
- Codex: `-m <model>`(model이 있을 때만), effort는 `-c model_reasoning_effort=...`. Claude Code: `--model`, `--effort`. 등급은 effort를 건드리지 않는다.
- 실제 모델은 CLI가 알린 값(`record_model_id`, 정식명)이고 직원 YAML은 별칭(opus)일 수 있어 비교는 별칭·정식명 규칙(`models_match`)으로 한다.

## 하지 않은 것
- CSO가 단계마다 등급을 고르는 것(계획 schema·CP1·plan hash가 바뀌므로 별도 이슈).
- 실제 ② run에서 선언 등급과 다른 모델 경고가 어떻게 나오는지, 비용 줄이 웹·CLI에서 어떻게 보이는지는 보지 못했다.

## 테스트
`tests/test_send_dry_run.py`(등급 표 검증, 별칭 비교, dry-run, 러너 채움과 엔진 순서, 비용 항목), `tests/test_cost_accounting.py`(gateway 동기·비동기 연결, 비용 줄, outcome_unknown), `tests/test_wsl_readiness.py`(WSL 설정에 표 전달)에 시험을 더했다. 수정을 빼면 실패함을 각각 확인했다. 관련 시험 64 passed(`test_send_dry_run`·`test_plan_only_cso_model`·`test_wsl_readiness`), `-k "bench or settings or example or config or init"` 988 passed(이전 head).
