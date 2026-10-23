## 결과
Closes #905

접근 metadata와 최초 실증 출처 계보를 기존 evidence contract에 추가했습니다.

## 변경
- `SourceRef`: 접근 범위·Methods/QC 위치·보충자료 상태. `Evidence.status`와 구분합니다.
- `Evidence.lineage`와 `assess_lineage`: 확인 근거가 있는 원출처만 확정하고 같은 source identity를 합칩니다. 순환·누락·미확인은 context로 남기며 독립 근거에서 제외합니다.
- `independent_groups`·verifier가 원출처 group을 재사용합니다. 명시한 계보의 원출처 조회가 미확인이면 verifier도 통과시키지 않습니다. legacy payload·group 동작은 유지합니다.

## 검증·인계
- 관련 pytest 303 passed: lineage, research evidence, source verifier, semantics pilot.
- Python 3.10 AST·public·diff 검사 통과. 전체 suite·최종 head CI·봇 판정은 PR 기록을 따릅니다.
- 공용 contract: https://github.com/ehojune/bioinfo-team-3d/issues/905#issuecomment-6371292848
- helper는 `labhq.evidence`에서 import합니다. `assess_lineage`와 `independent_groups` 모두 optional `artifact_paths`를 받습니다.
- report·manual·fake 통합은 #907 담당입니다. 실제 원문 독해 품질·기관 조회·연구 실행은 검증하지 않았습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>