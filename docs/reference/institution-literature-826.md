# 기관 문헌 접근의 세부와 공개 확인 범위 — #826

`labhq init`의 기관 문헌 질문(#850)이 받는 값의 세부와, 기관 연결 서비스에 대해 공개 자료로 확인한 범위를 적는다. 매뉴얼의 첫 설정 문단은 요약만 둔다. 로그인, 양식 제출, 기관 내부 페이지 열람, 실제 연결 시험은 하지 않았다.

## labhq에 이미 있는 기관 경로
- 핵심 논문 본문 찾기(`key_fulltext`)는 허용 OA → 기관 세션 → PI PDF 요청 카드 순서다.
- 기관 세션은 세 설정 `literature.institution_enabled`·`institution_tdm_allowed`·`external_model_allowed`를 모두 켰을 때만 쓴다. 이때 PI가 로그인해 둔 전용 browser profile이나 `literature.institution_proxy`로 한 DOI씩 연다.
- PI PDF 요청 카드는 `external_model_allowed`가 켜져 있어야 열린다.
- 이것은 구현된 경로의 조건이다. 실제 기관 프록시·세션으로 본문을 받는지는 검증하지 않았다.
- labhq는 비밀번호·cookie·token을 읽거나 저장하지 않는다. 자세한 동작은 매뉴얼 문헌 도구 절을 본다.

## init이 묻고 남기는 것
- 질문은 네 가지다. 소속 기관, 도서관 프록시 접두, 쓰는 문헌 서비스, Unpaywall 연락 email. 모두 Enter로 건너뛴다.
- 새 설정에는 `literature.institution_proxy`와 `annot.unpaywall_email`만 남기고 기관 세션은 꺼 둔다.
- 기관 이름과 서비스는 저장하지 않고 안내에만 쓴다.
- 설정이 이미 있으면 파일을 바꾸지 않는다. 현재 상태(있음/없음, 세션 켜짐/꺼짐)와 직접 채울 설정 이름만 보인다.
- 입력한 주소와 email은 화면에 다시 출력하지 않는다.
- `--yes`, `--dry-run`, 비대화형 실행에서는 묻지 않는다.

## 받는 프록시 주소 형식
- https만 받는다.
- host에 계정 정보(`user@`)나 `{url}`가 없어야 한다. port는 유효 범위 안이어야 하고, fragment가 없어야 한다.
- 기사 주소 자리는 정확히 하나여야 한다. 셋 중 하나다:
  - 끝이 `url=` 또는 `qurl=`인 query
  - 그 값 자리의 `{url}`
  - 경로 마지막 칸의 `/{url}`
  - 다른 query는 받지 않는다.
- 알려진 세션·ticket 형식은 거부한다:
  - 모든 `;` 매트릭스 매개변수(`;jsessionid=…` 등)
  - CAS형 `ST-…`·`PT-…`·`PGT-…`·`TGT-…` 경로 칸
  - session·ticket·token 등이 이름에 든 경로 칸
- 경로 속 임의 토큰은 다 알아보지 못한다. 그래서 로그인 뒤 주소창의 주소가 아니라 도서관이 공개 안내하는 접두를 넣어야 한다.
- 받지 않는 형식이 있다. `http` 주소와, 기사 주소 자리 없이 경로 끝에 주소를 바로 붙이는 형식이다. 지원하려면 별도 설계가 필요하다(http 허용 여부, 경로형 접두, 주소 인코딩).

## 아직 없는 것
- Web of Science(Research Assistant 포함)나 Nexus Connect에 자동으로 붙는 전용 연결은 구현돼 있지 않다.
- 기관 MCP endpoint나 labhq 같은 사용자 정의 client가 지원되는지는 확인되지 않았다.
- 위의 일반 기관 경로(프록시·기관 세션·PI PDF 요청)는 이와 별개로 구현돼 있고, 같은 동의 조건을 따른다.

## Clarivate Nexus Connect 공개 안내 (2026-10-22 확인)
- 출처(Clarivate 공식 페이지):
  - https://clarivate.com/academia-government/nexus-connect/
  - https://discover.clarivate.com/academic-ai_nexus-connect-early-access
- 확인한 내용:
  - "MCP-compliant connector"이며, 제공 대상은 "Institutional (enterprise) chat agents and customized library agents"다.
  - 기관이 관리하고, 기관 구독(entitlement) 범위를 따른다.
  - 초기 범위는 Primo/Central Discovery Index 검색과 Alma/Primo 도서관 서비스다.
  - 예시 플랫폼: ChatGPT Edu, Microsoft 365 Copilot, Google AI Pro for Education, Claude for Education.
- 공개 페이지에 없는 것: 연결 endpoint, API 문서, 인증 방식, CLI나 사용자 정의 client 지원 여부.
- 발표 기사 페이지는 확인할 때 403이 나와 내용을 보지 못했다.

## 확인되지 않은 것
- 특정 기관의 도입·계약 여부와 연결 주소.
- 브라우저용 구독이 API·MCP 사용 권한을 포함하는지. 둘을 같은 것으로 보지 않는다.
- 기관이 허용하는 client와 사용 조건. 기관 도서관에 확인해야 한다.
