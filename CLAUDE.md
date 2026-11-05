# 이 저장소에서 작업하는 에이전트에게

시작 전에 `HANDOFF.md`와 `STATUS.md`를 읽는다.

[현재 역할과 중단](docs/reference/lead-operations.md#현재-역할과-중단)을 확인한다. Claude 전체 총괄 복귀는 실제 기동·개발 재개와 별개이며, 옛 자동화·한도를 복원하는 지시가 아니다.

- 문서는 읽는 사람별로 나뉜다: `README.md`(처음 써 보는 사람), `docs/manual.md`(깊이 보려는 사람), `docs/pi-qa.md`(PI), 이 파일·`AGENTS.md`·`HANDOFF.md`(개발). 동작을 바꾸면 manual의 해당 절을 고친다.
- 작업 순서는 HANDOFF.md, 개발 담당·위임·점검·한도는 [운영 기준](docs/reference/lead-operations.md)과 [현재 창구 #883](https://github.com/ehojune/bioinfo-team-3d/issues/883)의 최신 PI 인계를 따른다. 병합 전 PR의 동작은 manual·README에 출시된 것으로 쓰지 않는다.
- 단계마다 브랜치에서 작업하고 PR을 연다. main에 직접 push하지 않는다. 병합 판단은 최신 PI 인계를 따르며 CI 통과가 조건이다. 자동 병합 워크플로는 껐다.
- 바꾼 곳의 관련 test만 로컬에서 돌리고 전체 suite는 CI에 맡긴다. test를 지우거나 약하게 만들지 않는다.
- 단계를 끝내면 PR 본문과 `docs/status/<YYYY-MM-DD>-<HHMM>-<branch>.md`에 같은 보고를 남긴다. `STATUS.md`는 main에서 자동 생성되므로 직접 고치지 않는다.
- 커밋마다 `patch_notes/entries/<branch>.yaml`에 한 줄을 남긴다(기록만 고친 커밋은 제외, branch의 `/`는 `--`). `patch_notes/README.md`는 main에서 자동 생성되므로 직접 고치지 않는다. main 커밋 3개 안에 README나 `docs/manual.md`를 한 번은 갱신한다(바뀐 파일이 전부 `tests/`·`docs/status/`·`patch_notes/entries/`·`config/ci/`·`.github/`·`scripts/ci_*`인 커밋은 세지 않는다). 행 초안은 `python scripts/patch_notes.py rows --pr N`, 검사는 `python scripts/patch_notes.py check`(CI의 `patch-notes` job).
- 공동 작성자 trailer는 `.githooks/commit-msg`가 GitHub이 인식하는 봇 주소로 맞춘다(Codex 이메일 치환, Gemini 추가). git이 마지막 문단에서 trailer로 인식하는 줄만 보며(본문의 예시는 글로 두고), 모델명은 환경변수 `LABHQ_COMMIT_MODEL`이 있을 때만 이름에 채운다(없으면 추정하지 않는다). 새 clone은 `git config core.hooksPath .githooks` 한 번.
  머신 전역 훅(`~/.git-template/hooks`)이 있으면 새 커밋·revert를 넘겨 author를 에이전트의 GitHub App 봇으로 맞춘다. rebase·cherry-pick·amend와 명시한 다른 author는 보존한다. merge 커밋·reflog를 끈 저장소는 자체 trailer 규칙만 쓴다.
- 이 저장소는 public이다. 커밋 전에 `scripts/check_public.sh`를 돌리고, 비밀값은 환경변수로만 다루며, `config/labhq.yaml`은 커밋하지 않는다.
- 통제접근(DUA) 데이터의 원본은 클러스터 밖이나 LLM 대화·프롬프트·로그로 가져오지 않는다. 경로 가드는 sandbox가 아니므로 코드 가드만 믿지 않는다.
- 리뷰는 [AGENTS.md](AGENTS.md)를 따른다. 같은 담당자가 P1을 고치고 새 head 리뷰를 받아 P1=0까지 반복한다. P2는 AGENTS의 기준으로 총괄이 선별한 것만 issue로 남긴다. 경미·추정·중복은 원 PR 댓글에 남기고 새 issue를 만들지 않는다. CI·봇 대기는 driver에 넘기며 실행 중 재호출하지 않는다. 인라인·PR 본문·커밋에는 봇 멘션을 쓰지 않는다.
- `labhq/web/`은 빌드 없는 정적 파일이다. 2.5D와 3D가 `state.js` reducer를 공유한다.
- 보고는 한국어로, 기술 용어는 영어 그대로.
