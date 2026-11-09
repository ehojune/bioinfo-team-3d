# #1347 r2 — 요청별 원격 산출

- 요청별 scratch 경로·잡별 새 폴더를 유지하고 산출은 원격 `data/`로 이동해 보존합니다.
- runner가 파일·크기·SHA-256 manifest를 저장합니다. 다음 잡 제출 전 같은 요청의 성공한 선행 단계와 hash를 다시 확인합니다.
- 큰 필수 산출은 로컬에 없어도 완료로 인정합니다. 작은 파일만 회수하고 경로·용량을 결과와 provenance에 남깁니다.
- 검증: 관련 Windows mock·CI inventory 회귀 398 passed·25 skipped. WSL helper smoke: 1,200개 파일·중첩 경로·재시도·변조·symlink 거절 PASS. public 검사·Windows inventory·diff 검사 PASS. CI·리뷰는 PR에 기록합니다.
- 미확인: 실제 HPC·다중 직원 운영. 원격 Linux `python3` 필요. `cso.py` 동시 dispatch·CP1 첫 줄 P2는 다음 revision 범위이며 이슈를 닫지 않습니다.
