Refs #825

Owned launcher 실패 시 기존 journal allowlist와 parent exitcode를 최대4096자 JSON으로 출력합니다. 진단 실패도 원래 예외를 바꾸지 않으며 45초·native assertions·제품 동작은 유지합니다.

Fake/AST 17건 PASS. 실제 Windows/WMI 원인·성공은 미확인입니다. Native 실행·push·CI 조회·병합·배포는 하지 않았습니다.
