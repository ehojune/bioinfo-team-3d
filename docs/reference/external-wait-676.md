# 외부 조회 시간 상한 #676

- 기존 ChIP-Atlas는 호출별 `wait_s`만 제한하고 시간 초과 뒤 재호출을 권했다. 단계 전체 상한·대기 알림·실패 산출 기록은 없었다.
- 일반 `labhq_annot` API 두 경로(`_run`, `_run_literature`)는 단계 첫 조회부터 1,200초의 wall-clock 기한을 공유한다. `.labhq/external_lookup_budget.json`으로 MCP 재시작·서비스 변경 뒤에도 기한을 유지한다. 조회 사이 계산 시간도 이 기한에 포함된다.
- 시작·60초 간격·종료를 기존 task 인증 `/event`의 `agent.log`로 보내고 `outputs/external_lookup_status.jsonl`에 상태·경과·상한만 쓴다. 입력·응답·토큰은 기록하지 않는다. 기록 경로의 기존 owned guard를 유지한다.
- 시간 초과는 MCP 오류이며 NOT_EVIDENCE 문구를 유지한다. ChIP-Atlas 자체의 짧은 상한도 timeout으로 기록한다. 직원 규칙은 자동 재시도를 멈추고 남은 작업과 빠진 조회 보고를 요구한다. checkpoint·게시·승인 판정은 바꾸지 않는다.
- `key_fulltext`의 fulltext(OA·OpenAlex·Unpaywall 포함), 기관 요청 간격 대기와 fetch도 같은 예산을 쓴다. 명시적인 PI PDF 승인 대기 시간만 제외하며, 승인 전 사용한 예산은 이후 서비스 조회에도 유지한다. 승인·기관 접근 허용 조건은 바꾸지 않는다. 직원이 직접 작성한 스크립트와 타사 MCP는 이 기한의 강제 대상이 아니며, 스크립트에는 별도 wall-clock timeout 작성 규칙을 준다. 전체 #676을 이미 해결했다고 닫지 않는다.
- 검증: 새 coroutine/owned-file/mock HTTP 및 기존 ChIP-Atlas MockTransport만 21 passed, 26 deselected. 기한 재설정 방지·대기 알림·오류/부재 구분·취소 전파·owned guard·인간 승인 경로 제외를 확인했다. P1 후속은 수정 전 자동 조회 3경로에서 실패를 재현했고, 수정 후 관련 mock/static 40건이 통과했다. 승인 거절·만료·취소 뒤 예산 보존과 기존 PDF 접근 검사를 포함한다. 실제 서비스·프로세스/proc_tree·기관·HPC·원자료·운영·PI 인스턴스는 실행하지 않았다.
- manual 163,011→163,275자(+264, LF Unicode). 상세 근거는 lookup-only로 보관한다.
- PR #776 P1 후속 manual 163,275→163,348자(LF Unicode). 실제 기관·브라우저·프로세스는 실행하지 않았다.
- P1 `4317534007`: 기관 fetch의 executor thread를 async subprocess 대기로 바꿨다. 실행 중·시작 도중 취소 시 소유한 handle만 종료·회수하고, 임시 PDF는 정상 완료·형식 검사 뒤 목적 경로에 반영한다. PID/tree 스캔은 없다. AlphaGenome은 외부 취소도 기존 `_AG_STUCK`에 집계하여 상한에서 새 호출을 거부하고 완료된 thread는 제외한다. 원격 SDK 호출 자체의 강제 취소는 보장하지 않는다.
- 검증: 수정 전 취소 회귀 4건 실패, 수정 후 fake process/thread·기존 coroutine·PDF gate mock/static 43 passed. 실제 browser/process/proc_tree/SDK·기관·운영 실행은 없다. manual 163,348→163,450자. P2 동시 승인 예산은 supervisor 판단 대상으로 남긴다.

## 조회 상태 기록의 결과·보고 연결

- 이전에는 `outputs/external_lookup_status.jsonl`을 직원 프롬프트와 MCP 안내만 가리켰다. runner는 이 파일을 다시 읽지 않았고, 직원이 원장에 행을 쓰지 않으면 빠진 조회가 보고에 드러나지 않았다.
- runner는 단계 turn이 끝난 뒤 `output_checks.external_lookups()`로 그 workspace의 파일을 읽어 `TaskResult.external_lookups`에 넣는다. 도구 이름, 결과별 건수(ok/timeout/failed/cancelled), 끝난 기록 없음 여부, 단계 조회 경과·상한 초, 잘못된 줄·중복 줄·초과 도구 수, 잘림 여부, 읽지 못함 여부만 담는다. 파일이 없으면 필드가 생략되어 기존 결과 형태를 유지한다. 다른 runner 판본이 보낸 잘못된 값은 요약만 버리고 결과는 유지한다.
- 읽기 경계는 다음과 같다.
  - link인 파일이나 link인 상위 폴더는 읽지 않는다(`read_owned`와 같은 판정, O_NOFOLLOW). 그 경우와 일반 파일이 아닌 경우, 읽기 오류는 `unreadable`로 남겨 파일 없음과 구분하고 보고에도 표시한다. `outputs/`나 파일이 아예 없으면 기록 없음이다.
  - 마지막 1,000,000바이트·5,000줄만 읽는다. 한 줄은 4,096자, 도구는 50개까지 받는다.
  - 도구 이름은 `^[a-z][a-z0-9_]{0,47}$`, 상태는 다섯 값만 받는다. 초는 bool이 아닌 유한 수치여야 하고 0 이상 366일 이하만 받으며(같은 workspace를 몇 주 다시 써도 단계 예산을 읽는다), 상한은 0을 넘어야 한다.
  - 같은 줄이 다시 나오면 한 번만 센다. 각 줄의 자유 `text`는 다시 읽지 않는다.
  - writer(`run_lookup`)는 호출마다 무작위 12자리 `lookup` id를 붙인다. 입력은 담지 않는다. 마지막 기록이 `waiting`인 호출은 같은 도구의 뒤 호출이 성공해도 끝난 기록 없음으로 남는다. id 형식이 틀린 줄은 잘못된 줄이다. id가 없는 이전 기록은 도구별로 읽어, 그 도구의 다음 결과가 대기를 끝낸다.
- 이 기록은 직원 workspace에 남은 기록이다. 직원도 이 파일에 쓸 수 있으므로 독립 tool 호출이나 본문 독해의 증명이 아니다. 그래서 증거 칸을 채우지 않고 `ok`·`missing_outputs`·`optional_outputs`·CP2 판정도 바꾸지 않는다. 필수 산출물이 빠진 단계는 그대로 실패하고 뒤 단계는 그대로 건너뛴다.
- 표시 경로는 다음과 같다.
  - runner는 빠졌거나 이상한 조회를 `agent.log` warn 한 줄로 남겨 작업판 경고로 보낸다.
  - `quality_lines()`의 단계별 한 줄은 일반 요청의 `## 보고서 경고`, 성공한 연구 단계의 CP2 카드 결과 한계, 종합 prompt의 gaps, 최종 보고서 `## 한계`로 이어진다. CP2 전에 실패한 연구 단계는 단계 실패와 작업판 warn만 남고, 그 단계의 조회 줄은 한계에 나오지 않는다(cso.py:4968-4976).
  - 보고 문구는 검증된 이름·건수·초로만 만들고, 도구 이름은 code span으로 감싼다. 원장에 대응하는 failed/unavailable 행이 없어도 표시한다.
- 범위 밖은 다음과 같다. 직원이 직접 쓴 스크립트·curl과 타사 MCP는 여전히 이 공통 상한·기록의 강제 대상이 아니다. optional_outputs의 의미 확장(결론과 무관한 조회 표를 선택 산출로 두는 것)과 공통 조회 CLI는 하지 않았다. 과거 95분 요청 재실행, 실제 작업판 표시, 실요청에서의 뒤 단계 진행은 확인하지 않았다. #676은 열어 둔다.
- 검증은 다음과 같다.
  - 동기 순수 검사 `tests/test_external_lookup_record_676.py`: 로컬 26 passed, 1 skipped. Windows에서 symlink 권한이 없어 link 검사는 CI Linux에서 확인한다.
  - runner fake CLI와 연구 CP2·한계 흐름 `tests/test_external_lookup_flow_676.py`: 작성만 했고 CI에서 실행한다.
  - 독립 검토 뒤 보정(검토 대상 `b8d452f`): 두 테스트를 Windows inventory(`config/ci/windows-boundary-v1.json` known_tests)에 등록했고, 패치노트에 `pr: null`을 넣었다. 호출 id로 뒤 성공이 앞 미완료를 지우지 않게 했고, 읽지 못함을 파일 없음과 구분했으며, 초 상한을 366일로 넓혔다. 동기 검사는 69 passed, 1 skipped, 2 deselected였다. 정적 CI 정책 검사는 `validate_profile`과 `include_windows` 판정, `tests/test_ci_parallel_policy.py`·`test_ci_windows10m.py`·`test_ci_cohort823.py` 199 passed였다. `notes_index.py --validate`도 통과했다. `tests/test_external_wait.py`의 key 검사(async)는 CI에서 확인한다.
  - 실제 제품, runner process, MCP, 네트워크, WSL, HPC는 실행하지 않았다.
