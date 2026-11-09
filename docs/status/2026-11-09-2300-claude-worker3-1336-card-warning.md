# 2026-11-09 claude-worker3 — #1336 r7·r8: CP1·CP2 카드 첫 줄과 의미 장치 경고

## 결론
CP1·CP2 카드 요약의 첫 줄이 카드 종류 문장이다. semantics를 설정으로 끈 경우(`configured_off`)는 경고로 보이지 않고, 켜 둔 장치가 실제로 실패한 경고만 카드 종류 문장 뒤에 붙는다.

## 바뀐 것
- `labhq/orchestrator/cso.py`: 두 카드에서 의미 장치 상태 줄과 경고를 요약 뒤로 옮김. `_device_stop_warning`이 `configured_off`를 뺀 오류로만 경고를 만듦
- `tests/test_research_cp2.py`·`tests/test_research_cp2_revise.py`: 첫 줄이 카드 종류이고 off 경고가 없음을 단언, 실제 실패 경고는 남는 시험 추가. 배정의 새 시험 파일 대신 Windows inventory에 이미 있는 이 파일들에 넣음

## 실행한 것
- 카드·의미 장치 관련 시험 12개 파일: 348 passed. 병렬 실행에서 1건(`test_semantics_shadow_breaker` 타이밍)이 실패했지만 단독 실행에서는 통과

## 미해결
- 없음. 전체 suite는 CI
