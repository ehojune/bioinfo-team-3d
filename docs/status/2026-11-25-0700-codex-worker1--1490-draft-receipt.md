## #1490 r12 · PR 3a 마무리

Refs #1490

- `labhq/research/analysis_draft.py`에서 초안을 수집·검증하고 보고서에 연결된 표를 읽습니다. `evidence_checkpoint.py`에서 CP2 답변과 영수증을 구성합니다. #1500에서 옮긴 독립 감사는 `independent_review.py`에 있으며, CP2 승인 뒤 기존 순서대로 실행합니다.
- 회귀시험 276개와 공개 저장소 검사를 통과했습니다. CP2 기록 저장 뒤 초안을 작성하고, CP2를 거부하면 초안 작성과 감사가 함께 멈춥니다. 초안 누락과 해시 불일치를 거부하며, 재시작하면 저장된 CP2·초안·감사 기록을 다시 씁니다. 실제 연구 요청은 미검증입니다.
- main `d951867f`의 `cso.py`·`server.py`·`daemon.py`는 21,825줄로 기준 22,125줄보다 300줄 적습니다. 이번 PR에서 `cso.py`만 54줄 줄어 합계 21,771줄(기준 대비 −354줄)입니다. 제품 코드 순증 42줄, 구현·기록 2커밋입니다.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
