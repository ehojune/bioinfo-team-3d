## 결론

PR #659의 P1 네 건을 닫힌 문법·쓰기 link 검사·관리 스킬 전체 트리 hash로 수정했습니다. 카드 감소보다 정확성을 우선합니다. Refs #638, Refs #553.

## 바뀐 것

- 변수·대입·치환·간접 실행·Python 본문은 카드. cd는 맨 앞 한 번, 뒤는 `&&`만 허용.
- 쓰기 대상·부모의 실제 경로와 reparse point, 기존 파일의 nlink=1 확인.
- 작업 스크립트에는 grant 없음. runner가 고른 쓰기 범위 밖 관리 스킬만 전체 트리 hash로 재사용.
- 자동 허용 로그와 카드에 문법 판정 근거 기록.

## 46건 재생

합성 문맥에서 판정기만 호출했습니다. 실행 당시 문맥은 없어 PI instance 재실행 결과로 보지 않습니다.

| 결과·남는 카드 부류 | 건수 |
|---|---:|
| 자동 허용 | 10 |
| 대입·확장·치환 | 13 |
| cd 위치·연결 | 3 |
| 스킬 실행 | 3 |
| Claude 저장 출력 shell 접근 | 3 |
| 다른 직원 venv | 1 |
| PowerShell 복합 식 | 1 |
| 입력이 잘려 재생 불가 | 12 |

재생 가능한 34건의 카드는 수정 전 18→24건입니다. [문법과 사례 번호](docs/reference/tool-approval-grammar.md).

## 확인

- 네 P1 모두 수정 전 자동 허용을 재현, 수정 후 카드. hard link의 세 쓰기 변형과 grant 재사용 포함.
- #553·#638·기존 PR을 포함한 관련 pytest: 853 passed, 38 skipped. 마지막 추가 후 해당 두 파일: 114 passed, 12 skipped.
- Node 승인 UI 10 passed. public·patch-notes·notes-index 검사는 로컬 로그에 기록.
- 매뉴얼 149,109→148,958자, 참고 문서 2,711→1,672자.

## 남은 지적

- 잘린 입력 12건과 당시 실행 문맥은 미확인입니다. 46건 오판 0건으로 보고하지 않습니다.
- 자동 허용 로그는 broker 장애 때 전달되지 않을 수 있습니다. 경로 검사는 OS sandbox가 아닙니다.
- CI는 최종 push 뒤 결과를 이 본문에 덧붙입니다. 리뷰 요청·병합은 하지 않습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
