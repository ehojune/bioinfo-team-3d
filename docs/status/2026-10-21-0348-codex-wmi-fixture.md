Refs #788, #793

실제 Windows job118803767466의 두 실패는 diagnostics를 받지 않는 기존 fixture 래퍼가 Create 전에 거부한 문제다. 전달 인자만 맞추고 모든 기존 assertion·1회 Create·no-ACK/미소유 PID 보호를 유지했다.

원래 함수 AST의 fake-only 검증: 수정 전 TypeError, 수정 뒤 전달 identity·1회 fake Create·receipt/identity assertion PASS. Python3.10 AST·diff PASS. 실제 native fixture 재실행·push·CI·병합·배포는 하지 않았다. C3 policy 작업과 독립된 로컬 커밋이며 root가 C3 완료 뒤 결합한다. 원래 WMI timeout 원인은 미확인이다.
