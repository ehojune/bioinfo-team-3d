## 단계

PR #578 rev2. main `24b4bb2d`를 통합하고 HPC 실행 원본을 단계별로 나눴습니다. hold를 유지합니다.

## 바뀐 것

- `hpc/<request>/steps/<step>/`에 manifest·jobs·outputs를 보관합니다. 입력은 검증 후 `data/<producer>/<source-task>/`로 복사하고 출처·hash를 기록합니다.
- wake baseline을 영속화하고 bundle의 run provenance·링크·job cwd를 고쳤습니다. 중첩 경로의 재개·verify는 요청·step·task를 대조합니다.
- 실제 worker만 HPC 선택지에 넣고 CLI `1: a/b`를 받습니다. 같은 단계의 중복·부모/자식 파일 경로 충돌을 거부합니다.
- main의 장 분리·paper 준비·optional outputs·environment binding을 보존했습니다. [상세](../reference/hpc-step-workspaces-578.md).

## 테스트 결과

- 관련 확장 993 passed/39 skipped, 후속 bundle·accounting·CSO 413/7, 경계 138/4(중복 포함).
- public·notes-index·integrations·compile·diff 검사 PASS. 최종 head CI·실제 리뷰는 PR에서 추적합니다.
- 08·11장 한영 합계 158,935→160,636자. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED입니다.

## 남은 조건

04장 한영은 #1276 소유여서 보류했습니다. 쓰기 종료·후속 claim 뒤 설치 절을 옮기고, 새 최종 head CI·실제 리뷰까지 통과해야 병합합니다.

Refs #430

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
