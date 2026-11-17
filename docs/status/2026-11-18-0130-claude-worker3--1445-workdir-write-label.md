# #1445 r22·r23 작업 폴더 안 쓰기 표시와 부류 (claude-worker3)

결론: 작업 root가 개인 경로로 닫혀 있어도 작업 폴더 안으로 확인된 쓰기는 "작업 폴더" 부류이고, 카드에 "위험 높음"·"다른 개인 경로"가 붙지 않는다. 경로 미확정 허용이 작업 폴더 부류를 덮는다(한 방향). 요청 재생 A 12·B 21 유지.

바뀐 것
- `labhq/approval_grants.py`: 리터럴 파일 명령(Bash `mkdir`·`echo >`… / PowerShell `New-Item`·`Set-Content`…)의 모든 피연산자가 실제로 작업 폴더 안이면 작업 폴더 부류. 작업 폴더 안에 등록된 개인 경로에 쓰거나 열린 읽기 밖을 읽으면 개별(전에 `echo x > inputs/a.txt`가 경로 미확정으로 묶이던 빈틈).
- `labhq/tool_explanation.py`: 작업 폴더 부류 카드는 "작업 폴더 안에서 파일을 만들거나 고칩니다", 위험 "중간".
- `labhq/gateway/server.py`: `_grant_key`로 작업 폴더 카드가 경로 미확정 허용을 근거로 자동 승인된다. 반대 방향 없음.
- 정책 판정(`policy.py`)은 바꾸지 않았다. 정책에서 고치면 개인 경로 상위 아래의 `python -c`·`mv a "$DEST"`가 카드 없이 허용돼 되돌렸다.
- 시험 `tests/test_policy.py`(Bash·PowerShell 양쪽, 덮기 양방향), `tests/test_tool_card_review_fixes.py`. 매뉴얼 한·영.

실행한 것: 배포 venv 관련 시험 1,453 통과, `tests/test_ci_*.py`(로컬 `filelock` 없음 5건 외 통과), 요청 재생 A·B.

미해결: 배포본 카드 화면은 재배포 뒤 점검에서 확인한다.
