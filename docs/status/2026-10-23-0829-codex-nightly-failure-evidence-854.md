## 단계
야간 Windows 실패 증거 보강 (#854, #844)

## 한 일
- run 39858916081의 `test_consult_restart` settle 시간 초과에 작업 상태·완료 callback 시각·경과 시간을 남기는 시험 전용 진단을 넣었습니다. 3초 상한·예외·판정은 그대로입니다.
- supervisor 실제 gateway 시험은 시작 증거를 이미 출력합니다. 야간 로그에 `stage=ack_wait error=PermissionError`(winerror 없음)가 남아 있어 시험은 바꾸지 않았습니다. 앞선 분류의 "증거 없음"은 틀렸고 여기서 정정합니다.
- 코드 읽기로 찾은 단서입니다(재현하지 않음): Windows에서 Python `open()`이 낸 PermissionError에는 winerror가 없을 수 있습니다. 그렇다면 #868의 winerror 32 재시도가 ACK 읽기에 적용되지 않을 수 있습니다. 제품 코드라 이 PR에서는 고치지 않습니다.

## 테스트 결과
- Python 3.10 문법 파싱, AST로 꺼낸 진단 함수·새 순수 시험을 가짜 작업으로 실행, settle 구조 AST 확인, 기록 검사·공개 검사·git diff --check만 했습니다.
- pytest·asyncio·제품 import·process·WMI 검사는 하지 않았습니다.

## 막힌 점
원인은 UNKNOWN입니다. 전체 Windows 야간 성공을 주장하지 않습니다. CI와 병합은 총괄 담당입니다.

🤖 Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
