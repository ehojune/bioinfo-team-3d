Refs #825 · #824

EOF 대기 대신 길이를 명시하는 stdin 대안을 구현했습니다. 실제 WMI 원인 수정/성공은 미확인입니다.

- 4-byte little-endian header와 정확한 UTF-8 JSON payload를 읽고 EOF 추가 probe를 하지 않습니다. 입력 예산1 MiB를 문자 하한·직렬화 bytes·수신 할당 전에 검사합니다. OS 상한이 아니며 초과는 `request_too_large`, 잘림/encoding/JSON/구조 오류는 WMI 전 고정 실패입니다.
- Command/env carrier는 익명 stdin뿐입니다. 고정 raw/header/payload marker를 추가하고 parser·안전한 fixture allowlist를 7단계에 맞췄습니다. 출력8192 bytes·기존 timing·timeout10/TIMEOUT20·단일 시도·nonce/envhash/identity/Job/ACK/정리 계약은 유지했습니다.
- 수정 전3건 실패 후 wire/model/source20건·관련 fake50건 PASS(70고유 case). 최종 긴 경로 stress와 receiver source 각1건 재확인. 수신 모델은 .NET/PowerShell 실행이 아님을 명시했습니다. 전체 trace는 로컬 산출에 보존합니다.
- Python3.10 AST/compile·변경 외 gate/cleanup/native assertions 동일 대조·public·diff·기록 검사를 수행했습니다. [상한·진단 해석](../reference/wmi-input-phase-825.md)에 한계를 기록했습니다.
- C3·selector/workflow는 변경하지 않았습니다. 실제 process/thread/PowerShell/WMI/WSL/native/CLI/PI/runtime/연구/bench/HPC 미실행. 로컬 커밋만 준비하며 push·PR/API/CI 조회·병합·배포 없이 독립 검토와 통합을 총괄에게 넘깁니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
