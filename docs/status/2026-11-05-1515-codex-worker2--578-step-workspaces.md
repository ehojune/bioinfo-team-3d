## 단계

PR #578 rev2. main `24b4bb2d`를 통합하고 HPC 실행 원본을 단계별로 나눴습니다. hold를 유지합니다.

## 바뀐 것

- `hpc/<request>/steps/<step>/`에 manifest·jobs·outputs를 보관합니다. 입력은 검증 후 `data/<producer>/<source-task>/`로 복사하고 출처·hash를 기록합니다.
- wake baseline을 영속화하고 bundle의 run provenance·링크·job cwd를 고쳤습니다. 중첩 경로의 재개·verify는 요청·step·task를 대조합니다.
- 실제 worker만 HPC 선택지에 넣고 CLI `1: a/b`를 받습니다. 같은 단계의 중복·부모/자식 파일 경로 충돌을 거부합니다.
- scheduler용 환경 snapshot이 없는 binding 계획은 dispatch 전에 거부합니다. 선택·계획 검증은 요청별 WSL runner view를 따릅니다.
- 기존 프로젝트·경로 참고 입력도 snapshot이 없어 HPC 처리 전에 거부합니다. runner는 직원 project 입력까지 검사하며 local 선택은 유지합니다.
- main의 장 분리·paper 준비·optional outputs·environment binding을 보존했습니다. [상세](../reference/hpc-step-workspaces-578.md).

## 테스트 결과

- 관련 확장 993 passed/39 skipped, 후속 bundle·accounting·CSO 413/7, 경계 138/4(중복 포함).
- 리뷰 후 환경·WSL runner 회귀 401 passed/15 skipped.
- 요청 입력·환경·WSL·workspace 회귀 455 passed/33 skipped(중복 포함).
- 입력 거부 수정 후 E2E·resume accounting 39 passed.
- public·notes-index·integrations·compile·diff 검사 PASS. 최종 head CI·실제 리뷰는 PR에서 추적합니다.
- 08·11장 한영 합계 158,935→161,077자. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED입니다.

## 남은 조건

04장 한영과 `config/ci/windows-boundary-v1.json`의 후속 claim이 필요합니다. Windows inventory에 새 HPC test와 verify mixed-node를 등록해야 CI를 통과합니다. scratch 수정안의 source/AST 검증은 통과했으며 checkout policy는 편집하지 않았습니다. 설치 절 반영과 새 최종 head CI·실제 리뷰 전에는 병합하지 않습니다.

Refs #430

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
