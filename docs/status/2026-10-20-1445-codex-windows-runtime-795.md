## supervisor fixture 후속

PR #802의 fake-role 시작과 parent-pin chain을 raw Popen에서 기존 verified spawn으로 연결했습니다. direct/WMI의 보유 handle과 file output을 함께 다루며 실패 시 기존 Job journal·fixture traceback을 제공합니다. 제품 gate와 lifecycle/restart/parent-pin 단정은 유지합니다.

확정한 결함은 fixture의 launch 경계 우회입니다. redirector Job이 네 실패의 직접 원인인지는 기존 로그로 확정하지 않습니다. d5954101 Windows 로그의 positive·nested WMI 생존은 PASS지만 수정 fixture의 실제 CI는 미확인입니다.

로컬 mock/static 155 passed, guard 유지·blocked 0. Python 3.10 AST·public·diff·기록 검사 결과는 [근거](../reference/windows-runtime-795.md)에 연결합니다. reference 3,702→4,357자(LF Unicode), manual 166,480자 유지. 실제 process/WMI는 로컬 미실행이며 CI 조회·봇·병합·배포는 총괄 소관입니다. manual·#805 문서는 수정하지 않았습니다.
