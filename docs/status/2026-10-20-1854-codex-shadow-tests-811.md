Fixes #811
Refs #804
Draft PR #818

Shadow 비교 검사에서 같은 off 실행의 반복을 줄였습니다. on 비교와 안전·제거 검증은 유지합니다.

- off 기준은 독립 상태에서 한 번 계산한 JSON 문자열만 공유합니다(비교 네 경우: lab 호출 8→5). on 네 실행은 각각 새 상태이며 fresh-process off/import·fail-open·privacy/read-scope 검사는 유지합니다.
- 제거 뒤 compile·잔여 참조·상태 복구·resume 승인·필수 E2E를 유지하고 child 실행은 계약 node로 좁혔습니다. 전체 생존 테스트 수집을 추가했습니다. #804의 script 의존 boundary harness는 script와 함께 제거하고 mainline CP1 검사는 남깁니다.
- 수집을 깨던 mixed-suite의 Shadow 전용 테스트 5개를 observer 테스트로 이동했습니다. AST 대조로 이동한 본문·assertion과 기존 함수 68개의 보존을 확인했습니다. CI workflow/classifier·Shadow 동작·fsync/SQLite 정책은 변경하지 않았습니다.
- 기존 head에서 새 계약 4건 red, 수정 뒤 관련 mock/static 79 passed·1 skipped(로컬 비공개 단어 목록 미설정)·2 deselected(실제 제거/E2E). 로컬 subset은 7.13초이며 CI 시간 절감은 미측정입니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 결과는 기록에 남깁니다.
- 실제 subprocess/thread·제거·Hub/PI 운영·연구/bench/HPC/원자료는 실행하지 않았습니다. 변경된 child 선택의 실제 수집/상태/E2E·Windows/3.10 통합과 전후 CI duration은 controller 검증 경계입니다. Draft로 제출하며 CI·리뷰를 기다리거나 봇 호출·병합·배포하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
