# #1387 r1 — 진행 중

- 2: 합성 QC 위치 정정이 원장에 반영되지 않음. 구조화 정정 계약과 범위 요청.
- 7: 원격 목록에 SHA-256 기록, 후속 입력에서 대조. 로컬 verify는 원격 파일 무결성을 미확인으로 반환.
- 16: 읽기 전용 스킬 안내와 편집자·리뷰어 지시를 실행 조건에 맞춤.
- Windows: transfer 75 passed·12 skipped, read-only 86 passed·1 skipped, remote-data 38 passed·18 skipped. Linux 로컬 helper는 정상 파일과 같은 크기 변조를 확인.
- Windows 관련 두 파일은 수정 전 7 failed → 수정 후 156 passed·25 skipped. [근거와 잔여](../reference/fix-1387.md). 실제 HPC 미검증.
- CI status 파일명 오류와 리뷰 P1 두 건(해시 작업 timeout·Linux 시험) 처리 중. 병합 보류.
