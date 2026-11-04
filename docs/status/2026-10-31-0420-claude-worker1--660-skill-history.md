# #660 2단계: 요청 사이 사용 이력

- 결론: 논문 스킬이 다른 요청에서 선택된 기록을 세 곳에서 본다. `labhq talent --history`(gateway 없이 store를 읽기 전용으로), `GET /api/paper-skills`(인증), 웹 여우 패널 카드의 `이전 사용` 칸. 모두 최근 요청 500건 기준이며 그보다 오래된 요청이 있으면 그렇게 밝힌다.
- 바꾼 것: `labhq/recruit/paper_skills.py`(`history_rows`·`skill_history`·`with_previous_uses`), `labhq/gateway/server.py`(API, 요청 상세의 `previous_uses`와 `history`), `labhq/cli.py`(`--history`, store 읽기를 `_paper_skill_store_rows`로 분리), `labhq/web/ui/literature.js`, 시험 `tests/test_contract_provenance.py`·`tests/web_contract_provenance.cjs`, manual 한·영.
- 사용 이력의 뜻: 그 요청이 스킬을 골랐고 어느 단계에 붙었는지, 결과가 참조했는지다. 결과 참조는 도구 호출이나 효용의 증거가 아니다.
- 한계: store에 남은 요청만 센다(삭제 정책 미확인). 요청 상세를 열 때마다 최근 500건을 훑는다. 웹 렌더는 시험에서 가짜 DOM으로만 확인했다.
- 미검증: 실제 gateway와 브라우저에서의 카드, 많은 요청이 쌓인 store의 응답 시간.
