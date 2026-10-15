## 결론

PR #659의 P1 4285239660 수정. task grant는 `uv run --no-project --no-config <관리 script.py>` 직접 실행만 허용합니다. Refs #638, Refs #553.

## 바뀐 것

- 두 옵션을 모두 요구해 hash 밖 cwd의 project/backend·설정 탐색을 막습니다. 옵션 순서는 자유입니다.
- 다른 `uv run`·`python`·`python3`·`uvx`와 환경 변수 접두는 grant 없이 매번 카드입니다.
- 기존 닫힌 문법·쓰기 link 검사·관리 스킬 전체 hash·작업 종료 시 만료는 유지합니다.

## 확인

- 새 회귀 26건: 수정 전 10 failed/16 passed → 수정 후 모두 통과.
- 관련 pytest: 443 passed/13 skipped. cwd에 pyproject·backend·uv.toml을 만든 뒤 카드/재사용을 검증했습니다. 명령과 backend는 실행하지 않았습니다.
- 매뉴얼과 문법 참고를 갱신했습니다. 공개 검사·patch-notes·notes-index 결과와 push 뒤 CI는 로컬 인계 기록에 남깁니다.

## 남은 것

CI 결과 확인 뒤 총괄에게 인계합니다. 리뷰 재요청·병합은 하지 않습니다.
