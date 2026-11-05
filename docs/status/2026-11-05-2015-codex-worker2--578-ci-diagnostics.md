# #578 rev8 CI 진단·precedent 입력 검사

**hold 유지·미병합.** 실제 HPC는 실행하지 않았다.

| 변경 | 범위 |
|---|---|
| CI 진단 | binary mode를 keyword로 적어 기존 encoding AST 검사를 통과한다. snapshot 복사와 크기·console 상한은 그대로다 |
| lock P2 | unlock이 끝난 뒤에만 `released`를 기록한다. 실패는 `release_failed`로 남기며 원래 예외·body 예외 연결을 유지한다 |
| receipt P1 | 저장된 precedent와 PI 선택 뒤 도착한 새 receipt를 같은 입력 정책으로 검사한다. HPC 미지원 입력은 planning·CP1 전에 거부하며 선택을 local로 바꾸지 않는다 |
| 기록 | status를 날짜 있는 이름으로 옮기고 rev7 patch-note 시각을 실제 커밋 시각 20:15로 맞춘다 |

수정 전: unlock 실패 2건과 HPC receipt 2건이 재현됐다. local receipt 2건은 통과했다. encoding AST 실패도 확인했다.

수정 후: 관련 mock·precedent·선택·phase·encoding 회귀 **327 passed/6 skipped**. 실제 Windows 잠금과 이전 timeout 원인은 UNVERIFIED다. [rev7 CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/42017666288)는 snapshot 업로드에 성공했으며 실패 실행의 PASS receipt는 없다.

최종 head CI·실제 리뷰 대기. 전체 gate가 끝날 때까지 hold를 유지한다. [기존 timeout 근거](../reference/windows-timeout-578.md)는 참고용이다.
