# 2026-11-11 claude-worker3 — #1347 r4: verify의 원격 보관 산출

## 결론
`labhq verify`가 요청 전체 HPC 단계가 HPC 데이터 폴더에 일부러 남긴 산출을 "원격 보관"(크기 기록)으로 세고 문제로 보지 않는다. 로컬 파일은 sha256을 그대로 대조하고, 이 PC에도 원격 목록에도 없는 산출은 "없음"이다. #1331 10회차 verify FAIL(원격에만 있는 산출 3개)의 원인이다.

## 바뀐 것
- `labhq/evidence/audit.py`: 상태 `remote`("원격 보관") 추가(문제 상태 아님), 단계 작업 폴더 `manifest.json`의 `runs[*].hpc_remote_outputs`에서 원격 산출과 크기를 읽는 `remote_outputs`, 폴더 산출은 아래 원격 파일 합으로
- `tests/test_hpc_whole_request.py`: 원격 보관·로컬 일치·폴더 산출, 어디에도 없는 산출은 없음
- manual 07 한·영

## 실행한 것
- verify·audit 관련 시험: 550 passed, 10 skipped

## 미해결
- 원격 파일의 hash는 대조하지 않는다(원격 목록에는 크기만 있음)
