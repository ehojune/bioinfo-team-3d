# #1413 — Windows venv Job 확인

## 결과
- venv launcher의 직계 자식인 Job wrapper를 두 process snapshot과 생성 시각으로 확인하고 핸들을 유지한다. 종료까지 확인하지 못하면 UNKNOWN으로 남겨 재시도를 막는다.
- 이 PC의 venv는 실제로 launcher PID와 interpreter PID가 다르다. 기존 코드는 같은 PID를 요구해 Job을 unconfirmed로 기록했다.
- 기존 doctor 경고는 doctor가 속한 바깥 Job에 관한 별도 관측이다. 동일 원인으로 판정할 근거는 없다.

## 확인
- Windows venv Python으로 관련 시험: 199 passed, 2 skipped. 기존 실패 두 parametrization을 포함한다.
- 대상 자식의 부모 변경·PID 세대 불일치·미확인 종료를 거부하는 회귀를 추가했다.
- 실제 직원 요청과 배포는 미검증이다. Codex 비용 UNKNOWN 차단은 유지한다.
