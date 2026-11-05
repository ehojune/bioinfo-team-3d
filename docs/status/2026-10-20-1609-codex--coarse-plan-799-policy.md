# 응집형 분석 스텝 정책과 bench 계약 최종 범위 (#799)

## 결론

PR #815(draft), 정책 커밋 `7330e77`. #799의 최종 범위입니다. 공통 계획 정책에 응집형 분석 스텝 문장을 더했고, 4-arm bench에서 무엇을 셀지 정한 계약과 결정적 계획 예제 test를 두었습니다. Refs #799.

**bench·실운전·모델 실행은 하지 않았고, 속도·비용·품질 효과는 측정하지 않았습니다.**

- **근거:** [#799](https://github.com/ehojune/bioinfo-team-3d/issues/799)의 PI 10-20 외부 감사 요구(데이터 탐색·전처리·핵심 통계처럼 강하게 결합된 작업은 같은 분석 스텝에서 잇고, 이종 벤더 qa-gate/critic·CP2는 유지)와 [#760 반영 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6281989709).
- **승인:** 총괄 확인 [#760 재개·배정](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6285136614). 이 승인은 정책·준비 작업까지이며, 연구·bench 실행 승인이 아닙니다.

## 바꾼 것

- **`labhq/bioinfo_policy.py`** (`BIOINFO_ASSIGNMENT_RULE`)
  - 같은 데이터·질문의 탐색·전처리·핵심 통계는 한 분석 단계에 두고, 데이터셋·질문·근거 갈래가 다른 진짜 독립 작업만 나눈다는 문장을 더했습니다.
  - 생물학적 해석, 독립 technical·scientific QC 단계, 다른 회사 과학 리뷰, CP1·CP2 승인·근거 검사는 따로 둡니다.
  - 같은 상수라 일반·연구·연구 CP2·재계획 prompt와 continuation이 모두 받습니다. `cso.py`는 고치지 않았습니다.
  - 직원 공통 규칙에도 들어가므로 "In a plan,"으로 시작합니다.
  - nf-core 배정과 건별 기여 승인 문장은 그대로입니다.
- **`tests/test_coarse_plan_799.py`** (결정적, 9개)
  - 새 문장과 경계 문구가 모든 계획 template에 있고, nf-core·기여 승인 문구가 남는지 확인합니다.
  - 최초·재계획·continuation 계획 예제로 split 후보와 유지 경계를 셉니다.
  - continuation의 rerun 수는 **계획상 기대 dispatch**이지 실제 CLI 시작 수가 아님을 명시했습니다(리뷰 P2).
- **`docs/reference/bench-coarse-contract.md`**
  - 계획 기대값, task 기록 대리값, 실제 관측값(runner의 CLI 시작·byte)을 나눕니다.
  - 관측하지 않은 재시도·consult·CLI 시작·byte는 0이 아니라 `미수집`으로 둡니다(리뷰 P2).
  - arm별 값의 수집 위치(각 arm 폴더의 `run.json` 옆)와 기록기 미구현을 적었습니다.
  - 기존 수치(144/19·21:26 벤치 A, 22:28 벤치 C 재채점, C 원 보고 85.9분)는 출처와 함께 두고 인과 결론은 내리지 않았습니다.

## 확인 (로컬 mock/static만)

- 새 정책 test는 수정 전 문장에서 실패했고(1 failed, 8 passed), 수정 뒤 통과합니다.
- `test_coarse_plan_799`·`test_role_footer`·`test_ask_policy`·`test_step_prompt_rules`: 45 passed.
- `test_nfcore_default_732`: 14 passed. 모든 계획 경로의 nf-core 배정 test를 포함합니다. 실제 하위 프로세스를 쓰는 restart test 5개는 mock guard가 막았고 다시 돌리지 않았습니다.
- Python 3.10 문법, `check_public.sh`, `git diff --check`, `patch_notes.py check`, `notes_index.py --validate` 통과. 계약 문서 링크 깨짐 0건.
- 독립 리뷰 P1=0은 `41475fd4` 기준이며, 이 head는 그 뒤 변경입니다.

## 남은 것·한계

- **기록기 미구현:** task 기록 대리값과 runner의 실제 CLI 시작·byte를 arm 폴더에 쓰는 코드는 없습니다. bench dry-run 전 별도 작업이며, 그때까지 해당 칸은 `미수집`입니다.
- **효과 미측정:** 새 문장으로 실제 CSO 계획이 응집되는지, 속도·비용·품질이 달라지는지는 측정하지 않았습니다. 실제 arm 측정은 PI의 별도 bench 실행 승인이 필요합니다.
- **목차 링크:** `docs/reference/README.md` 목차와 manual 반영은 #801 등 다른 PR이 그 파일을 고치는 동안 미뤘습니다.
