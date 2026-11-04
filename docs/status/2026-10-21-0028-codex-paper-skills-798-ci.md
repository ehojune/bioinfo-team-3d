CP1에서 논문 스킬 생성이 끝날 때까지 기다리던 흐름을 바꿉니다. CP1에서는 메타데이터만 확인하고 승인 뒤 스킬을 준비합니다. 담당 스텝은 hash와 소유권을 확인한 문서만 읽으며, 결과의 참조 receipt로 사용 여부를 기록합니다.

- 승인 전 준비 금지, 승인된 계획·hash, 취소 전파와 읽기 전용 입력 경계를 유지합니다. 파일 배치만으로 `called=true`를 기록하지 않습니다.
- 기존 CI 실패 4건을 재현한 뒤 같은 4건과 관련 17건의 fake 검사가 통과했습니다. prompt snapshot은 의도한 변경을 역치환으로 확인하며 단언을 유지했습니다.
- 앞선 main 문서 통합에서는 제품 코드·테스트를 바꾸지 않았고 자동 목차는 main 것을 보존했습니다. 공개 검사 2회와 기록 검사를 통과했습니다.

최종 head의 CI·재리뷰는 controller가 담당합니다. 실제 논문/PDF·연구 요청·성능 검증은 하지 않았습니다.

Fixes #798

## 10-21 Linux 3.12 CI 보정 (1079d5b)

- Job 118674733476의 terminal error는 staff-path 테스트의 전역 Path trap이 pytest traceback·cache·정리도 차단한 문제였습니다. trap은 staff/cache 경로와 해당 호출 context로, git fake는 해당 모듈로 한정했습니다. 제품 코드·안전 경계는 그대로입니다.
- 기존 사전 검사 6조합은 단독 PASS였으며 무관한 진단 I/O를 끼운 새 6건은 같은 메시지로 red 재현했습니다. 수정 뒤 해당 6+6건과 예상 밖 예외/조기 staff·cache I/O/git 차단·복원 4건 등 16건 PASS(로컬 0.36초), 관련 HOME guard 3건 PASS(0.10초)입니다. 실패 trace를 보존했습니다.
- Python 3.10 AST/compile 1파일·public·diff·patch-notes·notes 검사를 통과했습니다. inherited mock guard를 유지했습니다.
- 한계: 선행 E/F의 원 traceback은 CI 로그에 없어 전체 원인은 미확정입니다. 전체 CI green을 주장하지 않습니다. 실제 설치/git·process/thread·CLI·PI·PDF·연구·bench·HPC·원자료는 미실행이며 CI/리뷰 조회·대기·봇·병합·배포는 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
