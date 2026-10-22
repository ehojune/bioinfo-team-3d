## #678 일부 — 볼륨 번호 0의 중복 억제 방지 (source dea14b8, 이전 384db0c)

- root 선정 C1 P3: `_volume`이 `st_dev` 0(UNC·특수 볼륨)을 알려진 같은 볼륨처럼 비교해, 0끼리 같다는 이유로 gateway 상태 행을 뺄 수 있었다.
- 수정: 0이면 `OSError`(판정 불가)를 내 기존 경로대로 행을 남긴다. None을 돌려주면 None끼리 같아 다시 누락되므로 쓰지 않았다. 정상 볼륨의 중복 억제, 10 GiB·5% 기준, 폴더 무생성은 그대로다.
- 검사: `tests/test_disk_preflight.py`에 순수 회귀 1건을 더했다. 실제 `_volume`·`_disk_rows` 분기에서 모든 경로의 `st_dev`가 0이고, 기존 행은 넉넉하며, 상태 드라이브만 부족한 경우다. 로컬은 venv에 `websockets`가 없어 doctor를 import할 수 없어서 AST 추출로 확인했다. 새 코드는 경고하고 0360ac1 코드는 누락했다. 기존 세 시나리오는 그대로 통과했다. 3.10 AST·profile·public·diff 검사 통과. pytest는 CI 몫이다.
- doctor 부모 탐색 중복(P3)은 이번에 refactor하지 않았다. #678의 나머지 범위는 그대로 열려 있다.
