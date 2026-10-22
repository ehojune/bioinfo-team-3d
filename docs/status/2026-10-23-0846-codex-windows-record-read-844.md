## 단계
#844 Windows receipt·ACK 읽기 보강

## 한 일
- 기한이 있는 `_poll`에서만 winerror 32 또는 winerror 없는 EACCES를 다시 읽습니다. 명시된 다른 winerror·다른 errno·첫 request 읽기는 바로 실패합니다.
- 부모 receipt 대기와 bootstrap ACK 대기가 각자 `denied_reads`를 0부터 세어 실패 기록·정지 줄에 정수만 남깁니다.
- provider 첫 단계를 고정 문자열로 바로 flush하고 stdout 종류·byte 수를 기록합니다. 내용은 남기지 않습니다.
- 20초 기한·0.02초 간격·생존·만료·nonce·identity·환경·Job 확인과 WMI create 1회는 그대로입니다.

## 테스트 결과
- Python 3.10 문법 파싱을 했습니다.
- broker 원본을 가짜 `windows_launch`로 실행하고 세 시험 파일(broker·phases·frames)을 가짜로만 돌렸습니다. 새 코드는 119건 통과입니다. main의 broker에서는 28건이 실패하며, 야간과 같은 증상인 winerror 없는 EACCES 정지가 포함됩니다.
- 기록 검사·공개 검사·git diff --check를 했습니다.
- pytest·실제 Windows·WMI·Job·CI는 돌리지 않았습니다.

## 막힌 점
원래 Win32 원인은 미확인입니다. clean-install 진단 줄은 허용 목록 밖의 새 stdout 항목을 아직 보여 주지 않습니다(`scripts/clean_install_check.py`, 별도 작업). CI·병합은 총괄 담당입니다.

🤖 Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
