## #842 — hosted Windows에서 정리 실Chrome 회귀의 내부 skip 금지 (source 06a8a22)

- 빈틈: focus layout과 visual tooling의 실Chrome test는 `skipReason()`으로 안에서 건너뛰어도 `fail 0`을 출력해 부모 pytest가 PASS한다. 그래서 새 forced 회귀가 실제로 돌았다는 직접 증명이 없었다(C3 hook 0710·peer 0714). critical resizable의 Chrome 조건은 정황일 뿐이다.
- 수정: `test_office_in_node`가 hosted Windows(`os.name == 'nt'`이고 `GITHUB_ACTIONS == 'true'`, `test_windows_job_ci.py`와 같은 판정)에서 두 파일만 TAP `# skipped 0`을 추가로 요구한다. returncode·`fail 0`·기능 단언·node id·30/170초 상한·필수 26은 그대로다. Linux와 로컬의 선택적 Chrome은 넓히지 않았다.
- 등록: `config/ci/windows-boundary-v1.json`에서 `test_web_3d.py` mixed digest 한 줄만 바꿨다. 보호 node와 선택 규칙은 그대로다. 이 PR에서는 바뀐 mixed 파일이라 기존 규칙대로 파일 전체가 Windows에서 돈다.
- 검사: AST로 추출한 순수 실행(가짜 subprocess·os) 8경우가 기대대로였다. 새 head에서는 hosted Windows 내부 skip이 FAIL이고, 이전 head에서는 같은 경우가 PASS였다(빈틈 재현). 정적 CI 계약 199 passed, Python 3.10 AST, profile 검증, public·diff 검사 통과. guard blocked 0.
- 미실행: 실제 Node·Chrome·Windows. 이전 head의 CI 성공은 이 빈틈 전 결과라 새 head 성공으로 재사용하지 않는다. #842는 열어 둔다.
