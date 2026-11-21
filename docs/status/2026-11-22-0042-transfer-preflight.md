# #1447 r13 · [PR #1473](https://github.com/ehojune/bioinfo-team-3d/pull/1473)

- CP1과 CP1을 겸하는 재개 카드에서 HPC 입력으로 적은 개별 `--ref` 파일이 기본 전송 한도(512 KiB)를 넘으면 크기와 초과량을 표시합니다. 경고에는 파일명과 단계만 넣고, 승인 해시는 동결 계획으로 계산합니다.
- Windows 집중 시험 221건 통과, 10건 건너뜀. 두 카드의 경고 문구와 승인 해시가 같은지 확인했습니다. Node 회귀 24건과 별도 DOM 검증 1건에서 두 카드의 경고가 펼쳐지고 문구가 그대로 표시되는지 확인했습니다. 공개 검사 통과.
- 실제 HPC→PC 입력 사용 검증은 PI의 재시도 승인을 기다립니다. PR은 draft와 `hold`를 유지합니다. [실제 검증 기록](https://github.com/ehojune/bioinfo-team-3d/issues/1447#issuecomment-7065043971)
