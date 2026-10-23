Closes #654
PR #899

재시작 뒤 재개 카드를 승인하면 CP1(계획 승인) 카드가 바로 또 뜨던 문제를, 재개 카드 한 번으로 줄였습니다.

- CP1 전에 멈춘 첫 연구 계획이면 재개 카드에 단계·직원·지시 앞부분·비용 추정·집행 상한을 싣고, 승인하면 같은 계획 hash의 CP1 영수증(`via=resume_card`)을 한 번만 기록합니다. 집행 상한은 기존 CP1 승인 경로로 설정됩니다.
- hash 불일치·이어 가기·단계 실행 뒤 재승인·멈춘 의미 장치(`configured_off` 제외)·paper skill 줄·재배정 안내는 CP1 카드를 그대로 묻습니다. 계획이 없으면 재개만 묻습니다.
- `tests/test_resume_cp1_654.py` 6건: 수정 전 4건 실패·대조 2건 통과, 수정 뒤 6건 통과. 관련 묶음 283+92건, resume·interrupt 언급 파일 838건 통과(Windows, Python 3.12, 로컬).
- 미검증: 웹 UI 표시(summary 텍스트만 사용·`web/` 변경 없음), 실제 PI state·실요청, CI(3.10·Windows).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
