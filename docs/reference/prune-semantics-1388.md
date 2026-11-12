# #1388 — r2 독립 구현

조회용 근거. 기준 `a4b5aaa2`. 삭제안은 draft/hold이며 출시·병합하지 않았다.

- core input-fit 6시험 이관, fresh CP1 import 차단·typed-output·continuation round binding 보존.
- 어휘·EDAM·점검표·pack 진단과 code-only 오류 로그·CP1/CP2 경고를 보존했다.
- live 그림자·객체 뷰·액션·A/B·새 장치 보고서 생성과 CLI 연결을 소유 경로에서 제거했다.
- legacy 두 키만 무시한다. 직접 `model_validate`, 생성자, 파일 load와 다른 unknown key 거부를 시험한다.
- 실제 checkout 관련 회귀: 742 통과·25 실패·2 skip. 격리 사본에서 server·portfolio의 남은 두 연결만 정리하면 실패 25건과 mock 종단·bench가 27건 통과했다. 실제 가지 통합 PASS는 아니다.
- 전체 수집 15,642건, Node 웹 8시험, Python 3.10 AST 통과.
- 제품 5,406줄 삭제·46줄 추가, 시험 5,985줄 삭제·222줄 추가, 도구 296줄 삭제(rename 감지 없음).
- README·매뉴얼 19파일: 309,559자 → 281,974자. 삭제안이 아직 검증 중임을 명시했다.

## 다음 claim에서 통합할 경로

| 경로 | 남은 작업 |
|---|---|
| `labhq/gateway/server.py` | shadow hook와 terminal의 report import·freeze 연결 제거 |
| `labhq/portfolio.py` | 새 report fallback 생성만 제거하고 과거 보고서 사본 판독 보존 |
| `tests/test_portfolio_edition.py` | 과거 사본 호환·공개 안전성 통합 회귀 |
| `config/ci/windows-boundary-v1.json` | 삭제 시험 정리와 이관 시험 등록. CI 선택 규칙은 보존 |
| `docs/status/2026-11-12-1048-codex-worker2-prune-1388.md` | 배정된 옛 파일명에 HHMM이 없어 CI 실패. 새 이름으로 옮길 claim 필요 |
| `docs/manual.en.md` | 영문 목차의 옛 live 그림자 안내 정합 |
| `tests/test_model_refusal_682.py` | 삭제된 `status_line` stub 정리. CLI 모델 거절 assertion은 보존 |
| `docs/manual/04-install.md`, `04-install.en.md` | W3 소유 설치 안내의 옛 활성화 설명 정합 |
| `docs/manual/17-known-limits.md`, `17-known-limits.en.md` | 제거될 live 그림자·액션 안내 정합 |

실제 checkout의 미소유 경로는 편집하지 않았다. 최종 전체 CI·같은 head 실제 리뷰 P1=0·mock 종단·#1375 통합 trial 비교 전에는 hold를 풀지 않는다. HPC·PI 인스턴스·실제 모델 실행은 하지 않았다.

## r1 조사 원문

# #1388 삭제 조사 — r1

조회용 기록. 기준: `a4b5aaa2ffa7c098e5c4b2cb0ac335920a6161ee`.
복구 태그 `pre-prune-20261112`는 `88e47bf8`을 가리킨다.
제품은 편집하지 않았다. 아래 경로는 다음 claim 후보이며 현재 쓰기 권한이 아니다.

## 결론과 검증

`semantics_shadow_remove.py --check`만으로 삭제 완료를 판정하면 안 된다.
스크립트 밖의 init·장치 보고서 연결을 제거하고 공용 진단과 고유 시험을 보존해야 한다.

| 실행 | 결과 |
|---|---|
| `python -X utf8 scripts/semantics_shadow_remove.py --check` | 격리 임시 사본: compile 통과, 15,650건 수집, 선택 시험 21건 통과 |
| 원본의 관련 시험 9파일 | 260 passed, 70 warnings, 46.25초 |
| 동일 사본 구성·시험 명령에 기존 삭제 변환 적용 | 58 failed, 202 passed, 70 warnings, 26.98초 |
| 추가 2파일: 원본 / 변환 후 | 36 passed(7.98초) / 9 failed, 27 passed(7.88초). 합계 원본 296 통과, 변환 후 229 통과·67 실패 |
| AST import 추적 | 변환 후 남은 제품에서 삭제 모듈의 정적 import 0곳 |
| 미검증 | 전체 시험 실행, 삭제 후 상태 복구, 장치 보고서까지 제거한 mock 종단, 최종 CI·리뷰·실제 trial 비교 |

9파일: `test_doctor.py`, `test_ops_status_usage.py`, `test_research_protocol.py`,
`test_init_defaults.py`, `test_semantic_devices.py`, `test_semantic_device_report.py`,
`test_semantic_impact_796.py`, `test_cp1_input_fit_796.py`, `test_input_fit_hint.py`(모두 `tests/`).
명령: `python -X utf8 -m pytest -q -p no:cacheprovider --tb=short --basetemp=<격리 임시 경로> <9파일>`.
두 실행 모두 같은 COPY 목록을 사용했고 test HOME·cache·state 격리를 유지했다.
추가 파일은 `tests/test_semantics_output_types.py`, `tests/test_research_continue.py`다.

| 변환 뒤 실패 | 건수 | 원인·처리 |
|---|---:|---|
| doctor / CLI status / research protocol | 4 / 1 / 2 | 함수 본문의 삭제 모듈 import·옛 `_semantics_row` stub. 해당 기능 시험만 교체하고 일반 doctor·CP1/CP2 assertion 유지 |
| init defaults | 26 | `init_defaults.REFERENCE_BEHAVIOUR`와 예시 YAML이 옛 키를 생성한다. `Settings.load()`에서만 키를 거르는 것으로 `model_validate()` 경로를 보호하지 못함 |
| core device diagnostics | 3 | fixture의 `settings.semantics = ...`. fixture를 고치고 어휘·점검표·pack 오류·비공개 본문 차단 assertion 유지 |
| device report / impact | 17 / 4 | 삭제된 설정을 쓰는 fixture. 보고서 전용 시험과 core 판정 시험을 먼저 분리 |
| #1352 hint | 1 | `settings.semantics` 직접 조회. 모듈 부재 확인으로 바꾸고 실제 runner prompt·승인 0건 assertion 유지 |
| typed-output / continuation | 8 / 1 | 남은 모델에 삭제된 `records_from_rows()`를 호출함. 타입 판정·라운드별 plan binding assertion 보존 후 fixture/reader 경계 교체 |

## 기존 변환의 정확한 범위

다음 제품 6파일을 삭제한다(4,363줄).

```text
labhq/research/semantics_shadow.py
labhq/research/semantics_request_report.py
labhq/research/semantics_objects.py
labhq/research/semantics_input_fit.py
labhq/research/semantics_actions.py
labhq/research/semantics_actions_run.py
```

표시된 hook 56줄과 block 2개를 아래 7파일에서 제거한다.
실측 diff는 제품 삭제 201줄·추가 2줄이다.

```text
labhq/cli.py
labhq/doctor.py
labhq/gateway/server.py
labhq/orchestrator/cso.py
labhq/research/semantic_devices.py
labhq/research/semantics.py
labhq/settings.py
```

아래 시험·helper 16파일(5,479줄)과 제거 스크립트(295줄)도 삭제 대상으로 잡는다.
마지막 두 시험 파일은 먼저 core assertion을 옮기거나 다시 작성해야 한다.

```text
tests/semantics_shadow_lab.py
tests/test_semantics_shadow_settings.py
tests/test_semantics_shadow_worker.py
tests/test_semantics_shadow_provenance.py
tests/test_semantics_objects.py
tests/test_semantics_shadow_hash.py
tests/test_semantics_shadow_breaker.py
tests/test_semantics_shadow_report.py
tests/test_semantics_request_report.py
tests/test_semantics_ab.py
tests/test_semantics_shadow_remove.py
tests/test_semantics_actions.py
tests/test_semantics_actions_shadow.py
tests/test_semantics_actions_run.py
tests/test_semantics_input_fit.py
tests/test_input_fit_boundary_796.py
scripts/semantics_shadow_remove.py
```

따라서 **기존 변환만** 제품 4,564줄 삭제·2줄 추가, 시험 5,479줄 삭제다.
최종 구현 수치가 아니다. core 시험 이관과 추가 연결 제거 후 git diff로 다시 센다.

## 스크립트 밖의 연결과 경계

| 경로·기준 줄 | 판단 |
|---|---|
| `research/semantic_device_report.py:16,433,579` | 679줄의 별도 삭제 후보. bundle·portfolio·bench·CP2·terminal observation 연결도 함께 제거 |
| `bench.py:29,434,783` | report import·새 impact 계산·표시 제거. 다른 bench 점수·기존 결과 판독 유지 |
| `request_bundle.py:857,879,970` | 새 MD/JSON 생성·README 설명 제거. token/경로 redaction·통제 자료 차단·atomic 저장 유지 |
| `portfolio.py:2579` | 새 report fallback 생성 제거. 저장된 과거 report를 복사하는 호환 경로는 유지 권고 |
| `gateway/server.py:1688` | marked 호출 삭제 후에도 남는 report import·`freeze_pending` 배선 제거 |
| `orchestrator/cso.py:7931` | `__import__(f"labhq.research.{semantic_devices.DEVICE}_shadow", ...)`는 표시 block 안이므로 변환으로 제거됨 |
| `research/semantic_devices.py:43,82,171` | 파일 전체 삭제 금지. shadow 상태·advisory 분기만 제거. 어휘/EDAM·점검표·pack 진단, 오류 누적·안전 로그 유지 |
| `doctor.py:124`, `cso.py:3274,7288,8867` | core 진단을 실제로 호출한다. `_semantic_devices_check`, 오류 보존, CP1/CP2·실행 기록의 core 경고 유지 |
| `web/state.js:838,1175`, `web/ui/messenger.js:32` | core 오류 event·재연결 표시에도 쓰인다. 이벤트 전부 삭제 금지, 우선 그대로 유지 |
| `web/ui/decide.js:445,516` | 새 report label·다운로드 분기 제거. 일반 approval·evidence 렌더링 유지 |
| `init_defaults.py:21`, `init_wizard.py:633` | 새 설정의 semantics 키·기능 안내 제거. 작업 폴더 data zone·다른 기본값 보존 |
| `settings.py:538` | `Any`는 replan validator도 사용한다. import의 hook 표식을 제거하고 import 자체는 보존 |
| `config/ci/windows-boundary-v1.json` | 삭제 파일 목록·전용 fixture override 정리 필요. CI 선택 알고리즘 변경은 제외 |
| `scripts/repeat_fake_fixture.py:77` | 삭제된 field에 쓰는 설정 대입 제거. 실제 실행은 하지 않음 |
| `tests/test_semantics_output_types.py:20,70`, `test_research_continue.py:211` | import는 살아 있어도 삭제되는 `semantics.records_from_rows()`를 호출한다. core typed-output·계속하기 판정은 보존하고 fixture/reader 경계만 교체 |

위 표에서 생략한 `labhq/` 접두사는 모두 제품 경로다.
`semantics.py`의 순수 #136 모델·YAML·pilot·합성 fixture는 이번 삭제 목록 밖이다.
`scripts/semantics_pilot.py`의 동적 import도 이 보존 모델과 합성 baseline만 가리킨다.
오프라인 모델까지 삭제하는 범위로 넓히지 않는다.

## 보존할 계약과 시험

| 보존 계약 | 파일·처리 |
|---|---|
| core input-fit 6시험 | `test_semantics_input_fit.py`의 종류별 판정, 검토된 관계표, 다른 vocab 판본=unknown, agent operation 우선순위, EDAM 없는 로컬 판정, 의미 hash 시험을 새 `tests/test_input_fit.py`로 옮김. adapter/observer 전용 시험만 종료 |
| CP1 교정·예산·unknown | `tests/test_cp1_input_fit_796.py` 유지. `test_input_fit_boundary_796.py`는 제거 스크립트 의존을 없애고 삭제 모듈 import 금지·fresh evaluator·CP1 trace assertion 유지 |
| #1352 hint | `labhq/research/input_fit_hint.py`, `input_fit.py`, `input_fit.yaml`, `runner/daemon.py:2630`의 호출 보존. `tests/test_input_fit_hint.py`의 확장자/헤더·비차단·오류 fallback·runner prompt 시험 유지 |
| 어휘·선언·분야 점검표 | `labhq/vocab/` 전체와 output types/declaration 시험·`tests/test_checklist_review.py` 유지. checklist snapshot·교정은 CP1/리뷰 기록에서 검증 |
| core 진단·저장 복구 | `test_semantic_devices.py`, `test_research_protocol.py`, `test_resume_cp1_654.py`의 구조화 오류·snapshot 보존·오류 누적·본문 비공개 assertion 유지 |
| 보고서 공통 안전성 | report 전용 redaction 시험을 없애기 전 `test_request_bundle.py`·`test_portfolio_edition.py`의 공개 정리 경로에 bearer/basic·절대 경로·통제 데이터 차단 계약을 옮겨 같은 입력으로 확인 |
| 옛 설정 호환 | top-level `semantics`, nested `research.semantic_report`만 비활성 legacy로 수용. load·직접 model_validate·init 경로에서 확인하고 다른 unknown key는 계속 거부 |

삭제 기능 시험을 종료하는 것과 살아남는 core assertion 이관을 별도로 diff에서 확인한다.
새 report·장치 기여 표가 없어져도 CP1/리뷰의 근거·분모·unknown을 성공으로 바꾸지 않는다.

## 다음 claim 경로

기존 변환 범위에 위 보존 시험 이관을 더한다. 추가 경로는 다음과 같다.
아래는 수정 허가 요청용 목록이며 r1에서는 편집하지 않았다.

```text
labhq/research/semantic_device_report.py
labhq/bench.py
labhq/request_bundle.py
labhq/portfolio.py
labhq/init_defaults.py
labhq/init_wizard.py
labhq/web/ui/decide.js
config/labhq.example.yaml
labhq/config/labhq.example.yaml
config/ci/windows-boundary-v1.json
scripts/repeat_fake_fixture.py
tests/conftest.py
tests/test_input_fit.py
tests/test_cp1_input_fit_796.py
tests/test_checklist_review.py
tests/test_input_fit_hint.py
tests/test_doctor.py
tests/test_ops_status_usage.py
tests/test_init_defaults.py
tests/test_init_wizard.py
tests/test_research_protocol.py
tests/test_research_continue.py
tests/test_research_cp2.py
tests/test_semantics_output_types.py
tests/test_semantic_devices.py
tests/test_semantic_device_report.py
tests/test_semantic_impact_796.py
tests/test_portfolio_branch_folders.py
tests/test_request_bundle.py
tests/test_portfolio_edition.py
tests/web_pi_text.cjs
README.md
README.en.md
docs/manual.md
docs/manual/02-concepts.md
docs/manual/02-concepts.en.md
docs/manual/04-install.md
docs/manual/04-install.en.md
docs/manual/07-cli.md
docs/manual/07-cli.en.md
docs/manual/08-structure.md
docs/manual/08-structure.en.md
docs/manual/14-semantic-model.md
docs/manual/14-semantic-model.en.md
docs/manual/15-config.md
docs/manual/15-config.en.md
docs/manual/19-roadmap.md
docs/manual/19-roadmap.en.md
docs/manual/22-file-layout.md
docs/manual/22-file-layout.en.md
docs/pi-qa.md
docs/pi-qa.en.md
docs/reference/prune-semantics-1388.md
docs/status/2026-11-12-codex-worker2-prune-1388.md
patch_notes/entries/codex-worker2--prune-1388.yaml
```

소유권 대기: W1 `server.py`·inventory·보고 경로와 W3 `daemon.py`를 보존한다.
daemon 수정은 필요 없다. `cso.py`는 A→D15 뒤, `settings.py`는 C→B 뒤에 적용한다.
독립 후보는 삭제 전용 모듈·시험 정리와 core input-fit 시험 이관이다.
최종 PR은 hold로 열고 전체 CI·같은 head 리뷰 P1=0·mock 종단과 #1375 통합 trial 비교까지 추적한다.
