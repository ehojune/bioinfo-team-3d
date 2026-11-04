Closes #954

15차 선례 조사에서 `fulltext`가 4번 모두 실패한 원인을 고쳤습니다.

- **원인:** `labhq/tools/annot.py`의 `request(response_limit=…)` 경로가 `aiter_bytes()`로 이미 풀린 바이트를 모은 뒤 원래 헤더(`Content-Encoding` 포함)를 붙여 `httpx.Response`를 다시 만들어, httpx가 한 번 더 풀다 `DecodingError`가 났습니다(`TransportError`가 아니라 잡히지 않아 같은 호출의 모든 ID가 함께 실패).
- **수정 1:** 다시 만드는 응답에서 `content-encoding`·`content-length`·`transfer-encoding`을 뺍니다.
- **수정 2:** `fulltext`는 ID 하나의 조회·본문 저장 예외를 그 ID의 `error`(`예외 종류: 설명`)로만 남기고 다른 ID는 정상으로 둡니다. 모든 ID가 실패할 때만 호출이 실패합니다.
- **테스트(`tests/test_annot_literature.py`, 수정 전 3건 모두 실패):** gzip `Content-Encoding` 응답을 합법 OA 사본으로 받아 본문 저장, Http 계층이 헤더를 뺌, 한 ID가 `DecodingError`여도 다른 ID는 `full_text`. literature·annot 묶음 117건 통과(로컬, Windows, Python 3.12).
- **문서:** `docs/manual.md` 본문 조회 항목.
- **미검증:** 실제 NEJM 논문 재조회, 15차 실운전.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
