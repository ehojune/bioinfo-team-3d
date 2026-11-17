# #1445 r20 부류 판정 다듬기 (claude-worker3)

결론: 작업 폴더 안 `mv`·`sed -i`·`perl -i`, `$'...'` 따옴표, 대상 미확정 쓰기, 자기 세션 `tool-results` 읽기를 설계한 부류로 옮겼다. 요청 재생 사람 카드는 A 15→12, B 28→21.

바뀐 것
- `labhq/approval_grants.py`: 네트워크·삭제 표기에서 move/rename을 뺐다. 옮기기·제자리 고치기의 피연산자를 쓰기 대상으로 판정하고, 풀 수 없으면 경로 미확정으로 둔다. `$'...'`를 풀어 검사한 뒤 경로 미확정으로 둔다. 대상 미확정 쓰기는 개별 대신 경로 미확정으로 둔다. 자기 세션 `tool-results` 파일은 개인 경로 검사에서 작업 파일로 본다.
- `labhq/policy.py`: 셸 단어 분할을 `shell_segments`로 꺼내 공유한다(동작 같음).
- 시험: 표의 줄마다 묶음·개별 양쪽 사례(`tests/test_policy.py` 새 시험 3개, 21 사례).
- 문서: `docs/manual/11-safety(.en).md`, `docs/reference/request-a-replay-1445.md`.

현재 폴더 확인: 판정은 실제 실행에서도 직원 셸의 현재 폴더를 받지 않는다(runner `_on_approval`이 `workdir=ws.dir`). 고칠 곳은 이 PR의 배정 범위 밖(어댑터)이라 넣지 않고 총괄에게 알린다.

미해결: 개인 경로 상위 폴더 아래에서는 작업 폴더 안 보통 쓰기도 `unresolved_path`로 판정된다(기존 동작). 그래서 묶음은 실제로 모두 그 부류다.
