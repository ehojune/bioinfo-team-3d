# 2026-10-07 · PR #464

## 결론

Paper2Agent 설치 갱신에서 원본 누락·복사 실패가 기존 skill을 지우던 버그를 고칩니다.

## 바뀐 것

- 원본 폴더와 SKILL.md를 확인하고, Claude Code·Codex의 새 복사를 모두 staging에 마친 뒤 설치를 교체합니다.
- 교체 실패는 기존 두 설치로 rollback합니다. 복구도 실패하면 백업을 보존하고 경로를 오류에 표시합니다.
- install_skill의 인자·반환값과 설치 위치를 유지하고, manual에 실패 때의 동작을 적었습니다.
- 세 PR의 최종 status를 각 PR 본문과 맞춰 알려진 P1/P2 후속이 보고에서 빠지지 않게 했습니다.

## 실행한 것

- tmp_path와 mocked git만 사용한 실패 주입 회귀: 14 passed.
- Windows 전체 pytest: 4467 passed, 56 skipped. 단일 basetemp 종료 삭제 확인.
- scripts/check_public.sh 통과.

## 미해결

- P2: rename 완료 직후 상태 기록 전에 중단되는 구간의 복구는 후속 #465로 넘깁니다. 현재 회귀는 동기 복사·교체 오류를 검증했습니다.

#424의 source commit 고정·license·hash·외부 전송 계약은 별도 범위입니다. 이 PR은 설치 실패 버그만 다루며 #424를 닫지 않습니다.

Refs #424

🤖 Generated with Codex for the labhq dev lead