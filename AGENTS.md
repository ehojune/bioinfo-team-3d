# 이 저장소에서 작업하는 에이전트에게

시작 전에 `HANDOFF.md`·`STATUS.md` 상단과 현재 PI 창구 [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760)의 최근 댓글을 읽는다.

- 개발 총괄은 **Codex 고정**이며 Codex·Claude subagent에 작업을 위임한다(PI 2026-10-19). 제품 안의 연구 CSO·직원 설정과 구분한다. 작업 순서와 PI 확인은 HANDOFF를 따른다.
- 문서는 README 한영판(처음 쓰는 사람), `docs/manual.md`(상세), `docs/pi-qa.md`(PI), AGENTS·HANDOFF(개발)로 나눈다. 동작 변경은 manual에 반영하고 **큰 변경·매 PI 보고 전** 코드·직원 YAML·PR과 대조한다. 상세 근거·옛 결정은 `docs/reference/`에 두고 링크한다.
- 브랜치에서 작업하고 PR을 연다. main 직접 push 금지. 병합은 Codex 총괄이 판단하며 CI 통과가 조건이다. 자동 병합 workflow는 꺼져 있다.
- 관련 test만 로컬에서 돌리고 전체 suite는 CI(3.10·3.12·Windows)에 맡긴다. test를 삭제·약화하지 않는다. `tests/conftest.py`의 홈·캐시·state 격리를 지키고 사용자 `.labhq` 접근 guard를 우회하지 않는다.
- 단계 끝에는 PR 본문과 `docs/status/<YYYY-MM-DD>-<HHMM>-<branch>.md`에 같은 보고를 쓴다. `STATUS.md`는 main 자동 생성이므로 직접 고치지 않는다.
- 기록만 바꾼 커밋을 빼고 커밋마다 `patch_notes/entries/<branch>.yaml`에 한 줄. 생성 `patch_notes/README.md`는 직접 고치지 않는다. main 3커밋 안에 README나 manual을 갱신한다. 초안은 `python scripts/patch_notes.py rows --pr N`, 검사는 `python scripts/patch_notes.py check`.
- public 저장소다. 커밋 전 `scripts/check_public.sh`를 실행한다. 비밀값은 env로만, 실제 `config/labhq.yaml`·로컬 노트는 커밋 금지. DUA 원본은 클러스터 밖·LLM 대화·프롬프트·로그에 가져오지 않는다. 경로 가드는 sandbox가 아니다.
- 기본은 리뷰 1회 → 실제 P1 수정 1회 → CI → 병합이다. P1이 남으면 병합을 보류한다. 보안 등 큰 수정만 재리뷰 예외로 두고 실행 중에는 재호출하지 않는다. P2는 PR “남은 지적”에 한 줄, issue는 만들지 않는다. 인라인·PR 본문·커밋에는 봇 멘션을 쓰지 않는다. `scripts/pr_gate.py --dry-run`은 보조 도구다.
- `labhq/web/`은 빌드 없는 정적 파일이며 2.5D·3D가 `state.js` reducer를 공유한다. 보고는 한국어, 기술 용어는 영어 그대로 쓴다.
