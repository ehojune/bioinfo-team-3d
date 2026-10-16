## 결론

`labhq init`이 GitHub 토큰을 묻되 필수는 아니게 하고, doctor가 토큰 앞부분 대신 실제 권한을 탐침해 빠진 권한을 이름으로 알립니다. Fixes #687.

## 바뀐 것

- `labhq/github_token.py`: 환경 변수 → 이 계정만 읽는 `github.token_file` 순서로 토큰을 읽습니다. 파일의 토큰은 gateway가 시작할 때만 자기 환경에 올리고(직원·runner·MCP에는 안 감), 직원에게는 개인 경로로 막힙니다.
- 권한 탐침은 아무것도 만들지 않습니다. 이름 없는 `POST /user/repos`, 내용 없는 `PUT contents`, 제목 없는 `POST issues`에 권한이 있으면 422, 없으면 403이 옵니다(2026-10-16 실측, #687 댓글). classic 토큰은 `X-OAuth-Scopes`로 판정합니다. 네트워크가 안 되면 경고 대신 "처음 게시 때 확인"으로 둡니다.
- init: 필요한 권한 목록과 토큰 만드는 곳을 보인 뒤 숨김 입력으로 묻습니다. Enter로 건너뛰면 결과는 로컬에만 저장합니다. 토큰 값과 파일 위치는 출력하지 않습니다.
- doctor: 토큰이 없으면 "결과는 로컬에 저장"(ok), 있으면 탐침 결과 한 줄. fine-grained 토큰에는 Repository access를 All repositories로 두라는 안내를 붙입니다. 접근 범위는 API로 알 수 없기 때문입니다.
- test 환경은 운영자의 `GITHUB_TOKEN`·`GH_TOKEN`을 지웁니다. 그렇지 않으면 doctor test가 GitHub를 실제로 탐침합니다.

## 확인

- 새 test 21개와 바꾼 #607 doctor test 4개, private path·init·doctor·config·게시 관련 test 통과(441 passed, 323 passed).
- #607 test는 "fine-grained면 늘 경고"를 고정했는데 #687이 바꾸는 동작이라, 탐침 결과로 판정하도록 고쳤습니다. AlphaGenome init test 2개는 getpass 질문 수를 세므로 AlphaGenome 질문만 세게 했습니다.

## 남은 것

- 요청이 끝날 때 "GitHub로 정리할까요?" 질문은 #688입니다.
