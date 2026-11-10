# 2026-11-11 claude-worker1 — #1331 r7: 판정 스크립트 겹침 기준

## 결론
5회차(req_9190753bcd)를 다시 판정해 overlap·session·window가 PASS입니다. 겹친 쌍은 데이터 받기(data_steward)와 index(engineer) 1쌍, 겹친 동안 로그인 1회입니다. 전에는 두 가지 때문에 FAIL이었습니다.
- gateway 이벤트에 step_id가 없어 `None == None`으로 모든 쌍을 같은 단계로 건너뛰었습니다.
- 지난 회차는 이벤트 보존 기간이 지나 결과 시간이 없었습니다. step_id만 고치면 모든 단계가 끝나지 않은 것으로 보여 모든 쌍이 겹친다는 거짓 PASS가 나옵니다.

## 바뀐 것
- `tools/trial/judge_1331.py`: step_id가 없으면 같은 단계로 보지 않음. 결과 이벤트가 하나도 없으면 단계 폴더의 `events.jsonl`에서 단계 이름·시작·결과 시간·HPC 도구 사용을 읽음(`file_intervals`)
- `tools/trial/test_judge_1331.py`: 시험 3건

## 실행한 것
- `pytest tools/trial/test_judge_1331.py`: 30 passed
- 5회차 판정: overlap·session·window PASS (genes·verify·public 건너뜀)
- 10회차 판정: genes·window·public PASS, overlap·session FAIL(재사용 회차라 겹칠 단계 없음), verify FAIL(원격 보관 산출 3개, #1347 r4)

## 미해결
- 10회차 verify는 #1347 r4 병합 뒤 다시 돌립니다.
