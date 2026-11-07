## 단계
#1037 rev17: 네 번째 단위 — 증거 수준 검사와 결과 계약 규칙. 공개 보호는 아직 **UNVERIFIED**.

## 한 일
- W1 초안(`e2a36148`: `claims.py`, `report_check.py`, `wiki_contract.py`, `test_wiki_evidence.py`)을 되살렸다. `wiki_summary` 행은 literature_claim·tertiary·indirect이고 원본이 될 수 없다. 보고서 근거 표는 `wiki 요약 (신선도)`로 적는다.
- `ledger_errors`: 같은 주장에 `full_text` 읽기 기록이 없으면 wiki 요약만으로 `supported`·`partially_supported`를 채우지 못한다. 반대 방향 wiki 요약은 `supported`를 막는다.
- 결과 계약 규칙(`RESULT_CONTRACT_FIELD_RULES`)에 wiki hit 기록 방법을 더했다.
- 결과 스키마 hash를 의도적으로 갱신했다: `596679ad…` → `a8383b36…`. 이유는 `access_status`에 `wiki_summary`, `SourceRef.wiki_freshness`가 생겼기 때문이다. 계획 스키마, 계획 prompt, `VALID_PLAN_SHA`는 그대로다(`test_output_types_research.py` 주석에 적음).
- 시험 5건 추가(wiki 단독 지지 거부, 원문 읽기와 함께면 허용, context 허용, 반대 요약 표시, prompt 규칙 문구).
- manual 12장 한영, 참고 문서, Windows inventory(`tests/test_wiki_evidence.py`) 갱신.

## 하지 않은 것
- `check_wiki_ledger`를 단계 결과를 받는 쪽(`labhq/orchestrator/cso.py`, claim 밖)에 연결하지 않았다. 신뢰할 수 있는 `outputs/wiki_queries.jsonl`을 읽어 locator·신선도·origin을 대조하는 일이 남았다.
- 단계 prompt(`cso.py`의 문헌 증거 안내)에는 wiki 규칙을 더하지 않았다(claim 밖).
- 독립 출처 인증, wiki 본문·경로 마스크, 결과 저장소 선택 카드 표시.
