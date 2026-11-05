## 단계

PR #578 rev3. 단계별 HPC 원본·입력·기록 보존 구현과 04장 한영을 반영했습니다. hold를 유지합니다.

## 바뀐 것

- 원본은 `hpc/<request>/steps/<step>/`, 검증 입력은 `data/<producer>/<source-task>/`에 분리합니다.
- wake baseline·bundle provenance·중첩 경로 resume/verify·동명 산출·경로 충돌을 처리합니다.
- 요청별 runner와 실제 worker capability를 확인합니다. scheduler snapshot이 없는 기존 입력·환경 binding은 dispatch 전에 거부합니다.
- 04장에 선택·기록·단계 경로·지원 제한을 한영으로 반영했습니다. [구현·검증 근거](../reference/hpc-step-workspaces-578.md).

## 확인

- rev3 관련 HPC·workspace·bundle·verify mock 회귀: 298 passed/25 skipped.
- 04장 한영 합계: 54,037→56,966자(+2,929, 편집 전 기준).
- 이전 head `ff2408b6` 실제 Codex 리뷰 무지적. CI inventory 실패는 별도 #1280이 담당합니다.

## 남은 조건

#1280 병합 뒤 main 통합·inventory 검사·최종 head CI·실제 리뷰가 필요합니다. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED이며 실행하지 않았습니다.

Refs #430

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
