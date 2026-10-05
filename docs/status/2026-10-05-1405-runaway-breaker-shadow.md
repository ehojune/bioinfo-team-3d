## 2026-10-05 · #59 ② — 실행 중 폭주 감지(그림자)

- 결론: Codex·Antigravity에는 max_turns나 예산 상한이 없어, 같은 명령을 되풀이하는 직원을 막는 것이 task timeout뿐이었다. runner가 이제 직원 이벤트를 보고 루프를 알린다. 그림자 단계라 멈추지 않는다.
- 바뀐 것: `labhq/runner/breaker.py`(부수효과 없는 감지기: 같은 도구·입력 8번 연속, 실패한 도구 호출 5번 연속, 신호마다 task당 한 번). runner의 task `emit`이 `agent.breaker` 이벤트와 피드 경고(`agent.log` alert)를 낸다. 엔진은 성공을 알리지 않으므로 오류 없이 지나간 도구 호출이 실패 연속을 끊는다. 매뉴얼 로드맵.
- 실행한 것: `tests/test_breaker.py` 5건(반복·입력 변경·실패 연속과 끊김·오류 줄 여러 개, runner에서 경고 뒤에도 task가 정상 종료). 전체 pytest 3937 passed·54 skipped(첫 실행의 1건 실패는 다시 나오지 않았고 이름을 잡지 못함).
- 미해결: 멈추기(steer → 제한 → stop), 작업 폴더·HPC 진행 없음 신호, hook 제어(steer·pause·halt)는 #59에 남는다.
- 근거: `labhq/runner/breaker.py`, `tests/test_breaker.py`.
