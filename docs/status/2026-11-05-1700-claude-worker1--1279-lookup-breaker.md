# #1279 공개 조회 호스트 차단기 — 단계 보고

- 결론: 같은 호스트가 연속으로 5xx·429를 돌려주면 그 호스트로 보내지 않습니다. 조회 몫 계산(실패·재시도·redirect 포함, 보내기 직전 1회)은 바꾸지 않았고, 차단된 시도는 보내지도 세지도 않습니다.
- 기준과 근거: 5xx는 3회 연속, 429는 429·5xx 합쳐 5회 연속(annot의 `MAX_ATTEMPTS`). 차단 후 300초가 지나면 한 번만 다시 보내 보고, 성공하면 풀립니다. #910 8차에서 Europe PMC는 503 15회 연속(본문 0편), OpenAlex는 429 4회 뒤 200이었습니다. 429를 5회로 둔 것은 이 8차처럼 한 조회 안에서 풀리는 제한을 자르지 않기 위해서입니다. 이 기준이면 8차의 Europe PMC 15회가 3회로 줄어 몫 12회가 다른 출처에 남습니다.
- `Retry-After`: 한 조회 안의 재시도는 기존 `annot.Http`가 이미 지킵니다(최대 120초). 차단 기준에는 쓰지 않았습니다. 서버가 늦게 풀리는 값을 주는 경우도 300초 뒤 한 번 다시 시도하는 것으로 충분하다고 봤습니다(미검증).
- 바뀐 것: `labhq/runner/lookup_admission.py`(`breaker_wait`, `admit`의 `host_paused`, `paused_hosts`, 사용량 문단에 차단 호스트), `labhq/tools/annot_mcp.py`(도구 프로세스의 호스트별 최근 응답과 `LookupHostPaused`), `tests/test_lookup_host_breaker_1279.py`.
- 두 겹: 한 도구 호출 안의 재시도는 응답이 호출 끝에야 파일에 기록되므로 도구 프로세스가 직접 막고, 다른 호출·단계·직원은 요청 단위 파일의 기록으로 막습니다.
- 실행: `tests/test_lookup_host_breaker_1279.py` 13건과 기존 조회 시험 245 passed, 3 skipped(로컬 Windows).
- 미확인·한계: 실제 Europe PMC·OpenAlex 호출은 하지 않았습니다. 상한이 꺼져 있으면(`request_lookup_limit` 미설정) 차단기도 없습니다. 계획 경고(CP1 카드)에 차단 사실을 싣는 것은 이 claim 밖이라 하지 않았습니다. 차단 사실은 도구 오류 문구와 단계 prompt의 사용량 문단에 남습니다.
