# 이 저장소에서 작업하는 에이전트에게

시작 전에 `HANDOFF.md`와 `STATUS.md`를 읽는다.

- 작업 순서와 ⛔ 체크포인트는 HANDOFF.md를 따른다.
- 단계마다 브랜치에서 작업하고 PR을 연다. main에 직접 push하지 않는다. 병합은 PI가 한다.
- 변경 후 `pytest -q`를 돌린다. 테스트를 지우거나 약하게 만들지 않는다.
- 단계를 끝내면 PR 본문과 STATUS.md 맨 위에 같은 보고를 남긴다.
- 이 저장소는 public이다. 커밋 전에 `scripts/check_public.sh`를 돌리고, 비밀값은 환경변수로만 다루며, `config/labhq.yaml`은 커밋하지 않는다.
- GitHub PR에서 Codex에게 말할 때는 답글이어도 항상 @codex를 붙인다.
- `labhq/web/index.html`은 빌드 없는 단일 파일이다.
- 보고는 한국어로, 기술 용어는 영어 그대로.
