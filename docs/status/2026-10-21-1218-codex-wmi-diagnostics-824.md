Refs #799 · PR #824

새 Windows 실패는 이전 supervisor 조기 종료와 달리 WMI spawn broker에서 발생했습니다. 원인 수정이 아니라 진단 보강입니다.

- Broker는 provider timeout·protocol·native 오류와 실패 단계를 구분하고, provider가 반환한 경우에만 returncode를 기록합니다. 명령·PowerShell 원문·환경값·원문 stdout/stderr·예외 메시지는 새 진단에 넣지 않습니다.
- 시작 fixture는 detached-launch journal 끝 16 KiB에서 최대 4개 행의 allowlist 사실만 출력합니다. 누락·읽기 실패·잘못된 JSON과 잘린 tail을 표시하며 원래 예외를 유지합니다.
- 새/수정 회귀 11건 red→green, 관련 fake 계약 20건 PASS(총 31개 고유 case); 최종 journal 5건을 다시 확인했습니다. 기존 UTF-8/재채점 검증은 변경 없이 보존하며 반복하지 않았습니다. 전체 오류 trace는 로컬 산출에 남깁니다.
- 성공 경로의 nonce/envhash·identity·Job·ACK 순서, deadline·단일 시도, retained-handle 및 승인 후 정리는 그대로입니다. 기존 native 함수/assertions·시작/health/PID 순서는 AST로 대조했고 Python 3.10 문법/compile 4파일과 public·diff·기록 검사를 수행했습니다.
- Windows API·process/thread·WMI/WSL·PI·연구·bench·HPC는 실행하지 않았습니다. 실제 원인과 새 head의 native CI 결과는 미확인입니다. C3 CI/policy는 변경하지 않았으며 push·PR 변경·CI/리뷰 조회·병합·배포 없이 로컬 커밋만 준비합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
