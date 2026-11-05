Fixes #797
Refs #672, #680

PR #807: CP1 재사용 계약 `232690b`, 복구/예산 경계 `d175432`, 리뷰 수정 `ea0f289`.

- 실행 prompt와 identity가 같은 노드별 과학 context를 사용합니다. scoped prompt 이전의 도구 signature는 증거로 인정하지 않습니다.
- 도구·engine 설정은 CP1 대기 전 대신 실제 dispatch에서 기록합니다. retry/wake/결과 교정의 혼합 설정, 설정 변경 중 실행·증거 없는 복구는 재사용을 거절합니다.
- 부분 실행 뒤 drift는 진행 중인 차수와 완료 결과를 보존합니다. 새 CP1은 현재 차수의 완료 결과도 입력·도구·산출 증거를 검사해 snapshot에 포함하며, 증거가 깨져 있으면 기록을 남긴 채 중단합니다. 조용한 재실행이나 승인 우회는 없습니다.
- 기존 head에서 실제 실패 패턴 **8 failed**를 재현한 뒤 관련 mock/static **113 passed**. 현재 차수 완료 결과의 재승인·중복 dispatch 방지, CP1 카드/seal, 새 계획 전 restart, 예산 rollback, #796 input-fit/지표·Shadow 분리를 확인했습니다. 지적 4321664411은 기존 `d175432`와 복구 회귀 테스트로 해결을 확인했습니다.
- Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. 최신 main의 #810 변경을 merge로 보존했습니다. 전체 traceback은 작업별 local artifact에 보존했고 mock guard의 blocked는 0건입니다.
- 실제 process/thread/Hub·연구/bench·PI·원자료/HPC 실행 없음. Windows/3.10 실행·전체 suite·최종 head CI/리뷰·P1=0 판단은 controller 담당입니다. 파일 변경을 잠그지 않으며 실제 도구 설치 판본·과학 효용은 미검증입니다. 기존 P2와 #798/#811은 범위 밖입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>