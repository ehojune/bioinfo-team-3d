# 폭주 차단기 enforcement 문구 (#1030 후속)

| 항목 | 내용 |
|---|---|
| 발견 | 실제 세션 E2E(#1030)에서 경고가 저절로 풀릴 때(`stage: normal`)와 해제 뒤 다시 감지된 warn의 `agent.breaker`가 `enforcement: applied`였다. `_breaker_stage`가 모든 단계의 기본값을 `applied`로 둬서 생긴 문구 문제다 |
| 수정 | 기본값을 단계별로 정한다: warn=`notice`(PI에게 알렸고 제한한 것은 없음), normal=`released`, constrain·stop은 기존처럼 `applied`·`unsupported`·`off`·`halt`·`failed`. `stage_record` 설명에 값의 뜻을 적었다 |
| 영향 | 값 `applied`가 constrain이 실제로 걸린 경우에만 나온다. 웹 reducer는 이 값을 읽지 않고(`stage`와 `agent.control`만 쓴다), gateway는 `halt`인지만 본다(`_note_runner_halt`) |
| 시험 | `tests/test_breaker_enforcement.py` +1: 경고가 풀릴 때 `("normal","released")`, 다시 감지될 때 `("warn","notice")` |
| 미확인 | 이 값을 소비하는 외부 도구가 있는지는 확인하지 못했다(저장소 안에는 없음) |
