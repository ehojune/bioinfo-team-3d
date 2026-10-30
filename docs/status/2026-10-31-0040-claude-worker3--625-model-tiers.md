## 단계
#625(Freda·Moore 조사) 1단계: 단계별 모델 등급. 설계: [#625 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/625#issuecomment-6524050761), 승인: 명시한 model 우선·기본 표 빈 값.

## 한 일
1. `settings.model_tiers`(fast·balanced·reasoning × 엔진 → 모델, 기본 빈 값)와 `models_match`(별칭·정식명 비교).
2. 러너 `_resolve_agent`: 직원에 `model`이 없고 표에 그 엔진 항목이 있을 때만 등급으로 모델을 채운다. 명시한 model·task의 agent_overrides·agent_identity가 이긴다.
3. `send --dry-run`: 행마다 `(등급·모델)`, 모르면 `모델 미정`.
4. 비용 항목이 선언 등급(`model_tier`)을 남기고, 표가 있을 때 실제 모델이 다르면 비용 요약 `warnings`에 `model_tier_mismatch:<직원>:<등급>:<실제 모델>`.
5. 예시 YAML 두 사본에 빈 `model_tiers`와 설명.

## Codex 어댑터 읽은 결과
- Codex: `-m <model>`(model이 있을 때만), effort는 `-c model_reasoning_effort=...`. Claude Code: `--model`, `--effort`. 등급은 effort를 건드리지 않는다.
- 실제 모델은 CLI가 알린 값(`record_model_id`, 정식명)이고 직원 YAML은 별칭(opus)일 수 있어 비교는 별칭·정식명 규칙(`models_match`)으로 한다.

## 하지 않은 것
- gateway가 비용 요약을 만들 때 표를 넘기는 연결(`gateway/server.py` 두 호출): claim 범위 밖이라 `request_cost_summary(req, tiers)`의 선택 인자까지만 넣었다. 연결 전에는 요청의 실제 `cost_summary`에 경고가 나오지 않는다.
- manual 한·영(W2 #1093·W1 #792 rev9 뒤에 claim 예정). CSO가 단계마다 등급을 고르는 것(계획 schema 변경).

## 테스트
`tests/test_send_dry_run.py` 18 passed; `tests/test_cost_accounting.py`·`test_plan_only_cso_model.py`·`test_read_only_profile.py` 포함 70 passed; `-k "bench or settings or example or config or init"` 988 passed.
