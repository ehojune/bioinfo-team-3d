## 결론

홈·state 격리와 dry-run 정리 도구를 추가한 PR #554의 P1(4256352456)을 `d38535f`에서 수정했다. 실제 사용자 `.labhq` 설정을 파일 조회 전에 차단한다.

## 바뀐 것

- `Settings.load`의 명시 경로·`LABHQ_CONFIG`, staff 설정 복사.
- instance 경로·포트 탐색, init·bench 설정 확인.
- Paper2Agent cache·skill·설치 출처, 자식 CLI 설정 경로.
- doctor의 roster·설정 소유자 조회와 Codex 설정·marker 읽기. 기존 store guard도 유지한다.

## 확인

- 합성 홈 회귀 17건: 이전 head `590a41d`에서 모두 실패, 수정 후 통과. 격리 파일 전체 23 passed.
- 관련 테스트 187 passed. 이후 추가한 자식 설정 경로는 격리 테스트와 전체 suite로 확인한다.
- Windows 전체: **5646 passed, 66 skipped, 1 failed**(11분 40초). 실패는 `test_semantics_shadow_breaker.py::test_a_stuck_worker_turns_it_off_and_its_late_result_is_dropped`의 `disabled.json` 읽기 PermissionError다. 해당 파일만 재실행해 **50 passed**. 전체 통과로 판정하지 않는다.
- 공개 검사·patch-notes·notes-index·diff 검사를 수행했다. basetemp는 `%TEMP%/pt-testisofix` 한 폴더를 사용했다.

## 미해결

- 전체 suite의 Windows PermissionError 1건은 범위 밖이므로 수정하지 않았다. CI는 push 뒤 현재 head의 PR checks를 기준으로 총괄이 확인한다.
- basetemp 삭제가 자동 승인 검토의 `blocked by policy`로 차단됐다. 리뷰 요청·병합은 하지 않는다.

Refs #545, #221

🤖 Generated with Codex (gpt-6) for the labhq dev lead
