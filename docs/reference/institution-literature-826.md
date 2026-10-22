# 기관 문헌 연결의 공개 확인 범위 — #826

`labhq init`의 기관 문헌 질문(#850)이 다루는 범위와, 기관 연결 서비스에 대해 공개 자료로 확인한 범위를 적는다. 로그인, 양식 제출, 기관 내부 페이지, 실제 연결 시험은 하지 않았다.

## labhq가 하는 것
- `labhq init`은 기관 이름, 도서관 프록시 주소, 이용 중인 문헌 서비스, Unpaywall 연락 email을 묻는다. 저장하는 것은 `literature.institution_proxy`와 `annot.unpaywall_email`뿐이고 기관 세션은 꺼 둔다.
- 프록시는 https이고 기사 주소 자리(`url=`/`{url}`)가 있는 접두만 받는다. `http` 주소와 자리 없이 경로에 바로 붙이는 형식은 아직 받지 않는다. 지원하려면 별도 설계가 필요하다(http 허용 여부, 경로형 접두, 주소 인코딩).
- Web of Science나 Nexus Connect에 기관 계정으로 연결하는 기능은 없다. 기관 이름과 서비스는 저장하지 않는다.

## Clarivate Nexus Connect 공개 안내 (2026-10-22 확인)
- 출처: https://clarivate.com/academia-government/nexus-connect/ , https://discover.clarivate.com/academic-ai_nexus-connect-early-access
- 확인한 내용:
  - "MCP-compliant connector"이며 제공 대상은 "Institutional (enterprise) chat agents and customized library agents"다.
  - 기관이 관리하고 기관의 구독(entitlement) 범위를 따른다. 초기 범위는 Primo/Central Discovery Index 검색과 Alma/Primo 도서관 서비스다.
  - 예시 플랫폼: ChatGPT Edu, Microsoft 365 Copilot, Google AI Pro for Education, Claude for Education.
- 공개 페이지에 없는 것: 연결 endpoint, API 문서, 인증 방식, CLI나 사용자 정의 client 지원 여부.
- 발표 기사 페이지는 확인 시 403이라 내용을 보지 못했다.

## 확인되지 않은 것
- 특정 기관의 도입·계약 여부와 연결 주소.
- 브라우저용 구독이 API·MCP 사용 권한을 포함하는지. 둘은 같은 것으로 보지 않는다.
- labhq CLI가 이런 연결을 쓸 수 있는지. 기관 도서관에 사용 조건과 허용 client를 확인해야 한다.
- 연결 정보가 생기기 전에는 기존 경로(합법 OA 조회 → 켜 둔 기관 세션 → PI PDF 요청 카드)를 쓴다.
