# 반복 fake 최종 검증 (#923)

<!-- DO NOT MERGE: #925 and fresh-install proof are pending. -->

PR #926(`9976ef3f`)의 구현을 main 기반에서 마무리합니다. **선행 #925 병합·최종 새 설치 검증 전까지 draft를 유지합니다.** #923은 OPEN입니다.

- shutdown 뒤의 최종 Audit counter도 성공 조건으로 검사하고 종료 receipt를 대조합니다.
- main 기반 반복·설치·encoding 회귀 46 passed입니다. #925 병합 뒤 최신 main을 통합해 공백 Windows 경로에서 새 설치 → 동일 fake 2회를 검증하고 개별 receipt·비교 JSON을 보존합니다.
- 실제 모델 연구·외부 HTTP 조회·HPC·B/c3·provider billing·독립 hard-stop 집행은 미검증입니다. [검증 계약](../reference/repeat-fake-923.md). Refs #923.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
