## 단계
#1063 · #666 독립 supervisor/seal synthetic acceptance v2. Draft/hold 유지, 통합 승인 전 병합 금지.

## 한 일
- 29개 corpus case와 독립 pure validator. source→parent→root의 `parent_ref`·전체 `parent_identity`를 독립 pin과 대조합니다.
- 7개 관측 단계에서 잘못된 parent·누락 edge·cycle·self-edge·분리된 subtree·root 교체·변경 후 원복을 거부합니다.
- run/nonce·supervisor/child identity·link·연속 관측·descendant/socket 정리 검사와 Windows inventory를 유지합니다.
- manual 한영판에 synthetic 검증 범위와 실제 보호 UNVERIFIED를 명시했습니다.

## 테스트 결과
- 기존 seal contract 회귀 364 passed. rev6는 문서만 수정하며 로컬 pytest를 다시 실행하지 않습니다.
- rev5 CI 41039167818은 patch-notes FAIL·manifest SKIPPED였습니다. rev6는 manual 갱신으로 문서 gate를 복구합니다.
- 최종 head 문서/public 검사·전체 CI·manifest 실행·실제 review 결과와 identity는 [PR #1064](https://github.com/ehojune/bioinfo-team-3d/pull/1064)에 기록합니다.

## 막힌 점
- 실제 witness 인증·supervisor·seal·연속 감시·독립 cleanup은 UNVERIFIED. `adapt_producer`와 `protected_launch`는 unsupported입니다.
- [v2 계약·adapter 전제](../reference/process-boundary-seal-fixtures.md). 실제 root까지의 ancestry 증명은 후속 producer 범위입니다.
