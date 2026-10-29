## 단계
#827 PR 1: 설정·인덱스·`labhq wiki`·doctor 행(설계안 [#827 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/827#issuecomment-6504480814)).

## 한 일
1. `labhq/settings.py`: `WikiSettings`(`wiki:`, 기본 꺼짐, `external_model_allowed` 기본 false, 확장자 `.md`·`.txt`, 상한, `stale_days`).
2. `labhq/wiki/walker.py`: 루트 검사(링크 불가, `reference_roots` 안, 통제 구역·private paths·workspace·state와 겹치지 않음, 기존 `scan_reference_dir`) 후 링크·mount·숨김 폴더·credential 이름·큰 파일을 건너뛰며 색인 대상 나열.
3. `labhq/wiki/index.py`: runner `state_dir/wiki/index.sqlite3`. (size, mtime, 파일 ID)가 바뀐 문서만 다시 읽고, 같은 hash면 시각만 갱신, 사라진 문서는 삭제. 조각(제목 경로·줄)·출처(DOI/PMID/URL)·FTS5 trigram(2자는 LIKE). 검색 결과에 상대 경로·줄·hash·수정 시각·출처·`access_level: wiki_summary`·`verified_against_primary: false`·freshness(ok/stale/source_missing/disputed).
4. `labhq wiki connect|status|reindex|search|disconnect`(이 PC 로컬, gateway 불필요)와 doctor `wiki` 행. README 한·영 한 줄.

## 테스트 결과
- Windows `tests/test_wiki_index.py` 17 passed·2 skipped(POSIX 링크), WSL Ubuntu-22.04 19 passed. 관련 test(`test_init_defaults`·`test_doctor`·`test_init_wizard`·`test_cli_launch`·`test_integrations`) 중 `test_doctor_warns_about_parent_session_markers_and_pi_skills` 1건이 이 PC(Claude Code 환경변수)에서 실패했고 이 변경과 무관해 보이나 main에서 직접 대조하지는 않았다.
- 미검증: 실제 사용자 wiki(내용·크기·형식), Windows ctime 한계(기존 입력 hash 캐시와 같음), 새 내장 MCP 부착(2단계).
- 범위에서 뺀 것: `config/labhq.example.yaml` 주석 블록(저장소와 packaged 사본이 같아야 해서 `config/` claim 없이 못 고침, PR 4). 이후 봇 지적에 따라 manual 한·영의 1단계 범위 절과 Windows inventory 등록(`config/ci`)을 이 PR에 넣었다.

## 바꾼 파일
labhq/settings.py, labhq/cli.py, labhq/doctor.py, labhq/wiki/(신규 3개), tests/test_wiki_index.py, tests/fixtures/wiki/(공개 fixture), README.md, README.en.md, docs/manual.md, docs/manual.en.md(`내 LLM-wiki 연결` 절), config/ci/windows-boundary-v1.json(시험 등록), 패치노트·이 파일.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
