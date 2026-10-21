## HANDOFF 작업 배정 정책 현행화 (source c602ad1, base 99f1c49)

- HANDOFF "현재 우선순위"의 "신규 일반 작업 보류 해제" 문장을 현재 PI 결정으로 바꿨다: Codex 총괄 유지, 새 일반 Codex 작업 보류(기존 PR 마무리·긴급만), 개발은 Claude Opus 동시 최대 3, Claude 사용량은 Codex 예산 밖.
- README·manual·AGENTS는 일시적 운영 상황이라 바꾸지 않았다. 예산 수치는 적지 않았다.
- 검사: `check_public.sh`, `git diff --check`, `patch_notes check`, `notes_index --validate`(정적). 제품·process 실행 없음.
