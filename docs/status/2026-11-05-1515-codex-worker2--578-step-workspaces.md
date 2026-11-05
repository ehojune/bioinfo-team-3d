## 단계

PR #578 rev5. main `6323fff5`와 기존 수정은 보존하며 inventory P1을 수정했습니다. hold를 유지합니다.

## 바뀐 것

- 원본은 `hpc/<request>/steps/<step>/`, 검증 입력은 `data/<producer>/<source-task>/`에 분리합니다.
- wake baseline·bundle provenance·중첩 경로 resume/verify·동명 산출·경로 충돌을 처리합니다.
- 요청별 runner와 실제 worker capability를 확인합니다. scheduler snapshot이 없는 기존 입력·환경 binding은 dispatch 전에 거부합니다.
- 04장에 선택·기록·단계 경로·지원 제한을 한영으로 반영했습니다. [구현·검증 근거](../reference/hpc-step-workspaces-578.md).
- 리뷰 P1 `4479233491`: snapshot 없는 저장 precedent 본문을 gateway dispatch·runner 시작 전에 거부합니다. local 선택은 유지합니다.
- inventory P1 `4479191971`: verify mixed-module hash와 root 이동 후 task identity node를 갱신했습니다. 다른 policy 필드는 같습니다.

## 확인

- rev4 P1 수정 후 HPC·workspace·bundle·verify mock 회귀: 301 passed/25 skipped. 기존 precedent 회귀 13 passed.
- rev5 source/AST `validate_profile()` 통과. CI inventory·선택 정책·verify 관련 시험 320 passed.
- 04장 한영 합계: 54,037→56,966자(+2,929, 편집 전 기준).
- public·integrations·notes-index·diff 검사 통과. 최종 head CI·실제 리뷰는 PR에서 추적합니다.

## 남은 조건

rev4 CI는 stale inventory 계약으로 실패했고 재실행하지 않았습니다. rev5 새 head의 CI·실제 리뷰가 남았습니다. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED이며 실행하지 않았습니다.

Refs #430

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
