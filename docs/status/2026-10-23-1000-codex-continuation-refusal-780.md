## 단계
P2 — 이어 가기 계획 거절 뒤 복구 선택 보존 (#780)

## 한 일
- 이전 round를 복원한 종료 outcome과 이번 모델 거절 원인을 구분해 복구 카드를 표시합니다.
- 다른 모델 선택은 같은 요청·예정 round를 새 계획과 CP1부터 재개합니다. 이전 결과·archive·누적 비용·예산을 유지하며, 문구 수정은 기존처럼 명시적으로 새 요청을 만듭니다. 종료 선택은 실행하지 않습니다.
- 카드의 요청 시각·거절 task·round·계획과 승인 맥락 snapshot을 확인하고 바뀐 카드·이미 전송된 round는 거부합니다. 복구 선택 ID를 기존 recovery matcher에 묶어 같은 round의 예전 거절 결과를 다시 채택하지 않습니다.

## 테스트 결과
- 생산 종료·identity·카드·선택 코드를 추출한 synchronous inert fake 12건, Python 3.10 문법과 Windows 새 파일 12node 선택 PASS.
- 실제 Hub 카드/resolver의 trigger 2종×선택 3종 회귀는 추가만 했고 로컬에서는 실행하지 않았습니다. UI runtime 확인도 CI에 맡깁니다.
- 제품 import·pytest·async loop·프로세스·브라우저·모델 실행 없이 기록·공개·diff 검사만 수행했습니다.

## 막힌 점
실제 연구 성공이나 시운전 GO를 뜻하지 않습니다. 독립 검토·CI·병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
