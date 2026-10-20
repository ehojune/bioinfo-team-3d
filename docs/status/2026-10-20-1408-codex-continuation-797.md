Fixes #797
Refs #672, #680
PR #807. 재사용 계약 `232690b`, CI 회귀 수정 `d175432`.

CP1 전에 검증한 노드별 재사용 snapshot을 계획/승인 hash와 묶습니다. 승인 뒤·재개·dispatch 직전 증거가 달라지면 재실행 대신 중단·새 CP1을 요구합니다. 과학 계약·도구/engine 설정·의존 산출 hash를 비교하고, 증거 없는 입력·manifest 없는 디렉터리는 제외합니다.

- CI 회귀: 무조건적인 request 조회를 연구 step 경계 안의 안전한 조회로 바꿔 consult/wake/nonresearch 동작을 보존했습니다. 새 계획 채택 전 보관 차수의 이미 결정된 checkpoint 복구만 구분하며, 채택된 계획의 새 CP1·snapshot 검증은 유지합니다.
- 해시 증명과 실행 진행을 구분했습니다. 예산 거절 전에는 실행 준비 완료로 표시하지 않아 이전 계획·승인·집행 상한을 rollback합니다. 실제 dispatch 뒤의 drift는 중단하되 그 차수를 되돌리지 않습니다.
- 실제 CI 패턴에서 누락 request mock 16건과 연구 복구/예산 4건, 합계 **20 failed**를 재현한 뒤 관련 mock/static **100 passed**. 새/동일 hash 계획 CP1, CP1/CP2 revise·resume, 예산 상한 rollback, 승인/dispatch 뒤 drift, #796 input-fit/지표·Shadow 분리를 검사했습니다. 기존 연구 실패 테스트의 assertion은 유지했습니다.
- Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. git probe는 명시 mock, native thread 시작 금지·to_thread 인라인 mock, 기존 guard 환경 유지·blocked 0건입니다. 전체 실패/성공 trace는 local artifact에 보존했습니다.
- 실제 Hub를 구성하는 ask 37건·process/thread/signal·전체 suite·Windows/3.10 실행은 CI 경계입니다. PI·원자료/HPC·연구/bench·봇 요청/대기·병합·배포는 하지 않았습니다. #798/#811·다른 owner 범위 미착수. 실제 과학 효용·도구 설치 판본은 미검증이며 파일 변경을 잠그지 않습니다. 최종 CI·리뷰·P1=0·병합은 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
