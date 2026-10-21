Refs #825

Clean-install 시작 실패의 안전한 진단을 임시 폴더 삭제 전에 남긴다. 두 supervisor의 고정 phase·실패 분류·제한된 시간만 출력하고 원래 오류·실제 up/readiness·정리 계약은 유지한다. 제품 transport·timeout·Job/ACK는 바꾸지 않았다.

기존 실패는 provider_timeout 외 수신/Create 증거가 없어 원인 미확정이다. Fake/static12건·Python3.10 AST·기존 호출/정리 대조를 통과했다. 실제 설치·process·WMI·PI 실행과 CI 조회·push는 하지 않으며 다음 remote clean-install 검증은 총괄이 맡는다.
