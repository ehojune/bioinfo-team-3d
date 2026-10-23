Closes #654
PR #899

재시작 뒤 재개 카드를 승인하면 CP1(계획 승인) 카드가 바로 또 뜨던 문제를, 재개 카드 한 번으로 줄였습니다.

- CP1 전에 멈춘 첫 연구 계획이면 재개 카드에 단계·직원·지시 앞부분·비용 추정·집행 상한을 싣고, 승인하면 같은 계획 hash의 CP1 영수증(`via=resume_card`)을 한 번만 기록합니다. 집행 상한은 기존 CP1 승인 경로로 설정됩니다.
- hash 불일치·이어 가기·단계 실행 뒤 재승인·멈춘 의미 장치(`configured_off` 제외)·paper skill 줄·재배정 안내는 CP1 카드를 그대로 묻습니다. 계획이 없으면 재개만 묻습니다.
- `tests/test_resume_cp1_654.py` 6건: 수정 전 4건 실패·대조 2건 통과, 수정 뒤 6건 통과. 관련 묶음 283+92건, resume·interrupt 언급 파일 838건 통과(Windows, Python 3.12, 로컬).
- 봇 리뷰 반영(2차): 카드 detail에 CP1과 같은 `plan_canonical` 등을 싣고 웹이 계획 전체를 그립니다. `resume_cp1`은 영수증 저장 save에서 지웁니다. paper skill·재배정 안내가 있는 계획에는 통합 승인 카드를 만들지 않습니다. python 7건·web 1건 추가(수정 전 실패 확인), 관련 묶음 303건 통과. web 7건은 main에서도 실패하는 브라우저 의존 테스트입니다.
- CI: head `c529d574`의 `pytest (3.12)` 실패 1건은 무관한 timing 테스트(`test_policy` 경로 스캔 선형성)였고 재실행에서 통과했습니다. 부분 재실행(attempt 2)은 Windows 커버리지 artifact가 없어 `validation-manifest`만 실패해 전체를 새 push로 다시 돌렸습니다.
- 미검증: 웹 UI 표시(summary 텍스트만 사용·`web/` 변경 없음), 실제 PI state·실요청, CI(3.10·Windows).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
