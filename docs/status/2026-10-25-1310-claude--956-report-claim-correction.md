Closes #956

17차(`req_2fe54ec7dd`)가 연구와 보고서까지 마치고도 `the report anchors no claim while the ledgers hold citable claims` 한 번에 `report_incomplete`로 끝났습니다. 계획 검사(#948)와 달리 보고서에는 고칠 기회가 없었습니다.

- **교정:** claim 검사에 문제가 있으면 종합 단계(CSO)에 문제 원문, 인용 가능한 claim 목록(`step_id/claim_id`와 한 줄 요약), 고칠 보고서를 주고 "분석을 다시 하지 말고 결론마다 `[[claim:<step_id>/<claim_id>]]`만 달라"고 최대 2번(`REPORT_CORRECTIONS`) 되돌립니다(`report_correction`). 고친 보고서로 다시 검사합니다.
- **결과:** 통과하면 기존 성공 경로(`research_reported` → CP4)로 갑니다. 끝까지 실패하면 지금처럼 `report_incomplete`이고 첫 보고서가 아니라 마지막 교정본과 그 문제를 기록합니다. 교정 호출이 실패하거나 예산이 거부되면 각각 앞 보고서 유지·예산 거부 경로로 갑니다. 교정 횟수는 `research_contract.report_corrections`에 남깁니다(`report_check` 형식은 그대로).
- **종합 prompt:** 이미 인용 가능한 claim 목록을 처음부터 담고 있어 바꾸지 않았습니다(prompt 고정 hash 유지).
- **테스트(`tests/test_research_report.py`):** 앵커 없는 첫 보고서가 교정 1회 뒤 통과 / 교정 후에도 실패하면 `report_incomplete`(호출은 첫 보고서 + 교정 2회) / 두 번째 교정에서 통과, 새 문제 원문이 두 번째 prompt에 실림 / 첫 검사를 통과하면 교정 호출 없음. 기존 report 검사 테스트 포함 41건, 관련 묶음 1141건 통과(로컬, Windows, Python 3.12).
- **문서:** `docs/manual.md` 보고서 절.
- **미검증:** 실제 모델이 교정으로 앵커를 다는지(18차가 첫 근거).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
