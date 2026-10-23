## #891 출처 실시간 확인 조회도 요청 HTTP 상한에 계수 (base 01b100ad)

- 바꾼 것: `LiveSourceResolver`(`labhq/evidence/verify.py`)가 매 HTTP 요청(재시도 포함) 전에 `LookupAdmission.admit`을 부른다. `annot.request_lookup_limit`과 같은 runner 상태 폴더 SQLite 파일·같은 요청 단위 카운터를 쓴다(`admission_for`). 거절되면 보내지 않고 `LookupFailed("lookup_limit")` → `requires_verification`(확인 못 함), `not_found`와 구별. 주 조회가 통과한 뒤 보강 요청이 거절되면 found 기록을 유지하고 `enrichment_failures`에 남는다. gateway(`cso.py`)와 `labhq verify`(`audit.py`) 두 곳에 연결. 상한이 없으면 세지 않고 파일도 만들지 않는다. manual 갱신.
- 검사: 새 test 6건(수정 전 수집 실패), 관련 348 passed·2 skipped, 조회 상한 test 44 passed.
- 한계: 같은 설정을 쓰는 PC에서만 runner와 한 파일을 센다(runner가 다른 PC면 각자). `live_source_check` 기본값(false)과 ② 설정은 바꾸지 않았다. 실제 Crossref/NCBI 호출·gateway 실행은 확인하지 않았다(fixture 전송으로만 검증).
