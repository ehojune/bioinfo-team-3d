## #826 일부 — key_fulltext의 Unpaywall email 전달 (source a6c9e86)

- 변경: `annot_mcp._run_key_fulltext`가 `_run_literature`와 같은 `annot.unpaywall_email`을 `LiteratureAnnotator`에 넘긴다. 설정한 사용자의 핵심 논문 조회도 Unpaywall 합법 OA 위치를 먼저 확인한다. 미설정은 이전 기본값(비활성)과 같다. 기관 세션·PDF 카드·timeout·접근 receipt 경로는 그대로다.
- 검사: 기존 `tests/test_external_wait.py`를 고쳤다.
  - 새 검사: 가짜 `pi@example.org`와 빈 값이 key_fulltext와 fulltext 생성자에 같게 전달되는지 본다. backend는 생성 시점에서 멈추므로 요청이 나가지 않는다.
  - PDF 대기 검사: 빈 값 전달을 단언으로 추가했다.
  - 두 settings stub에 `annot`를 넣었다.
- 로컬: 생성 인자 계약을 AST 추출로 순수 실행했다(HEAD FAIL, 수정 후 PASS). Python 3.10 AST/compile, `git diff --check`, `check_public.sh`를 통과했다. async 검사와 파일 전체는 CI에 맡긴다.
- 남음(#826 open): 셋업의 기관·문헌 질문, 연결 점검, 제공자 표, WoS/Nexus 연동. 실제 조회·기관 인증은 하지 않았다. 매뉴얼은 이미 "허용 OA → 기관 세션 → PI PDF"로 설명하고 있어 바꾸지 않았다.
- 공개 준비(09:21): 승인 main `9d4836c`(#846·#845·#843·#841)를 merge `888b774`로 통합했다(충돌·자체 변경 없음, core 두 파일 hash 불변). 매뉴얼 Unpaywall email 항목에 `key_fulltext`도 같은 email을 쓴다는 한 문장을 더했다(`376af69`, 177,138→177,193자). 새 head의 독립 검토와 CI는 남았다.
- 작업 중 main이 #847(`9588695`)로 움직여 `06dfb1b`를 merge `2a78722`로 한 번 더 통합했다. 매뉴얼은 서로 다른 줄이라 자동으로 합쳐졌고 main 대비 차이는 이 branch의 5파일뿐이다.
