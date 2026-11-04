# runner broker port 사전 충돌 검사 (#618 P2)

| 항목 | 내용 |
|---|---|
| 문제 | 설정 파일·state 폴더·runner id가 모두 달라도 `runner.broker_port`가 같으면 두 번째 runner가 bind에 실패했다(원인 불명 포트 오류) |
| 방식 | `instance_guard.claim(…, "runner")`가 runner state lock에 이어 broker port별 lock(`labhq-broker-port-<port>.lock`)을 잡고, PC 기본 state 폴더의 `labhq-broker-port-<port>.json`에서 살아 있는 다른 설정의 owner를 확인한다. 다르면 `broker_port` notice로 시작 전에 막고 아무것도 쓰지 않는다. 같은 설정이면 통과, 끝난 owner는 교체 |
| 시험 | `tests/test_instance_guard.py` +3: 거부(문구·고치는 명령·claim 없음), 다른 포트는 충돌 없음, 죽은 owner 복구와 거부된 claim이 기록을 바꾸지 않음 |
| 범위 밖 | `doctor`의 읽기 전용 점검에는 넣지 않았다(시작 시 claim만). 실제 두 프로세스 동시 시작·운영 인스턴스는 검증하지 않음 |
