# 2026-11-11 claude-worker3 — #1347 r5: verify의 원격 보관 그림

## 결론
`labhq verify`의 보고서 그림 링크 검사가, 단계의 원격 목록(`hpc_remote_outputs`)에 기록된 그림을 "원격 보관" 경고로 돌리고 문제로 세지 않는다. 어디에도 기록이 없는 그림 링크는 지금처럼 문제다. #1331 10회차 verify의 남은 1건(2.1MB QC 그림)이다.

## 바뀐 것
- `labhq/evidence/audit.py`: 단계별 원격 목록을 모아, "링크한 그림이 묶음에 없습니다" 문제 가운데 `steps/<step>/outputs/...` 또는 `<workdir_id>/outputs/...`가 원격 목록에 있는 것만 경고로 옮김
- `tests/test_hpc_whole_request.py`: 묶음 형식·미매핑 형식 통과, 기록 없는 그림은 문제

## 실행한 것
- verify·audit·그림 관련 시험 통과(아래 PR 본문)

## 미해결
- 그림 검사 자체(`report_figures.py`)는 바꾸지 않았다. 문제 문구를 audit이 해석하므로, 문구가 바뀌면 같이 고쳐야 한다
