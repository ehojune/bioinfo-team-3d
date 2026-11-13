# #1423 Windows Codex 설치 선택과 sandbox 기록

- Windows 앱 자동 선택은 CLI와 helper 두 개가 함께 있는 폴더만 사용하고, 선택한 폴더를 직원 프로세스 PATH에 추가합니다.
- doctor는 설치 식별값이 일치하는 과거 셸 성공만 표시하고, 이후 setup 실패나 옛 기록은 경고합니다.
- 관련 네 test 파일: 248 passed, 1 skipped. Windows subprocess reader의 UTF-8 decode 경고가 있었습니다.
- 실제 직원 셸·UAC·운영 보호는 이 배정에서 실행하지 않았습니다.
