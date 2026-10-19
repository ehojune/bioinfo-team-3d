Fixes #748

공개용 소스의 문법 검사와 실제 실행을 구분하는 receipt를 추가했습니다. Python compile·R parse·bash -n, 코드/lock hash, 도구 판본·상대 cwd·변수 이름·명령·예상 출력·시각·결과를 기록합니다. 기본 `not_rerun`, 입력 누락 `blocked_input`, 없는 도구 `unavailable`을 유지합니다.

- 정리본의 `run/reproduction_receipt.json`, 공통 ReportPackage, bench run/score/comparison에 연결했습니다. 실패·도구 없음·미검사를 숨기지 않고 Python 문법 실패의 기존 게시 차단도 유지합니다.
- 허용된 작은 fixture의 명시적 실행은 exit 0 + 선언 출력 존재만 검증합니다. 원자료·다운로드·기관·HPC·분석 재실행·실제 게시를 실행하지 않았습니다. #754의 CLI/storage rebuild는 별도 범위입니다.
- 관련 pytest 168 passed, 2 skipped(중복 제외). Rscript가 없어 실제 R parse 두 경우는 skip했으며 명령·도구 없음 경로는 별도 검증했습니다. public·diff·Python 3.10 AST 검사 통과.
- manual 해당 절·HANDOFF 큐·reference를 갱신했습니다. HANDOFF 3,617→3,780자, manual 161,473→161,875자.

## 남은 지적

- Rscript 실측·실데이터 재실행·수치 동등성 검증은 미실행입니다. fixture의 실행 PASS를 과학 결과 재현 PASS로 해석하지 않습니다.
- CI 조회 후 대기·봇 호출·병합·배포는 총괄 driver가 맡습니다.
