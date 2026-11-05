## 단계

PR #578 rev4. 로컬 `cc7d9087`을 보존하며 main `6323fff5`(`#1281` 포함)를 충돌 없이 통합했습니다. hold를 유지합니다.

## 바뀐 것

- 원본은 `hpc/<request>/steps/<step>/`, 검증 입력은 `data/<producer>/<source-task>/`에 분리합니다.
- wake baseline·bundle provenance·중첩 경로 resume/verify·동명 산출·경로 충돌을 처리합니다.
- 요청별 runner와 실제 worker capability를 확인합니다. scheduler snapshot이 없는 기존 입력·환경 binding은 dispatch 전에 거부합니다.
- 04장에 선택·기록·단계 경로·지원 제한을 한영으로 반영했습니다. [구현·검증 근거](../reference/hpc-step-workspaces-578.md).
- 리뷰 P1 `4479233491`: snapshot 없는 저장 precedent 본문을 gateway dispatch·runner 시작 전에 거부합니다. local 선택은 유지합니다.

## 확인

- rev4 P1 수정 후 HPC·workspace·bundle·verify mock 회귀: 301 passed/25 skipped. 기존 precedent 회귀 13 passed.
- 04장 한영 합계: 54,037→56,966자(+2,929, 편집 전 기준).
- public·integrations·notes-index·diff 검사 통과. 최종 head CI·실제 리뷰는 PR에서 추적합니다.

## 남은 조건

#1281은 HPC test 등록을 포함하지만 `test_evidence_verify.py` mixed-node hash·새 node는 남았습니다. inventory 사전 검사는 stale hash로 실패했습니다. scratch 수정안의 source/AST 검증은 통과했으나 policy는 rev4의 34경로 밖이므로 편집하지 않았습니다. 후속 claim과 최종 CI·리뷰 통과가 필요합니다. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED이며 실행하지 않았습니다.

Refs #430

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
