## 단계

#666 rev4 · [PR #1257](https://github.com/ehojune/bioinfo-team-3d/pull/1257). Draft/hold 유지, 병합은 총괄 판단.

## 변경

- Runner·adapter의 기본 off 연결은 요청 시 unsupported로 거부합니다. 기존 승인 gate를 유지합니다.
- offline producer/observer 결과는 `synthetic_only`이며 보호 capability를 부여하지 않습니다.
- 새 회귀를 Windows inventory와 필수 boundary cohort에 등록하고 manual 한영판에 사용 한계를 명시했습니다.
- status 파일명을 HHMM 형식으로 고쳤습니다. [연결 계약](../reference/sandbox-666-phase2.md).

## 검증

- 관련 7개 파일 회귀: 275 passed, 10 skipped, 1 xfailed, 7 subtests passed.
- `scripts.ci_selection.validate_profile()` 통과. 최종 head CI·실제 리뷰·기록 검사는 PR 댓글에서 추적합니다.

## 남은 조건

- 최종 head CI·실제 리뷰와 P1 해결 뒤에도 draft/hold를 유지합니다.
- 실제 보호·인증 cleanup은 **UNVERIFIED**. 실제 engine/provider·credential·provisioning·연구/HPC·배포는 범위 밖입니다.
