## 단계
#1269(#1184 후속): PowerShell 디스크 확인이 `$r = iwr ...`·`[void](...)`처럼 명령 이름이 첫 단어가 아닌 구문을 놓치던 부류. 같은 종류의 지적이 일곱 번째라 구문을 더 읽지 않고 안전망으로 닫는다.

## 한 일
- `labhq/disk_space.py`: `_ps_unplaced`를 더했다. 글에 다운로드·압축 해제 cmdlet·별칭이 단어로 든 횟수가 tokenizer가 명령 이름으로 찾은 횟수보다 많으면, 현재 폴더와 글에 적힌 모든 드라이브를 `_assess`로 확인한다.
- 여유가 `disk_fail_free_gb` 아래일 때만 ask이고 deny는 없다. 크기를 모르니 경고 기준만 걸리면 경고다.
- 오탐: 문자열·주석 속 단어와 `curl.se` 같은 URL은 단어 경계로 가려내고, 남는 것은 ask로만 끝난다.
- 시험 11건 추가, 닫히지 않은 따옴표 시험 1건을 새 계약에 맞춰 바꿨다(여유가 없으면 ask, 있으면 통과).

## 확인
- Windows `tests/test_disk_shell_check_678.py` 175 passed.

## 하지 않은 것
- manual은 고치지 않았다(W2 PR1064 충돌 때문). 고칠 문장: 한·영 manual 셸 다운로드 단락의 "an `iwr` that does not write a file and a path with a `$` variable are left alone" 뒤에 "a cmdlet the reader cannot place (an assignment, a cast) is checked against the current drive" 취지를 더한다.
