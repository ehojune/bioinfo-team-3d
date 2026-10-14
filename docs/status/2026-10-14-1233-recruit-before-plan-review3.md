PR #581 · `recruit-before-plan` · Refs #549

- 4266319195: 내부 변환 task를 요청 예산에 ID별 한 번만 집계. 다음 dispatch 전 예산 카드 확인.
- 4266319201: 변환 입력을 계약에 저장·비교. repo·focus 변경 시 별도 인재로 재변환.
- 4266319202: 제안 필드·단계 존재·중복·기술자형 조건을 한 함수에서 검사하고 일반·연구 계획 교정으로 반환.
- 4266319205: 남은 단계 계약을 재개 전에 확인. 확정 전 fallback, 확정 후 terminal failure와 기존 resume 카드 종료.
- PR 원래 head `87cc431`: 새 회귀 35 failed / 7 passed. 수정 뒤 42건 통과.
- 관련 pytest 424 passed + Windows 파일 교체 WinError 5 한 건; 해당 test 재실행 1 passed. Node 51개 파일 통과.
- CI에서 발견한 mock 제안의 누락 step_ids와 roster 없는 재개 경로를 보완. 기존 회귀 5 failed → 관련 163 passed, shadow 제거 검사 6 passed.
- 이후 main의 웹 테스트 자동 수집을 병합. 채용·웹 174 passed / 1 skipped, Node 52개 파일 통과.
- 다른 레인 전체 pytest와 겹쳐 로컬 전체 검사는 생략. 최종 CI 결과는 PR 본문에 기록.
- 공개·패치노트·목차 검사 후 push. PI/trial 실행·리뷰 요청·인라인 답글·병합 없음.
