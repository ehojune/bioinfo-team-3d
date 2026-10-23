## 단계
P2 — 이어 가기 계획 거절 뒤 복구 선택 보존 (#780)

## 한 일
- 이전 round를 복원한 종료 outcome과 이번 모델 거절 원인을 구분해 복구 카드를 표시합니다.
- 다른 모델 선택은 같은 요청·예정 round를 새 계획과 CP1부터 재개합니다. 이전 결과·archive·누적 비용·예산을 유지하며, 문구 수정은 기존처럼 명시적으로 새 요청을 만듭니다. 종료 선택은 실행하지 않습니다.
- 카드의 요청 시각·거절 task·round·계획과 승인 맥락 snapshot을 확인하고 바뀐 카드·이미 전송된 round는 거부합니다. 복구 선택 ID를 기존 recovery matcher에 묶어 같은 round의 예전 거절 결과를 다시 채택하지 않습니다.

## 테스트 결과
- 생산 종료·identity·카드·선택 코드를 추출한 synchronous inert fake 13건, Python 3.10 문법과 Windows 새 파일 13node 선택 PASS.
- 실제 Hub 카드/resolver의 trigger 2종×선택 3종 회귀는 추가만 했고 로컬에서는 실행하지 않았습니다. UI runtime 확인도 CI에 맡깁니다.
- 제품 import·pytest·async loop·프로세스·브라우저·모델 실행 없이 기록·공개·diff 검사만 수행했습니다.

## CI 후속
- 첫 CI의 Linux 3.10/3.12는 기존 metadata 회귀의 plain namespace에 새 helper 연결이 빠져 각각 1건 실패했습니다. 실제 helper를 연결하고 같은 원래 회귀를 동기 추출 검사에 포함했습니다. 제품 코드는 바꾸지 않았습니다.
- Windows는 기존 #833 브라우저 시작 30초 초과(30 PASS/1 FAIL)로 중단됐습니다. 이 수정은 해당 실패를 해결했다고 주장하지 않습니다.
- 공통 시작 예산 수정 #881이 병합된 뒤 main을 충돌 없이 통합했습니다. 복구 제품·기존 fake 검사·CI 선택기는 검토된 a142462d와 같은 Git blob입니다. 새 head의 실제 CI·리뷰는 별도로 확인하며 새 연구 요청·worker 호출은 없습니다.

## 최종 리뷰 수정
- b3f13b8a는 CI10개 모두 통과했으나 봇이 단계 이름 `plan`과 계획 거절 기록의 충돌을 지적했습니다. 이전 단계 결과를 덮어쓰고 같은 요청의 복구 카드를 잃는 문제를 review·CP2 두 경로에서 재현했습니다.
- 계획 제어 결과는 진단 기록에 보존하고 이전 단계 결과와 분리했습니다. 같은 단계 이름의 결과 보존→카드 생성→다른 모델로 같은 요청 복구를 검증한 동기 fake 회귀를 추가했습니다.
- 수정 전 두 경로 FAIL, 수정 후14검사 PASS. 제품 import·async loop·외부 process·network·signal 없이 실행했고 Python3.10 AST·profile도 통과했습니다. 새 head CI·리뷰는 다시 확인해야 합니다.

## 막힌 점
실제 연구 성공이나 시운전 GO를 뜻하지 않습니다. 독립 검토·CI·병합은 총괄 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
