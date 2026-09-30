# 이 저장소에서 작업하는 에이전트에게

시작 전에 `HANDOFF.md`와 `STATUS.md`를 읽는다.

- 작업 순서와 ⛔ 체크포인트는 HANDOFF.md를 따른다.
- 단계마다 브랜치에서 작업하고 PR을 연다. main에 직접 push하지 않는다. PI 위임(2026-09-28): 병합은 Claude가 판단해서 한다. 자동 병합 워크플로는 껐다.
- 변경 후 `pytest -q`를 돌린다. 테스트를 지우거나 약하게 만들지 않는다.
- 단계를 끝내면 PR 본문과 STATUS.md 맨 위에 같은 보고를 남긴다.
- 커밋마다 `patch_notes/README.md`에 한 줄을 남긴다(패치노트만 고친 커밋은 제외). main 커밋 3개 안에 README를 한 번은 갱신한다. 행 초안은 `python scripts/patch_notes.py rows --pr N`, 검사는 `python scripts/patch_notes.py check`(CI의 `patch-notes` job).
- 이 저장소는 public이다. 커밋 전에 `scripts/check_public.sh`를 돌리고, 비밀값은 환경변수로만 다루며, `config/labhq.yaml`은 커밋하지 않는다.
- P1은 해당 PR에서 고치고 P2는 후속 issue로 넘긴다. codex 리뷰는 push 묶음마다 PR 상단 요청 댓글 한 번만 부르고, 인라인 답글·PR 본문·커밋 메시지에는 codex 멘션 문자열을 쓰지 않는다(쓰면 봇 세션이 따로 뜬다). Running 중에는 재호출하지 않는다. 고정 상한은 없다. 새 라운드는 직전 수정의 확인이 필요하거나 다른 부류의 결함이 나왔을 때만 부른다. 같은 부류의 더 좁은 변형이 이어지면 그 부류를 닫는 수정 한 번 뒤 병합하고 나머지는 후속 issue로 넘긴다. 판단이 서지 않는 쟁점만 PI에게 넘긴다. `scripts/pr_gate.py --dry-run`은 판정 보조로만 쓴다.
- `labhq/web/`은 빌드 없는 정적 파일이다. 2.5D와 3D가 `state.js` reducer를 공유한다.
- 보고는 한국어로, 기술 용어는 영어 그대로.
