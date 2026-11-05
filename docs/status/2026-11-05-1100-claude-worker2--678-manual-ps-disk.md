## 단계
#678 rev12: PR #1184(#1167)와 PR #1270(#1269)이 바꾼 PowerShell 디스크 확인을 한·영 manual에 반영한다.

## 한 일
- `docs/manual.md`·`docs/manual.en.md`의 셸 다운로드·압축 해제 확인 단락에 안전망 문장을 더했다. 읽지 못했지만 이름이 보이는 다운로드는 현재 드라이브와 명령에 적힌 드라이브를 확인하고, 결과는 ask·경고·허용이며 deny는 없다.
- #1184분(`-OutFile`·`Expand-Archive`·BITS)은 이미 manual에 있어 그대로 두었다.

## 확인
- 동작은 `labhq/disk_space.py`의 `_ps_unplaced`와 #1270 patch note·status로 대조했다. 코드는 바꾸지 않았다.

## 하지 않은 것
- 시험은 돌리지 않았다(문서만 변경).
