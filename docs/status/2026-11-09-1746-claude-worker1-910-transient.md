# 2026-11-09 claude-worker1 — #910 r7: 5xx·429는 조회 상한에서 빼기

## 결론
HTTP 5xx·429로 답한 송신은 응답이 기록되면 조회 몫에서 되돌리고, 일시 오류로 따로 셉니다. 일시 오류는 요청마다 조회 상한과 같은 횟수에서 멈춥니다. 8차의 503×15·429×4 뒤에도 조회 20회가 남는 것을 mock으로 확인했습니다.

## 바뀐 것
- `labhq/runner/lookup_admission.py`: used·선행 몫 계산에서 5xx·429 제외, `transient_limit`과 `transient exhausted`, seq는 MAX(seq)+1, 사용량 문단
- `labhq/tools/annot_mcp.py`·`labhq/evidence/verify.py`: 일시 오류 상한 거절 문구
- manual 09(한·영)
- 배정 범위 밖: `tests/test_lookup_host_breaker_1279.py`·`tests/test_live_source_limit_891.py`의 계수 단정을 새 규칙에 맞춤(시험을 지우거나 약하게 하지 않음), manual 09는 동작 변경 규칙에 따라 고침, 상태 파일 이름은 CI 규칙(HHMM)대로

## 실행한 것
- `pytest tests/test_lookup_admission_910.py tests/test_lookup_host_breaker_1279.py tests/test_live_source_limit_891.py tests/test_external_lookup_flow_676.py tests/test_evidence_verify.py`: 146 passed

## 미해결
- KRAS 문헌 연구 1회(배정 2단계)는 병합 뒤 진행
