# #1388 — 삭제 범위와 보존 계약

조회용 근거. PR #1399는 draft/hold이며 #1375의 실제 통합 비교 전에는 병합하지 않는다.

## r4 통합

- main `f30179b90a3ca033dd96ad044d97d5550c677012`(#1405 포함)을 충돌 없이 반영했다. 표시·자산·각주·claim/review 수정은 보존했다.
- server의 live hook·terminal 보고서 import와 portfolio의 새 보고서 생성 fallback을 제거했다. 기존 사본 읽기는 유지한다.
- doctor/model-refusal의 삭제 모듈 stub, CI 직접 참조, Windows inventory와 status HHMM 파일명을 정리했다. CI 선택 알고리즘은 바꾸지 않았다.
- legacy bundle·직접 전달 사본·사본 없음의 3경우를 시험했다. 기존 사본의 bearer·개인 경로 제거와 새 생성기 import 금지를 확인했다.

| 검증 | 직접 결과 |
|---|---|
| 통합·시설·거절·CI 정책 | 477 passed (14.91초) |
| 관련 회귀·mock 종단·mock bench | 768 passed, 2 skipped, 90 warnings in 177.11s (0:02:57) |
| Node | PI prose 8·report markdown 10, request bundle script 통과 |
| import 추적 | Python 3.10 AST 통과; 삭제 모듈 정적 import·완전한 모듈명 문자열 0곳 |
| 기록·목록 | notes-index·Windows inventory validation 통과 |
| 제품 | 5,429줄 삭제·46줄 추가 |
| 시험 | 5,988줄 삭제·258줄 추가 |
| 도구 | 296줄 삭제 |
| README·manual 22파일 | 404,832자 → 372,546자 |

삭제량은 main 기준 `git diff --no-renames --numstat` 실측이다. 관련 회귀는 기존 `.venv`의 timeout plugin 누락을 확인해 첫 실행을 중단하고 로그를 보존한 뒤, r2와 같은 기존 Python(timeout 지원)으로 실행했다. 첫 실행은 PASS에 세지 않는다. 전체 시험은 최종 head CI로 확인한다.

## 삭제 코드 ↔ 시험 ↔ 보존 계약

제품 접두사는 `labhq/research/`, 시험 접두사는 `tests/`다. 삭제/이관은 `git diff --no-renames a4b5aaa2 1ed8764f`로 대조했다.

| 삭제 코드·동작 | 삭제·교체 시험 | 보존 계약·시험 |
|---|---|---|
| `semantics_shadow.py`: live worker·관측·breaker·설정/상태; CLI·CSO hook | **삭제** `semantics_shadow_lab.py`, `test_semantics_shadow_settings.py`, `test_semantics_shadow_worker.py`, `test_semantics_shadow_provenance.py`, `test_semantics_shadow_hash.py`, `test_semantics_shadow_breaker.py`, `test_semantics_shadow_report.py` | `test_init_defaults.py`: legacy 두 키만 비활성 수용·다른 unknown key 거부. doctor/status 일반 진단 유지. **server hook 제거·통합 회귀 통과** |
| `semantics_objects.py`: 관측 객체 뷰 | **삭제** `test_semantics_objects.py` | core input-fit·어휘/EDAM·typed output 유지. `test_semantics_output_types.py`는 순수 `Records` fixture로 교체하고 typed·unknown assertion 보존. CI의 삭제 파일 직접 읽기를 core 이관 시험으로 변경 |
| `semantics_actions.py`, `semantics_actions_run.py`: 액션 제안/실행 | **삭제** `test_semantics_actions.py`, `test_semantics_actions_shadow.py`, `test_semantics_actions_run.py` | runner/승인·계속하기는 삭제 대상 밖. `test_research_continue.py`가 라운드별 plan hash로 결과 검증. `test_input_fit_hint.py`의 prompt·오류 fallback·승인 0건 assertion 유지 |
| `semantics_request_report.py`: live 관측 보고·A/B | **삭제** `test_semantics_request_report.py`, `test_semantics_ab.py` | 순수 #136 모델·YAML·pilot·합성 fixture 보존. CP1/CP2·checklist·pack·오류 기록은 `test_research_protocol.py`, `test_research_cp2.py`, `test_semantic_devices.py`에서 유지 |
| `semantics_input_fit.py`와 `semantics.py::records_from_rows()`: live adapter/reader | **삭제·이관** `test_semantics_input_fit.py` → core 6시험 `test_input_fit.py`. **교체** `test_input_fit_boundary_796.py`의 제거 도구 의존 | 종류별 판정·규칙표·다른 vocab=unknown·agent operation 우선·EDAM 없는 판정·의미 hash. boundary는 fresh CP1/evaluator import 차단·trace 유지. `test_cp1_input_fit_796.py` correction·budget·unknown 유지 |
| `semantic_device_report.py`: 새 MD/JSON·impact 생성; bench·bundle·웹 연결 | **일부 교체** `test_semantic_device_report.py`, `test_semantic_impact_796.py`; 전용 생성/impact assertion 종료 | legacy 설정에서도 새 report 부재, checklist missing 항목·unknown 교정 오판 방지 유지. `test_request_bundle.py`에 bearer/basic·통제 경로/자료 redaction 이관. **portfolio 과거 사본 호환·공개 정리 회귀 통과** |
| `scripts/semantics_shadow_remove.py`: 제거 도구 | **삭제** `test_semantics_shadow_remove.py` | 최종 import 추적·전체 CI·mock 종단으로 확인. fresh CP1 경계 시험은 위 파일에 보존 |

`semantic_devices.py` 전체와 `labhq/vocab/`는 지우지 않는다. 어휘/EDAM·topic 점검표·pack 진단, 오류 누적과 code-only 공개 로그를 유지한다. `settings.py`의 `Any`는 replan validator도 쓴다. `labhq/runner/daemon.py`는 편집하지 않는다.


## 남은 gate·복구 근거

- 최종 head 전체 CI·같은 head 실제 리뷰 P1=0은 GitHub 결과로 확인한다. 진행/접수는 완료로 세지 않는다.
- #1375: 병합 직전 main과 세 삭제 PR 통합 가지에 같은 요청/입력으로 trial 비교. 단계·산출 목록·판정이 다르면 병합하지 않는다. trial 사용 상태 확인과 별도 조정이 남아 있다.
- 실제 모델·PI 인스턴스·HPC는 이번 r4에서 실행하거나 바꾸지 않았다.
- 복구 태그 `pre-prune-20261112` 유지. [r2 근거](https://github.com/ehojune/bioinfo-team-3d/issues/1388#issuecomment-6812128276), [r3 경로 대조](https://github.com/ehojune/bioinfo-team-3d/issues/1388#issuecomment-6813051549).
