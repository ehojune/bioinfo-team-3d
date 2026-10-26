Closes #971. Refs #606, #970. #972보다 먼저 병합하는 최소 수정입니다.

- `wait_halted()`의 초기 두 publish를 기존 cleanup try/finally 안으로 옮겼습니다. 진입 중 취소도 종료 이벤트·대기/제어 상태를 정리하고 CancelledError를 전달합니다.
- 두 publish를 각각 barrier로 멈춘 일반/요청 취소 4개 조합: 수정 전 **4 failed**, 수정 후 관련 mock/static **10 passed**. `halted_waits`·`pi_waits`·task control·DB 정리와 resume·timeout을 확인했습니다. 기존 종료 이벤트·취소 전파 assertion은 유지했습니다.
- resume 검사는 등록 직후가 아닌 `waiting_pi` 발행 완료를 기다리도록 readiness만 고쳤습니다. 취소 재현 지점은 늦추지 않았습니다. temp/state는 저장소·OS TEMP 밖에 격리했습니다.
- 첫 green 명령에 `test_staff_control.py` 전체를 잘못 포함해 hook subprocess도 실행됐습니다. 중단 후 명시한 mock/static node 10개만 검증했습니다. 연구·HPC 실행은 없습니다. 실제 process gate는 기존 CI 결과로 판단합니다.
- manual·patch notes를 포함합니다. 최종 head 필수 CI·동일 head 봇 완료·모든 head 미해결 P1=0 뒤 병합하고 #972에 반영합니다.

Refs #974. failed-job 재실행의 수집 오류도 수정했습니다.

- 같은 run/head의 유효한 Windows 실행과 원래 job·upload 시간을 대조해 artifact ID를 선택합니다. 누락·다른 출처·시간 불일치는 거부하며 coverage 검증을 유지합니다.
- 모든 재실행 run은 receipt 검증 뒤 reusable manifest를 발행하지 않습니다. Windows만 새로 실행한 경우도 포함합니다. 이전 실패 artifact는 후보에서 제외합니다. 관련 mock/static 611 passed, 실제 실패 run API에서 artifact 12615771377·attempt 1 선택을 확인했습니다.
- 최종 head CI·동일 head 봇 완료·미해결 P1=0 확인 뒤 #973→#972 순서로 병합합니다. #972 봇 대기는 #974 PI 예외이며 PASS로 기록하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
