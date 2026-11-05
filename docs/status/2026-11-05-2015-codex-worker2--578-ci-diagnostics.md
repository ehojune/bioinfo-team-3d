# #578 rev10 main 통합·Windows 선택 확인

**hold 유지·미병합.** 실제 HPC는 실행하지 않았다.

- main `a7ca7667`을 통합했다. [#1284](https://github.com/ehojune/bioinfo-team-3d/pull/1284)의 helper 상수 `"1: b"`를 그대로 받아 `tests/semantics_shadow_lab.py`가 main과 같아졌다. 다른 충돌은 없었다.
- [이전 CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42021211558)는 Windows pool의 13분 상한으로 실패했다. [진단 근거](../reference/ci-1283-windows-pool-timeout.md)에 따라 helper 변경을 PR diff에서 없앴다. 시험 선택 정책·assertion·상한·lock 보호는 바꾸지 않았다.
- 로컬 관련 회귀와 정적 시험: **84 passed/1 skipped**. integrations·vocab·notes-index·public·diff 검사 통과. 새 head의 CI·실제 리뷰는 대기 중이다.

rev8의 receipt P1·unlock P2 수정과 **327 passed/6 skipped**는 [이전 검증](https://github.com/ehojune/bioinfo-team-3d/pull/578#issuecomment-6657466961)에 보존했다. 통합 뒤 실제 Windows 실행 시간은 아직 미확인이다.
