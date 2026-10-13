# PR #553 main 충돌 해소

- main `6150223`을 merge commit `cb564bf`로 반영했다.
- 직원 전용 설치·open_reads·pin 검사·Codex recruiter 조건부 필드와 main의 홈 격리 guard를 보존했다. 설정 경로와 명시한 설치 대상도 I/O 전에 검사한다.
- 관련 165 passed·3 skipped, 추가 홈 보호 회귀 6건 포함. Windows 전체 재실행은 5977 passed·66 skipped(704.45초), 공개·diff 검사 통과.
- 첫 전체 실행의 41건 실패는 홈 격리 후 로컬 Node shim이 실행 파일을 찾지 못한 경우다. 기존 node.exe 경로를 테스트 PATH 앞에 넣어 웹 66 passed·1 skipped와 전체 통과를 확인했다. guard는 유지했다.
- CI 최종 결과는 PR #553 본문에 기록한다. 리뷰 요청·PR 병합·실제 직원 실행은 하지 않았다.
- pytest 임시 폴더 정리는 완료하지 못했다. 남은 경로는 로컬 인계 기록에 남겼다.
