## 결론
PR #766. 이름으로 여러 프로세스를 종료하는 명령을 승인 카드 전에 거부하고 자기 작업 폴더의 PID·시작 시각 확인을 안내합니다. Refs #681. 백그라운드 PID tracker(#675)는 남아 있어 부분 해결입니다.

## 변경
- `taskkill /IM`·`//IM`(필터 포함), `pkill`·`killall`, PowerShell 이름·pipeline 종료를 정적으로 검사합니다. 연결·fallback·shell wrapper도 확인합니다.
- policy와 broker에서 같은 guard를 쓰며 거부 시 pending future나 승인 카드를 만들지 않습니다.
- PID 지정 명령과 기존 runner의 PID·시작 시각 검증 cleanup은 유지합니다. manual 162,210→162,588자(LF).

## 검증
- 새 guard 회귀는 명령 문자열·fake broker만 사용합니다.
- 최초 관련 tests 327 passed/6 skipped 중 임시 parent/child fixture integration 1개 실행. PI 운영 검증은 안 했습니다. 실제 프로세스 종료 금지 범위를 넘긴 검증 실수이며 영향은 별도 확인 대상입니다.
- 추가 guard 수정 후 실제 fixture를 제외한 관련 tests: 331 passed, 6 skipped, 1 deselected (`-k 'not real_parent'`). 이후 test 재실행 없음.
- public·diff 검사를 통과했습니다. patch-notes·notes 형식도 확인했습니다. 전체 suite는 CI에 맡깁니다.

## 남은 것
- PID tracker·정리 도구(#675), 동적으로 조립한 명령의 OS 격리, 다른 엔진의 sandbox 경계는 이번 범위 밖입니다.
- CI·리뷰·병합·배포는 총괄이 이어받습니다. 봇 호출·병합·배포는 하지 않았습니다.

