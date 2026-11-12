# #1388 — 삭제 시험 대응과 통합 준비

조회용 근거. #1399는 draft/hold다. r3는 이 문서와 PR 본문만 다룬다. 제품 편집·main 반영은 다음 revision이다.

## 기준과 기존 증거

| 대상 | revision / 결과 |
|---|---|
| 공통 기준 | `a4b5aaa2ffa7c098e5c4b2cb0ac335920a6161ee` |
| r2 head | `1ed8764f92c4a9306ba0a22ee8d233411ffed19d` |
| r3 대조 main | `f849ef66832c753d6a84d63b7368467fa21f0e2b`; #1401 merge `734c15d0` 포함 |
| 복구 태그 | `pre-prune-20261112` → `88e47bf8` |
| r2 실제 checkout | 관련 회귀 742 passed·25 failed·2 skipped. 전체 수집 15,642건·Node 웹 8시험·Python 3.10 AST 통과 |
| r2 격리 진단 | server·portfolio 연결 두 곳을 정리한 사본에서 실패 25건+mock 종단·bench=27 passed. 실제 가지 통합 PASS가 아님 |
| r2 삭제 실측 | 제품 5,406줄 삭제·46줄 추가, 시험 5,985줄 삭제·222줄 추가, 도구 296줄 삭제. rename 감지 없음 |
| r2 문서 실측 | README·manual 19파일 309,559자 → 281,974자 |

위 실행은 r2 기록이며 r3에서 재실행하지 않았다. r1의 기존 제거 도구 진단은 원본 296 passed → 변환 후 229 passed·67 failed였다. 선택 시험 21건 통과만으로 삭제 완료를 판정하지 않는다.
원문·명령은 [r1/r2 보존본](https://github.com/ehojune/bioinfo-team-3d/blob/1ed8764f92c4a9306ba0a22ee8d233411ffed19d/docs/reference/prune-semantics-1388.md)과 [r2 결과](https://github.com/ehojune/bioinfo-team-3d/issues/1388#issuecomment-6812128276)에 있다.

## 삭제 코드 ↔ 시험 ↔ 보존 계약

제품 접두사는 `labhq/research/`, 시험 접두사는 `tests/`다. 삭제/이관은 `git diff --no-renames a4b5aaa2 1ed8764f`로 대조했다.

| 삭제 코드·동작 | 삭제·교체 시험 | 보존 계약·시험 |
|---|---|---|
| `semantics_shadow.py`: live worker·관측·breaker·설정/상태; CLI·CSO hook | **삭제** `semantics_shadow_lab.py`, `test_semantics_shadow_settings.py`, `test_semantics_shadow_worker.py`, `test_semantics_shadow_provenance.py`, `test_semantics_shadow_hash.py`, `test_semantics_shadow_breaker.py`, `test_semantics_shadow_report.py` | `test_init_defaults.py`: legacy 두 키만 비활성 수용·다른 unknown key 거부. doctor/status 일반 진단 유지. **server hook는 통합 대기** |
| `semantics_objects.py`: 관측 객체 뷰 | **삭제** `test_semantics_objects.py` | core input-fit·어휘/EDAM·typed output 유지. `test_semantics_output_types.py`는 순수 `Records` fixture로 교체하고 typed·unknown assertion 보존. CI의 삭제 파일 직접 읽기는 후속 수정 |
| `semantics_actions.py`, `semantics_actions_run.py`: 액션 제안/실행 | **삭제** `test_semantics_actions.py`, `test_semantics_actions_shadow.py`, `test_semantics_actions_run.py` | runner/승인·계속하기는 삭제 대상 밖. `test_research_continue.py`가 라운드별 plan hash로 결과 검증. `test_input_fit_hint.py`의 prompt·오류 fallback·승인 0건 assertion 유지 |
| `semantics_request_report.py`: live 관측 보고·A/B | **삭제** `test_semantics_request_report.py`, `test_semantics_ab.py` | 순수 #136 모델·YAML·pilot·합성 fixture 보존. CP1/CP2·checklist·pack·오류 기록은 `test_research_protocol.py`, `test_research_cp2.py`, `test_semantic_devices.py`에서 유지 |
| `semantics_input_fit.py`와 `semantics.py::records_from_rows()`: live adapter/reader | **삭제·이관** `test_semantics_input_fit.py` → core 6시험 `test_input_fit.py`. **교체** `test_input_fit_boundary_796.py`의 제거 도구 의존 | 종류별 판정·규칙표·다른 vocab=unknown·agent operation 우선·EDAM 없는 판정·의미 hash. boundary는 fresh CP1/evaluator import 차단·trace 유지. `test_cp1_input_fit_796.py` correction·budget·unknown 유지 |
| `semantic_device_report.py`: 새 MD/JSON·impact 생성; bench·bundle·웹 연결 | **일부 교체** `test_semantic_device_report.py`, `test_semantic_impact_796.py`; 전용 생성/impact assertion 종료 | legacy 설정에서도 새 report 부재, checklist missing 항목·unknown 교정 오판 방지 유지. `test_request_bundle.py`에 bearer/basic·통제 경로/자료 redaction 이관. **portfolio 과거 사본 호환·안전성 통합은 미완료** |
| `scripts/semantics_shadow_remove.py`: 제거 도구 | **삭제** `test_semantics_shadow_remove.py` | 최종 import 추적·전체 CI·mock 종단으로 확인. fresh CP1 경계 시험은 위 파일에 보존 |

`semantic_devices.py` 전체와 `labhq/vocab/`는 지우지 않는다. 어휘/EDAM·topic 점검표·pack 진단, 오류 누적과 code-only 공개 로그를 유지한다. `settings.py`의 `Any`는 replan validator도 쓴다. `labhq/runner/daemon.py`는 편집하지 않는다.

## main 대조와 정확한 통합 경로

`git merge-tree --write-tree 1ed8764f92c4a9306ba0a22ee8d233411ffed19d f849ef66832c753d6a84d63b7368467fa21f0e2b`: exit 0, 예상 tree `325ad1c9389192fae959ada24c138d99649807ee`. 검증 대상은 위 r2/main snapshot이다. branch/index/worktree는 바꾸지 않았다. 공통 기준 이후 양쪽에서 동시에 바뀐 경로는 0개다. W1 r14 종료 뒤 텍스트 충돌을 다시 확인해야 한다.

main 제품/시험 6경로: `labhq/portfolio.py`, `labhq/evidence/report_check.py`, `labhq/web/state.js`, `tests/test_portfolio_edition.py`, `tests/test_report_check.py`, `tests/web_report_markdown.cjs`. 이전 차수 review·claim ID 연결·미해결 표시·literal link 안전성 수정을 보존한다. `AGENTS.md`의 #1401 삭제 시험 예외와 한영 12장·status·patch note 기록도 merge로 가져온다.

다음 통합 claim의 추가 경로:

| 경로 | 작업·검증 |
|---|---|
| `labhq/gateway/server.py` | live hook·terminal report import/`freeze_pending` 제거. 일반 terminal·async 저장·안전성 유지 |
| `labhq/portfolio.py` | 새 report fallback만 제거. main/r14 review 표시와 과거 사본 판독·공개 정리 보존 |
| `tests/test_portfolio_edition.py` | 과거 사본 호환·redaction·새 fallback 부재. main/r14 assertion 유지 |
| `config/ci/windows-boundary-v1.json` | 삭제 시험/fixture override 정리, 이관·변경 시험 등록/digest 정합. 선택 알고리즘·coverage 유지 |
| `tests/test_model_refusal_682.py` | 243행 삭제 모듈 `status_line` stub 제거. CLI 모델 거절 assertion 유지 |
| `tests/test_ci_cohort823.py` | 465행 `test_semantics_objects.py` 직접 읽기 대상 갱신. 나머지 Windows 경계 assertion 유지 |
| `tests/test_doctor_facilities_support_983.py` | 34행 삭제된 `_semantics_row` fixture stub 정리. 시설·엔진·지원 판정 assertion 유지. Linux 각 47 errors의 직접 원인 |
| `docs/manual.en.md` | 옛 live 그림자 목차 정합 |
| `docs/manual/04-install.md` | 한국어 설치 안내의 옛 활성화 설명 정합 |
| `docs/manual/04-install.en.md` | 같은 절 영문 정합 |
| `docs/manual/17-known-limits.md` | 한국어 live 그림자·액션 안내 정합 |
| `docs/manual/17-known-limits.en.md` | 같은 절 영문 정합 |
| `docs/status/2026-11-12-codex-worker2-prune-1388.md` | **rename 원본도 claim 필요**. 기존 보고 보존 |
| `docs/status/2026-11-12-1048-codex-worker2-prune-1388.md` | rename 목적지. HHMM 파일명 계약 복구 |

추가 14경로다. r2 요청 12경로에는 rename 원본과 doctor 시설 시험이 빠졌다. 기존 reference와 `patch_notes/entries/codex-worker2--prune-1388.yaml`도 후속 증거·커밋 기록 범위에 포함해야 한다.

## CI·통합 대기

- r2 head CI [43137435498](https://github.com/ehojune/bioinfo-team-3d/actions/runs/43137435498): 성공 3·실패 6·skip 1. 실패: patch-notes, readme, clean-install, pytest 3.10/3.12, pytest-windows. 같은 head 실제 Codex 리뷰 completed·findings 없음.
- Linux 3.10/3.12 모두 **190 failed·15,310 passed·103 skipped·2 xfailed·47 errors**다. 47 errors는 위 doctor fixture의 `_semantics_row` 부재. 실패에는 삭제 report import와 bundle/storage/editor 후속 실패·bench/CP4 timeout, inventory와 삭제 시험 직접 참조가 포함된다. 모든 실패를 같은 원인으로 확정하지 않는다.
- patch-notes/readme: notes-index가 HHMM 없는 status 파일명을 거부. Windows: 새 `test_input_fit.py`의 inventory review 누락으로 시험 시작 전 거부. clean-install: `보고서와 요청 묶음: 묶음 파일 누락`. 재실행·assertion 완화는 하지 않았다.
- inventory 정합 뒤 `test_ci_windows10m.py`, `test_ci_boundary_preview.py`, `test_ci_parallel_policy.py`, `test_ci_phase2_policy.py`도 검증한다. 뒤 두 파일의 삭제 경로 오류는 inventory 열거에서 발생하므로 현재 근거만으로 이 시험 자체의 편집을 요청하지 않는다. 통합 뒤 남는 실패는 원인을 분리해 새 범위를 확정한다.
- W1 #1384 r14 finish 후 총괄이 소유권·빈 큐를 확인해 통합 revision을 배정한다. r3 main merge·제품 편집 없음. #1396 후속은 W3 #1387 종료 뒤다.
- 통합 뒤 전체 시험·최종 head CI·같은 head 실제 리뷰 P1=0·실제 가지 mock 종단·import 추적·삭제 줄 수 재측정이 필요하다.
- #1375 main/세 PR 통합 가지의 같은 입력 trial 비교와 단계·산출·판정 일치 전에는 hold를 유지한다. 실제 모델·PI 인스턴스·HPC 실행은 r3 범위 밖이다.
