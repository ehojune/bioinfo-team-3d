# 도구 승인 판정 참고

조회용 근거. 사용법은 [매뉴얼](../manual.md#승인-게이트와-쓰기-범위)을 따른다.

## 닫힌 자동 허용 문법

관리 작업의 읽기 예외에 적용한다. 기존 정책의 별도 허용·거부 규칙은 유지한다.

| 입력 | 판정 |
|---|---|
| `$`, 백틱, `$()` | 카드 |
| `X=... cmd`, assignment-only | 카드 |
| here-string·heredoc·process substitution | 카드 |
| `eval`, `source`, `.`, `exec` | 카드 |
| 맨 앞 한 번 `cd path` 뒤 `&&`만 연결 | 경로 검사 |
| `;`·pipe·`||`와 섞인 cd, 뒤에 나온 cd, 반복 cd | 카드 |
| Python 본문, 알 수 없는 명령·옵션 | 카드 |
| 리터럴 파일 명령·쓰기 대상 | 실제 경로·link 검사 |
| PowerShell의 비 URL 콜론 operand (`Data:`, `Env:`, `Registry::` 등) | 실제 작업 폴더 안 드라이브 절대 경로만 검사 계속, 나머지는 카드 |
| 명령 이름 | 셸 내장 또는 staff PATH에서 찾은 쓰기 불가한 시스템 실행 파일만 허용 |

- 쓰기는 resolve한 경로도 작업 폴더 안이어야 한다. 기존 파일은 regular file·nlink=1, 대상과 기존 상위 폴더는 symlink·junction·reparse point가 아니어야 한다. 새 파일도 기존 부모부터 검사한다. POSIX 디렉터리의 nlink는 하위 폴더 수이므로 파일의 nlink 조건과 구분한다.
- 자동 허용 근거는 `agent.log`의 `문법 안: …`, 카드 근거는 `문법 밖: 이유`. broker 통신 장애 때 로그 전달은 보장하지 않는다.
- 명령·cwd별 PATH 해석은 판정마다 한 번만 하며 실행 파일 경로를 근거에 남긴다. 시스템 위치는 Windows System32·Git `usr/bin`, POSIX `/usr/bin`·`/bin`이다. Windows는 readonly 속성 대신 현재 token의 파일·디렉터리 변경 권한을 확인한다.
- profile·함수 덮어쓰기, 판정과 실행 사이 교체 등 남은 명령 해석 우회는 #666의 OS sandbox에서 다룬다. 이 검사는 sandbox가 아니다.
- 스크립트와 실행 코드는 명령 해석만으로 전체 범위를 고정할 수 없어 매번 카드로 보낸다. 반복 승인을 줄이는 방안은 OS sandbox 격리로 별도 이슈에서 다룬다.

## 46건 재생

원본 cwd·private_paths·open_reads는 없어 합성 문맥으로 판정기만 호출했다. PI instance 실행 결과가 아니다.

| 입력 | 건수 | 수정 전 카드 | 수정 후 카드 |
|---|---:|---:|---:|
| 완전 입력·합성 문맥 | 26 | 18 | 24 |
| 요약에서 복원한 mock 삭제 | 8 | 0 | 0 |
| 잘린 입력 | 12 | 미확인 | 미확인 |
| 재생 가능 합계 | 34 | 18 | 24 |

| 남은 카드 부류 | 건수 | 사례 |
|---|---:|---|
| 대입·확장·치환을 포함한 명령 | 13 | 3,4,5,6,10,18,20,22,25,40,43,44,45 |
| cd 위치·연결 문법 밖 | 3 | 23,35,38 |
| 스킬 실행 | 3 | 13,19,39 |
| Claude 저장 출력 shell 접근 | 3 | 1,15,21 |
| 다른 직원 venv | 1 | 31 |
| PowerShell 복합 식 | 1 | 46 |

근거: `tests/fixtures/approval_fatigue.json`, `tests/test_approval_fatigue.py`, `tests/test_approval_closed_grammar.py`. 네 P1은 수정 전 자동 허용을 재현했고 수정 후 카드로 바뀐다. 잘린 12건은 skip하며 오판 0건으로 보고하지 않는다.
