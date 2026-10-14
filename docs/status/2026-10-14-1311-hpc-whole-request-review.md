## 결론

PR #578의 P1·P2 리뷰 지적을 고쳤다. HPC 실행 원본은 요청 묶음과 분리되고, direct 요청은 지정 직원의 HPC 권한만 따른다.

## 바뀐 것

- 4266517517(P1): 공유 실행 workspace를 `hpc/<request_id>`로 옮겨 bundle 뒤에도 manifest·job 기록·outputs를 보존한다.
- 4266517530(P2): direct 실행 위치는 지정 `agent_id`가 해당 runner에서 HPC를 쓸 때만 묻고, 아니면 local로 정한다.
- 최신 main의 파견직 사전 채용 변경을 병합해 HPC·채용 계획 검사를 함께 보존하고 branch 패치노트에 기록했다.

## 확인

- 수정 전 새 회귀 test 2건 실패, 수정 뒤 통과. 최종 관련 pytest 199 passed, 1 skipped.
- Node 52개 파일 통과. 최종 Windows 전체 pytest 6,450 passed, 67 skipped.
- 공개·patch-notes·notes-index 검사 통과. 실제 HPC·PI/trial은 실행하지 않았다.

## 남은 지적

- push 뒤 CI 확인이 남았다. 테스트 basetemp 삭제는 실행 정책이 막아 다섯 폴더가 남았다.

Refs #430
