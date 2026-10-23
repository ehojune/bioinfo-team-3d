## #676 외부 응답 대기 경과 표시·상한 경로 점검 (base b09b6994)

- 바꾼 것: 공개 조회가 기다리는 동안의 진행 알림(`agent.log`의 `external_lookup`, 60초 간격)을 gateway가 task 활동 `external_wait`로 남기고(끝·시간 초과·실패면 지움), 웹 직원 카드가 `외부 응답 대기 n분째 / 상한 20분`을, `labhq status`의 단계 줄이 같은 문구를 보인다(`step_waits`, 마지막 알림 뒤 경과 시간을 더하고 상한을 넘지 않게 표시). manual의 20분 상한 절 갱신.
- 경로 점검: `labhq_annot`의 모든 `@server.tool`(variant·regulatory·문헌·`key_fulltext`·AlphaGenome)은 `_run`/`_run_literature`/`_run_key_fulltext`를 거쳐 `run_lookup`(20분)에 닿는다. 빠진 경로는 없어 연결한 것은 없고, 우회하는 새 도구가 생기면 실패하는 AST test를 추가했다. gateway의 출처 실시간 확인은 별도 상한(요청 10초·보고서 60초, 꺼짐이 기본)이다. HPC job 도구와 임의 셸·curl·타사 MCP는 이번 범위 밖.
- 검사: 새 test 4건·웹 1건, 수정 전 코드로 수집 실패·웹 1건 실패, 수정 후 관련 229 passed·1 skipped·web fail 0.
- 한계: 실제 95분 사례와 실제 UI·`labhq status` 화면 재현은 하지 않았다. 표시는 60초 간격 알림에 의존한다.
