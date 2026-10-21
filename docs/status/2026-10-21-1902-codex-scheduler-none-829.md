# HPC 비활성 설정의 명령·job 감시 차단 (#829)

- none에서 Scheduler의 제출·큐·상태·취소와 내부 명령 실행을 명시적으로 거부한다. SSH를 구성하거나 subprocess를 호출하기 전 차단한다.
- Runner는 로컬 broker/연결을 유지하고 job watcher 예약·직접 polling을 차단한다. 기존 job·missing count·notified·요청 연결은 보존한다.
- 기존 코드에서 새 회귀 13건 실패, 수정 뒤 관련 mock 37건 통과. SGE/PBS/Slurm/mock 계약을 확인했다. Python 3.10 AST·public·diff·기록 검사 결과는 최종 로컬 보고에 기록한다.
- 실제 process/thread·PI·HPC·WSL·연구·CI는 실행하지 않았다. 임의 shell·모델 cache·외부 자동화 격리는 이 수정의 증거가 아니다. 로컬 커밋만 준비하며 push/PR/CI는 root가 맡는다.
