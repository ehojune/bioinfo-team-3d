## #826 일부 — 첫 설정의 기관 문헌 접근 질문 (source c60adba, base 06dfb1b)

- 변경: `labhq init`이 사람이 터미널에 있을 때만(`--yes`·`--dry-run`·비대화형 제외) 기관 이름, 도서관 프록시 주소, 이용 중인 문헌 서비스, Unpaywall 연락 email을 묻는다. 모두 Enter로 건너뛸 수 있다.
  - 새 설정에는 기존 항목 `literature.institution_proxy`와 `annot.unpaywall_email`만 저장한다. 프록시는 https, 계정 정보 없음, `url=`/`{url}` 외 query 없음일 때만 받는다. 기관 세션·TDM·외부 모델 전송은 꺼 둔다. schema는 바꾸지 않았다.
  - 기관 이름과 서비스는 저장하지 않고 안내에만 쓴다. Web of Science·Nexus는 기관 연결 주소·권한이 확인되지 않았고 자동 연결하지 않는다고 알린다.
  - 기존 설정은 다시 쓰지 않는다. 현재 상태(프록시·세션·email 유무)를 보이고, 넣을 항목만 YAML로 출력한다.
  - 비밀번호·cookie·API 키는 묻지 않으며, 접속·로그인·다운로드를 하지 않는다.
- 문서: 매뉴얼 첫 설정 절에 안내 한 문단을 더했다(177,200→177,632자).
- 검사: `tests/test_init_wizard.py`에 프록시 판정 10건, 새 설정 저장, 전부 Enter, 거부, 재실행 미변경, 비대화형·`--yes`·`--dry-run` 미질문 검사를 더했다. 로컬에서는 helper를 AST로 추출해 실제 `LiteratureSettings`·`Settings`와 가짜 입력으로 순수 실행해 모두 통과했다. init 흐름 test(`wizard.run`)는 doctor import에 필요한 의존이 로컬에 없어 CI 몫이다.
- 남은 #826: 연결 뒤 검색·원문·추출 점검, 제공자 표, 호출 기록과 실패 인계, WoS/Nexus 실제 연결, 구독 원문. 기관 계약·endpoint는 외부 미확인이다.
- 공개 준비(09:35): 승인 main `4ff7c12`(#848 `92b4f9a`)를 merge `591c387`로 통합했다(자체 변경 없음, init·test hash 불변). #848 peer P3에 따라 매뉴얼 Unpaywall email 항목의 문장 순서를 고쳐 `enabled: false`가 `unpaywall` 도구의 답이고 `key_fulltext`는 email이 없으면 Unpaywall만 건너뛴다고 적었다(`3f31354`, 177,687→177,758자). 코드·timeout·기관·PDF 경로는 그대로다.
