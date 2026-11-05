## 단계
PR #820 최신 main 통합

## 한 일
- main `8151f23`을 명시적으로 병합했습니다. 충돌은 HANDOFF 한 곳이며 기존 문서 정리와 main의 queued-halt 검증 근거를 모두 보존했습니다.
- 병합된 #818·#819를 큐에서 빼고 열린 #793·#821을 남겼습니다. 매뉴얼의 #784 미출시 표식도 제거했습니다. 구현·검사와 실제 운영·과학 효용의 구분은 유지합니다.
- main의 코드·테스트와 생성 목차는 그대로 보존했습니다. 새 기록은 원본에 남기며 목차는 병합 뒤 main workflow가 갱신합니다.
- 세 문서의 LF 정규화 길이는 기존 head 179,685→통합 후 180,420자입니다. main의 새 설명을 포함한 수치이며 최신 main 180,815자보다 395자 짧습니다.

## 검사
- 충돌 잔여·큐·병합 ancestry·상대 링크·공개 경로/비밀값 변경분·소유 범위·`git diff --check` 통과.
- patch-notes 규칙과 notes-index 원본 검증 통과. 생성 목차의 PR 변경 금지 규칙을 지킵니다.

## 한계
문서와 기록만 수정했습니다. 전체 공개 스크립트·pytest·서비스·PI 인스턴스·배포·실운전·bench·HPC는 실행하지 않았고 CI·리뷰는 기다리지 않았습니다. 새 head 검토·CI·병합은 총괄이 관제합니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
