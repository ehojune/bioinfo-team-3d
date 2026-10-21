# WMI 입력 경계 진단 — #825

Lookup-only. 이 변경은 timeout 원인을 고치거나 native 실행 성공을 입증하지 않는다.

main job119031364879와 PR #825 job119038816296은 `provider_timeout`과 `powershell_started`만 남겼다. 기존 한 줄의 입력 읽기·JSON 변환 중 어디까지 끝났는지는 미확인이다.

| 마지막 수신 marker | 확인되는 범위 |
|---|---|
| `powershell_started` | Script 진입. 입력 읽기 완료는 미확인 |
| `input_read` | `ReadToEnd()` 반환. JSON 변환 완료는 미확인 |
| `input_accepted` | JSON 변환 완료. WMI Create 결과는 미확인 |
| `create_entered` / `create_returned` | Create 직전 / 반환. 성공 code·PID 및 별도 identity/Job/ACK 검증은 여전히 필요 |

Marker 부재는 해당 연산의 hang을 확정하지 않는다. 연산 뒤 marker 전 스케줄링 지연도 구분되지 않는다.

- [Provider](../../labhq/windows_broker.py): 고정 순서 5개 marker와 마지막 결과 1개, 최대 8192 bytes. 타이밍은 기존 finite·단조·0–60000ms 검사다. 잘못된·잘린·중복·순서 불일치 frame을 거부한다. Timeout의 부분 결과는 PID/성공으로 쓰지 않는다.
- [Fixture](../../tests/test_web_shutdown_process.py): 기존 journal tail/행/출력 상한과 redaction을 유지하며 새 enum만 보존한다. 입력·명령·환경·경로·원문 출력은 새 marker에 포함하지 않는다.
- Timeout10초·TIMEOUT20초·단일 시도·breakaway·nonce/envhash·identity·Job·ACK·retained-handle 정리는 변경하지 않는다. 실제 판정은 검토 후 remote native CI에 남는다.
