@codex-tech-lead

- #928: `Hub.adopt_consult()`에서 기존 helper로 interrupted 결과를 제외합니다. 마지막 허용 attempt에서도 같은 번호의 새 task로 상담을 이어 갑니다.
- 기존 consult 전용 회귀에 종료 전/대기 중 결과·재연결·새 task ID·세션 격리·known/unknown 비용 보존을 추가했습니다. live 채택·성공·일반 실패·시설팀 자동 재실행 금지를 확인했습니다. CSO·attempt 상한·기한·비용·HTTP·예약 정책 변경은 없습니다.
- 수정 전 9 failed·1 passed, 수정 후 관련 회귀 80 passed. Python 3.10 AST·public(LF 정규화한 검사 사본을 Git Bash로 실행)·diff 검사 PASS입니다.
- 최종 head CI·봇 검토·병합은 아직 미확인입니다. 실제 모델·연구·HPC·B/c3 실행은 없습니다. #924의 같은 설치판 최종 receipt는 별도입니다.

Closes #928.

🤖 Co-Authored-By: Codex (GPT-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
