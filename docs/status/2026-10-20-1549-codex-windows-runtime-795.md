## ancestor fixture import 경로

확정 원인: detached direct child의 script 경로는 임시 폴더이며 pytest/WMI caller의 sys.path는 상속되지 않습니다. parent-fixture.log의 tests import 실패를 재현했습니다. launcher·parent가 이미 전달받는 checkout root를 helper import 전에 추가합니다. 제품 코드·Job gate·소유 handle·parent-pin 단정은 유지합니다.

로컬 mock/static 13 passed. 새 회귀 2개는 import cache를 우회한 경로 검색으로 수정 전 ModuleNotFoundError, 수정 뒤 통과를 확인합니다. 기존 fixture contract·Python 3.10 AST·encoding 검사도 통과했습니다. guard 유지·blocked 0, 실제 process/WMI는 로컬 미실행입니다.

03828116의 제공된 Windows 로그는 positive·nested Job-close 생존 PASS, 1 failed/8510 passed/48 skipped입니다. 새 head CI는 미확인이고 조회·대기·봇·병합·배포는 하지 않습니다. 수정 범위는 해당 test와 필수 기록뿐입니다.
