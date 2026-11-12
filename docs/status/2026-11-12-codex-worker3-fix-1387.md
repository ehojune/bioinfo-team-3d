# #1387 r1 — 진행 중

- 2: 합성 QC 위치 정정이 원장에 반영되지 않음. 구조화 정정 계약과 범위 요청.
- 7: 원격 목록에 SHA-256 기록, 후속 입력에서 대조. 로컬 verify는 원격 파일 무결성을 미확인으로 반환.
- 16: 읽기 전용 스킬 안내와 편집자·리뷰어 지시를 실행 조건에 맞춤.
- Windows: transfer 75 passed·12 skipped, read-only 86 passed·1 skipped, remote-data 38 passed·18 skipped. Linux 로컬 helper는 정상 파일과 같은 크기 변조를 확인.
- 기존 whole-request 시험 6건은 이전 verify 종료 코드를 기대해 실패. [근거와 잔여](../reference/fix-1387.md), 시험 범위 추가 대기. 실제 HPC 미검증.
