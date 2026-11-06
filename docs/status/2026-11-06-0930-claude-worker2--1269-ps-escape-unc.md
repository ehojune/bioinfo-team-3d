## 단계
#1269 rev2: PowerShell 디스크 확인에 남아 있던 두 건(cmdlet 이름 안의 backtick escape, UNC 대상).

## 한 일
- `labhq/disk_space.py`: 안전망(`_ps_unplaced`)이 이름을 셀 때 backtick을 먼저 뺀다. 이름이 backtick으로 가려진 `$r = iw`r ...`도 이제 센다.
- UNC: 쓰는 명령(`iwr`류·`Expand-Archive`·BITS)의 단어와 안전망 경로의 글 전체에서 `\server\share`(와 `//server/share`) 뿌리를 모은다. Windows에서 여유를 읽을 수 있으면 기준 아래에서 ask, 경고 기준 아래에서 경고, 여유가 있으면 조용하다. 읽을 수 없으면(다른 OS 포함) 경고만 남기고 막지 않는다. 읽기만 하는 명령의 UNC와 URL 속 `//`는 보지 않는다.
- 시험 11건 추가(`tests/test_disk_shell_check_678.py`): backtick 4종과 placed 경우, UNC 읽을 수 없음 4종, 쓰지 않는 명령, Windows에서 읽을 수 있는 경우.

## 확인
- `tests/test_disk_shell_check_678.py` 189 passed(Windows 로컬).

## 하지 않은 것
- manual 04·장 문장은 고치지 않았다(claim 밖). 고칠 문장: 안전망 단락에 "cmdlet 이름 안의 backtick은 무시하고 센다"와 "UNC 대상은 읽을 수 있을 때만 확인하고 읽지 못하면 경고만 한다".
- 같은 종류가 더 나오면 병합하고 기록만 한다는 지시를 따른다.
