## 단계
#1037 rev18: 다섯 번째 단위 — wiki 인용 행을 감사 기록과 대조. 공개 보호는 아직 **UNVERIFIED**.

## 한 일
- `wiki_contract.step_findings(ledger, workdir)`: 그 단계 workdir의 `outputs/wiki_queries.jsonl`을 읽어(최대 4 MiB, 한 줄이라도 깨지면 전체 거부) `check_wiki_ledger`의 대조(locator·신선도·출처·분류)를 한다. wiki 행이 없으면 아무것도 읽지 않는다.
- `orchestrator/cso.py`의 `save_salvage`가 단계 결과를 받을 때(`accept`와 두 salvage 경로) 이 결과를 `result_salvage`의 거부 행·지지 못 받는 claim에 합친다. CP2 카드의 거부 행(`wiki audit: …`)과 거부 근거로 나온다. 기록이 없거나 읽을 수 없으면 그 단계의 모든 wiki 행을 거부한다(fail-closed). 거부 행에서 파생된 행, 그 행에만 기대는 claim도 뺀다.
- 단계 prompt(`RESEARCH_RESULT_FIELD_RULES`)에 wiki hit 인용 규칙 한 줄을 더했다. 계획 prompt와 스키마는 그대로다.
- 시험: 합성 audit로 정상 수용, 파일 없음·깨짐·미기록 locator·신선도 위조·출처 불일치 거부, 파생·claim 연쇄, 실제 CP2 흐름(MiniHub)에서 거부 행 확인(연결을 끄면 실패하는 것 확인).
- manual 12장 한영, 참고 문서 갱신.

## 한계·하지 않은 것
- 감사 기록은 직원의 작업 폴더 안 파일이라 직원이 고쳐 쓸 수 있다. 독립 출처 인증은 다음 단위다.
- gateway가 workdir를 직접 읽을 수 없는 runner(다른 머신·WSL)면 기록을 못 읽어 wiki 행이 모두 거부된다. runner가 기록을 결과에 실어 보내려면 `models.py`·`runner/daemon.py`(claim 밖) 변경이 필요하다.
- wiki 행이 단계의 수집 산출(`outputs`)에 `outputs/wiki_queries.jsonl`이 없으면 기존 규칙(`bind_result_artifacts`)이 먼저 그 행을 거부한다. 계획이 그 파일을 산출로 선언하게 하는 일은 하지 않았다.
- wiki 본문 마스크, 결과 저장소 선택 카드 표시.
