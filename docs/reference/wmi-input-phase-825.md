# WMI framed stdin — #825

Lookup-only. EOF 의존성을 줄이는 대안이며 timeout 원인 수정이나 native 성공을 입증하지 않는다. 초기 CI는 `powershell_started`만, 후속 CI는 `header_read`까지 남긴 뒤 timeout됐다.

[Provider](../../labhq/windows_broker.py)는 4-byte little-endian unsigned 길이 뒤 ensure_ascii UTF-8 JSON을 stdin으로 한 번 보낸다. Receiver는 `OpenStandardInput`·`BinaryReader.ReadUInt32/ReadBytes`로 선언 길이만 읽고 EOF 추가 read를 하지 않는다. 짧은 header/payload·잘못된 길이·strict UTF-8/JSON·구조 오류는 WMI 전에 고정 실패 응답(code -1)으로 끝난다. Python에서는 `create_failed`로 처리하며 원문 예외를 기록하지 않는다.

입력 예산은 **1 MiB**, 출력은 기존 **8192 bytes**다. 입력 원문 문자 수의 하한을 먼저 검사해 escaping 할당을 제한하고 실제 직렬화 bytes를 검사한다. 긴 절대경로·quote·Unicode 환경의 구성과 경계값을 synthetic으로 확인했다. OS 최대값은 아니며, 이 예산을 넘는 유효한 환경도 `request_too_large`로 거부한다. 잘라 보내지 않는다. Receiver payload 할당은 길이 검사 뒤 최대1 MiB다.

| 수신 marker | 확인되는 범위 |
|---|---|
| `powershell_started` / `stdin_open` | Script 진입 / raw stream 열림 |
| `header_read` / `input_read` | 허용 길이 header / 정확한 payload 수신 |
| `input_accepted` | UTF-8·JSON·요청 구조 검사 완료 |
| `create_entered` / `create_returned` | Create 직전 / 반환. 성공 code·PID 및 identity/Job/ACK 검증은 별도 |

Marker 부재는 hang의 위치를 확정하지 않는다. 고정7단계·결과 포함8frame, finite·단조0–60000ms와 [journal](../../tests/test_web_shutdown_process.py)의 기존 tail/행/출력 상한을 유지한다. 새 전송은 command/env를 파일·argv·env override에 넣지 않고 익명 stdin에만 둔다.

Timeout10초·TIMEOUT20초·단일 시도·breakaway·nonce/envhash·identity·Job·ACK·retained-handle 정리는 그대로다. Popen timeout 뒤 회수 수명주기도 이번에는 바꾸지 않는다. Python 수신 모델·source 계약은 PowerShell 실행 증거가 아니며 실제 검증은 root 검토 뒤 remote native CI에 남는다.

Nested launcher fixture는 실패 시 기존 journal allowlist와 얻을 수 있는 parent exitcode를 출력한다(JSON 최대4096자). 원문 parent 로그는 출력하지 않으며 예외·45초 한도·native assertions를 유지한다. 결과 파일 부재만으로 hang이나 같은 provider 원인을 단정하지 않는다.

Clean-install의 `up` 실패도 임시 폴더 삭제 전에 기존 launch journal의 두 supervisor 사실을 출력한다. Tail16 KiB·최근8행을 고정 enum/수치로 투영하며 JSON은4096자 이하다. 누락·읽기 실패·잘린 tail·malformed 행을 표시하고 원래 실패를 유지한다. 명령·환경·경로·PID·원문 로그는 추가 출력하지 않는다. 별도 clean-install CI의 provider timeout은 phase 증거가 없어 원인 미확정이다. 다음 remote 실행에서 수신/Create/ACK 중 관측된 경계를 구분하며 native suite 통과로 설치 후 시작 성공을 대신하지 않는다.
