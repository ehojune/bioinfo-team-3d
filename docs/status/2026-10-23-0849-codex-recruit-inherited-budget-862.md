## 단계
P1 — 계획 채용 변환에 상위 비용 예약 전달 (#862)

## 한 일
- 상위 계획 채용 task의 예약액과 남은 금액을 실제 내부 변환 task까지 전달합니다. 예약 모드에서는 채용 설정 상한과 함께 작은 값을 택하고, 잘못됐거나 없는 allowance는 보내지 않습니다.
- Claude CLI의 cent 단위로 내림해 예약액보다 큰 상한이 전달되지 않게 합니다. 1cent 미만은 거절하며 기본 예약-off와 수동 채용은 유지합니다.
- 내부 결과는 기존 task ID 회계로 한 번만 집계합니다. 내부 실행 뒤 결과를 못 받은 경우 상위 receipt도 비용 UNKNOWN으로 남깁니다.

## 테스트 결과
- 생산 코드·Task 선언·Claude 예산 argv 구간을 추출한 순수 fake 검사 11건과 Python 3.10 문법 검사 통과.
- Windows 경계에 해당 새 파일만 등록했고 11개 node 선택을 확인했습니다. 기록·공개·diff 검사 통과.
- 제품 import·pytest·async loop·실제 runner/CLI·프로세스·모델 실행은 하지 않았습니다. 실제 task 실행과 전체 회귀는 CI에서 확인합니다.

## 막힌 점
공급자 청구 상한이나 실제 A/B 성공을 입증하지 않습니다. 독립 검토·CI·병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
