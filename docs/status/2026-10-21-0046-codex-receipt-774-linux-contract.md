# PR #822 Linux fake fixture 계약 수정

- 제공된 Linux 3.10·3.12 로그의 동일 3건을 로컬 mock으로 재현했다. 진단 wrapper가 추가한 AssertionError 대신 기존 LaunchError를 기대한 test 계약 오류였다.
- AssertionError와 원래 LaunchError의 cause 동일성을 검사한다. spawn 1회·stream close·raw retry 금지·미확인 PID 종료 금지·PID/ACK 기록 부재를 유지한다.
- 수정 전 3 failed, 수정 후 관련 mock 14 passed. Python 3.10 AST·public·diff·기록 검사를 수행한다.
- 제품·Windows integration·CI/conftest·#774 수정은 변경하지 않았다. 원래 WMI timeout 원인·Create 결과는 UNVERIFIED이며 새 CI는 조회하지 않는다.
