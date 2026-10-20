## 결론
PR #768. 감독 시작 실패 뒤 정리도 실패하면 PID 기록을 보존하고 오류를 알립니다. 다음 `down`·`up`이 같은 감독을 다시 찾습니다. Fixes #627.

## 변경
- `_await_child_record`는 `_terminate_tree`가 성공했을 때만 감독 PID 파일을 지웁니다.
- 정리 실패 시 기존 시작 실패 이유와 감독 역할·PID·종료 오류·기록 보존 안내를 stderr에 쓰고 exit 2로 끝납니다.
- manual에 실패 후 기록 보존을 설명했습니다. LF 기준 162,210→162,351자(+141).

## 검증
- 새 mock 회귀 6개: gateway·runner × 감독 alive/gone/identity unreadable. 수정 전 6 failed → 수정 후 통과.
- 기존 성공·중복 감독 방지·종료 실패 회귀를 포함해 선택한 mock-only tests 11 passed. PID 기록 원문·종료 호출 인자·stderr·exit 2·기존 감독 재인식을 확인했습니다.
- 실제 process 생성·종료, proc_tree/supervisor integration, PI up/down/status, 원자료·HPC·연구 실행·설치는 하지 않았습니다. 전체 suite는 CI에 맡깁니다.
- Python 3.10 AST·public·diff·patch-notes·notes 형식 검사를 확인했습니다.

## 남은 것
CI·리뷰·병합·배포는 총괄이 이어받습니다. 실제 운영 환경 검증은 하지 않았습니다.
