## #842/#843 — Windows demo Chrome 소유 Job과 supervisor 시작 실패 진단 (source 1193f98, c8751ad)

- 이전 실패: PR843 첫 CI(18abfff)의 Windows forced 실Chrome 회귀는 소유 launcher exit 뒤에도 stdio pipe가 마감 후 13초 열려 있었다. 소유 범위 밖에 남은 하위가 쥐었을 가능성이 높지만 미확인이다.
- 선택: C1 설계안을 root가 채택했다.
  - Windows에서만 Chrome을 제품의 `labhq/runner/memory_limit.py --windows-job` 아래에서 띄운다. 실소스 확인: 자기 자신을 Job에 넣은 뒤 Chrome을 시작하고, Job handle은 비상속이며, `JOB_MEMORY|KILL_ON_CLOSE`다. 메모리 상한은 소유 목적의 큰 값(64 GiB)이다.
  - 소유 child는 wrapper다. forced 정리는 그 wrapper만 끝낸다. 정상 graceful 경로의 Node kill은 0이다.
  - interpreter는 `test_web_3d.py`가 넘기는 `LABHQ_TEST_PYTHON`(=`sys.executable`)만 쓴다. PATH나 Store alias로 대체하지 않으며, 없으면 profile·server를 만들기 전에 실패한다.
  - Linux 경로는 바뀌지 않았다. 제품 Job 코드도 바꾸지 않았다.
- 유지: 실제 exit·stdio close·socket·server close와 profile 정리로만 완료한다. 5초(3+2), 30/170초, noskip, 필수 26은 그대로이고 회귀 제거는 없다.
- 함께: `tests/test_supervisor_process.py`의 실 gateway 시작 실패(`SystemExit: 2`, 감독 프로세스가 기록 전에 종료) 때 이 test 자신의 supervisor 로그 꼬리를 남기는 진단(C3 patch 검토 후 통합). 원래 예외와 90초 단언은 그대로다.
- 검사: bundled Node `--check`(CJS 2개), fake 갱신(Windows 실행 명령 형태, 실패 5종은 자원 생성 전, Linux 직접 실행). 정적 CI 계약 199 passed, supervisor 순수 검사 1 passed, 3.10 AST·profile·public·diff 검사 통과.
- 미확인: 실제 Windows에서 Job close 뒤 2초 안에 stdio close가 오는지, Chrome 하위가 Job을 벗어나지 않는지, supervisor 재발 여부와 원인. 새 head 전체 CI가 필요하며 이전 head 결과는 재사용하지 않는다.
