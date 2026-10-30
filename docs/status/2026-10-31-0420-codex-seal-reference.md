## 단계
#1063 rev8 · [PR #1064](https://github.com/ehojune/bioinfo-team-3d/pull/1064)의 reference P2 수정. Draft/hold·미병합 유지.

## 한 일
- reference를 `synthetic-v3`에 맞췄습니다. v2는 unsupported이며 정상 read/output만 synthetic 수락합니다.
- observer·외부 ledger·seal·broker·deadline·cleanup 조건을 현재 corpus/validator와 대조했습니다.
- reference·전용 patch note·이 status만 바꿉니다. 실제 adapter와 보호는 UNVERIFIED입니다.

## 테스트 결과
- notes_index·patch_notes·public·diff 검사 PASS. 최종 head CI·실제 리뷰 결과는 PR에 기록합니다.
- reference 3,638→5,134자. 새 lifecycle 조건은 lookup-only 표에 모았습니다.
- corpus·validator 변경이 없어 로컬 pytest는 재실행하지 않습니다.

## 막힌 점
- 실제 adapter 인증·OS seal·handle 취소·독립 inventory와 #666 통합 승인은 남아 있습니다. 실제 실행·provisioning·연구/HPC는 수행하지 않습니다.
