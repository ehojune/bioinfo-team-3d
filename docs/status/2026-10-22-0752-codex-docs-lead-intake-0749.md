## HANDOFF 작업 배정 정책 현행화 (source c602ad1, base 99f1c49)

- HANDOFF "현재 우선순위"의 "신규 일반 작업 보류 해제" 문장을 현재 PI 결정으로 바꿨다: Codex 총괄 유지, 새 일반 Codex 작업 보류(기존 PR 마무리·긴급만), 개발은 Claude Opus 동시 최대 3, Claude 사용량은 Codex 예산 밖.
- README·manual·AGENTS는 일시적 운영 상황이라 바꾸지 않았다. 예산 수치는 적지 않았다.
- 검사: `check_public.sh`, `git diff --check`, `patch_notes check`, `notes_index --validate`(정적). 제품·process 실행 없음.
- 첫 CI(head `e9ee408`, run 39689292875): 문서 diff라도 HANDOFF 문구 변경과 새 status 파일 때문에 full로 선택됐다. Windows에서 `test_supervisor_process.py`의 감독 조기 종료 1건이 실패했다(#844와 같은 node, 원인 미확정). Linux는 당시 진행 중이었다.
- 통합: 승인 main `e41cfe2`(#843 `bf51c58`, #841 `e7c3159` 포함)를 merge `3ab4f9a`로 명시 통합했다. 충돌은 없고 main 대비 차이는 HANDOFF 한 문단과 이 기록 2파일뿐이다. 새 CI가 한 번 통과해도 #844 해결로 보지 않는다.
