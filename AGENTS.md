# 이 저장소에서 작업하는 에이전트에게

시작 전에 `HANDOFF.md`·`STATUS.md` 상단과 현재 PI 창구 [#883](https://github.com/ehojune/bioinfo-team-3d/issues/883)의 최근 댓글을 읽는다.

역할 복귀·중단·위임·병합 책임은 [현재 운영 기준](docs/reference/lead-operations.md#현재-역할과-중단)을 먼저 따른다. 과거 보고 주기·고정 모델/worker 수를 자동 복원하지 않는다.

- 개발 담당과 위임 범위는 최신 PI 인계를 따른다. 제품 안의 연구 CSO·직원 설정과 구분한다. 현재 우선순위는 HANDOFF, 점검 주기·동시 투입·한도·P2 선별은 [운영 기준](docs/reference/lead-operations.md)을 따른다.
- 문서는 README 한영판(처음 쓰는 사람), `docs/manual.md`(상세), `docs/pi-qa.md`(PI), AGENTS·HANDOFF(개발)로 나눈다. 동작 변경은 manual에 반영하고(영문판 `docs/manual.en.md`·`research_protocol.en.md`·`pi-qa.en.md`·`vocabulary.en.md`는 한국어 문서의 번역이므로 같은 PR에서 같은 절을 고친다. `vocabulary.en.md`는 생성 파일이 아니라 손으로 옮긴 사본이다) **큰 변경·매 PI 보고 전** 코드·직원 YAML·PR과 대조한다. 병합 전 PR의 동작은 manual·README에 출시된 것으로 쓰지 않는다. 상세 근거·옛 결정은 `docs/reference/`에 두고 링크한다.
- 브랜치에서 작업하고 PR을 연다. main 직접 push 금지. 병합 판단은 최신 PI 인계를 따르며 CI 통과가 조건이다. 자동 병합 workflow는 꺼져 있다.
- 관련 test만 로컬에서 돌리고 전체 suite는 CI(3.10·3.12·Windows)에 맡긴다. test를 삭제·약화하지 않는다. `tests/conftest.py`의 홈·캐시·state 격리를 지키고 사용자 `.labhq` 접근 guard를 우회하지 않는다.
- CI의 Linux 전수/Windows 경계·nightly full profile과 worker HOME/port 격리는 [CI 정책](docs/reference/ci-reuse.md)을 따릅니다. 로컬 worker/process 실행은 별도 허가 없이 하지 않습니다.
- CI 축소는 예제·보호 markup이 그대로인 prose와 정해진 기록, main 재사용은 같은 tree·workflow/policy·전체 gate 증거에 한정한다([정책](docs/reference/ci-reuse.md)). 불명확하면 full이며 assertions·안전·설치·validator·MCP gate를 줄이지 않는다.
- 단계 끝에는 PR 본문과 `docs/status/<YYYY-MM-DD>-<HHMM>-<branch>.md`에 같은 보고를 쓴다. `STATUS.md`는 main 자동 생성이므로 직접 고치지 않는다.
- 기록만 바꾼 커밋을 빼고 커밋마다 `patch_notes/entries/<branch>.yaml`에 한 줄(branch의 `/`는 `--`). 생성 `patch_notes/README.md`는 직접 고치지 않는다. main 3커밋 안에 README나 manual을 갱신한다. 초안은 `python scripts/patch_notes.py rows --pr N`, 검사는 `python scripts/patch_notes.py check`.
- 패치노트는 `summary`에 처음 읽는 사람을 위한 쉬운 한 문장, `text`에 구체적인 변경을 쓴다. 약어·파일명은 두 번째 설명에 두고 실제 실행·성능을 확인한 범위를 넘겨 말하지 않는다. 예시는 [작성 기준](patch_notes/STYLE.md)을 따른다.
- 공동 작성자 trailer는 `.githooks/commit-msg`가 GitHub이 인식하는 봇 주소로 맞춘다(Codex 이메일 치환, Gemini 추가). 새 clone은 `git config core.hooksPath .githooks` 한 번.
  머신 전역 훅(`~/.git-template/hooks`)이 있으면 새 커밋·revert를 넘겨 author를 에이전트의 GitHub App 봇으로 맞춘다. rebase·cherry-pick·amend와 명시한 다른 author는 보존한다. merge 커밋·reflog를 끈 저장소는 자체 trailer 규칙만 쓴다.
- public 저장소다. 커밋 전 `scripts/check_public.sh`를 실행한다. 비밀값은 env로만, 실제 `config/labhq.yaml`·로컬 노트는 커밋 금지. DUA 원본은 클러스터 밖·LLM 대화·프롬프트·로그에 가져오지 않는다. 경로 가드는 sandbox가 아니다.
- P1은 같은 담당자가 고치고 새 head 리뷰를 받아 **P1=0까지 반복**한다. 남은 P2는 총괄이 선별한 것만 issue로 남기고 경미·추정·중복은 원 PR 댓글에 둔다. CI·봇 대기는 driver가 맡는다. 실행 중 재호출과 인라인·PR 본문·커밋의 봇 멘션은 금지한다. `scripts/pr_gate.py --dry-run`은 보조 도구다.
- `labhq/web/`은 빌드 없는 정적 파일이며 2.5D·3D가 `state.js` reducer를 공유한다. 보고는 한국어, 기술 용어는 영어 그대로 쓴다.
- `docs/manual.md`는 `labhq`를 깊이 공부하고 운영하는 사용자를 위한 문서다. 개발 에이전트 전용 규칙(`pytest` 홈·`.labhq` 격리, [Ubuntu 3.10 CI 진단·대기 상한](docs/status/2026-10-20-0955-ci-stall-786.md), `signatures.yaml`·`topic_checklists.yaml` 수정 시 fixture와 가짜 실행 test 추가 규칙)은 매뉴얼에 섞지 않고 이 문서에서 따른다.
