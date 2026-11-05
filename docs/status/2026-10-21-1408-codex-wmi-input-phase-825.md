Refs #825 · #824

WMI timeout 진단만 분리했습니다. 원인 수정이나 실제 Windows 성공을 주장하지 않습니다.

- `ReadToEnd()` 반환 직후 `input_read`, JSON 변환 뒤 기존 `input_accepted`를 출력합니다. [진단 의미와 한계](../reference/wmi-input-phase-825.md)를 따릅니다.
- 순서 parser·fixture allowlist는 5단계에 맞췄습니다. 결과 포함 frame 상한은 6개이며 기존 8192 bytes·숫자/timing·redaction 상한은 유지합니다. 성공 code/PID·timeout 부분 결과 처리와 보안/정리 경로는 그대로입니다.
- 수정 전 4건 실패 → 명시한 fake/string 계약 34건 PASS(로컬 0.27초). 완료 전 각 timeout·6frame/byte 경계·잘린/중복/불일치·비밀값 부재와 journal 예외 보존을 확인했습니다. 전체 trace는 로컬 산출에 보존합니다.
- Python3.10 AST/compile·진단 외 source/native assertions 동일 대조와 public·diff·기록 검사를 수행했습니다. C3 CPUclock/CIpolicy·테스트는 변경하지 않았습니다. 실제 process/thread/PowerShell/WMI/PI/bench 미실행, native CI 미확인입니다.
- 로컬 커밋만 준비했습니다. Push·API/CI 조회·새 PR·병합·배포 없이 독립 검토와 C3 변경 통합을 총괄에게 넘깁니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
