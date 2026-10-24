@codex-tech-lead

# 강제 종료 복구·중단 조건 (#924)

- runner 재기동 뒤 저장된 interrupted 결과를 다시 반환해 두 번째 resume 카드가 열리던 결함을 고쳤습니다. 성공 결과·live task 재채택은 유지하고 남은 단계만 새 task ID로 실행합니다. 기존 비용·조회 기록을 지우지 않습니다.
- 시험 소유 handle로 gateway/runner 각각을 강제 종료·재기동했습니다. 승인 1회, 완료 s1 실행 1회, 같은 task 중복 실행·수락 0, 비용 $0.04와 원장 일치, 조회 각각 2·3회, 잔류 0입니다.
- 관련 회귀 52 passed, live adoption·비용 회귀 13 passed, 강제 종료·receipt 변조 거부 2 passed. direct checkpoint 단독·unknown 비용 기록도 확인했습니다. Windows inventory에 두 검사를 등록했고 CI selection 회귀 85 passed입니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 PASS.
- manual 중단 조건을 추가했습니다: 186,764→187,844자(+1,080). [코드 대조·재현·한계](../reference/recovery-stop-924.md).
- 봇 P2의 새 task 비용 행·합계 동시 누락을 재현했고 모든 실행·수락 task의 known 비용 행을 요구하도록 고쳤습니다.
- 통합 전 근거입니다. #925→#926 병합 뒤 같은 설치판에서 최종 재현, 최종 head CI·봇 검토·모든 head 미해결 P1=0이 남았습니다. Refs #924.
- 실제 모델·외부 서비스·HPC·PI 인스턴스·B/c3 proof는 실행하지 않았습니다. driver deadline은 제품의 요청 전체 독립 hard-stop 증명이 아닙니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
